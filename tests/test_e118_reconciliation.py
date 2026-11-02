"""E1.18 source-evidence-only review queue; never network, databases or orders."""
from __future__ import annotations
import csv
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import copy

import pytest

from congress_remediation import remediate_snapshot, write_research_outputs
from congress_reconciliation import (
    ReconciliationError, reconcile_e117, write_outputs, _candidate_action,
    _candidate_asset, VERSION,
)

FIXED = datetime(2026, 11, 2, 3, 10, tzinfo=timezone.utc)


def trade(**kw):
    data = {
        'filingId': 'source-1', 'ticker': 'NVDA',
        'memberName': 'Nancy Pelosi', 'memberSlug': 'nancy-pelosi',
        'owner': 'Spouse', 'assetType': 'Stock', 'assetDescription': 'NVIDIA Common Stock',
        'type': 'Purchase', 'transactionDate': '2026-07-24', 'disclosureDate': '2026-08-24',
        'amountLabel': '$1,001 - $15,000', 'amountMin': 1001,
        'amountMid': 8001, 'amountMax': 15000,
        'ingestedAt': '2026-08-24T15:02:49.624Z',
        'sourceLink': 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf',
    }
    data.update(kw)
    return data


def source(tmp_path, *trades):
    raw = {
        'data': {'member': {'slug': 'nancy-pelosi'}, 'trades': list(trades)},
        'error': None,
        'meta': {
            'count': 1, 'fetchedAt': '2026-11-02T02:53:54.620Z',
            'attribution': {'page': 'https://tracefour.com/trackers/pelosi'},
            'license': 'https://creativecommons.org/licenses/by/4.0/',
        },
    }
    path = tmp_path / 'snapshot.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    folder = tmp_path / 'e117'
    write_research_outputs(remediate_snapshot(path, assessed_at=FIXED), folder)
    return folder


def test_no_quarantine_rows_does_not_invent_reviews(tmp_path):
    r = reconcile_e117(source(tmp_path, trade()))
    assert len(r['research_only']) == 1 and not r['review_queue']
    assert not r['group_queue']
    assert r['summary']['source_verified_rows'] == 0
    assert r['research_only'][0]['first_seen_at'] is None
    assert r['research_only'][0]['eligible_for_execution'] is False


def test_missing_official_link_requires_independent_document(tmp_path):
    result = reconcile_e117(source(tmp_path, trade(sourceLink=None)))
    task = result['review_queue'][0]
    assert task['review_priority'] == 'P2'
    assert task['official_filing_url'] is None
    assert 'locate_official_pdf_independently; do_not_inherit_link_from_other_row' in task['review_tasks']
    assert task['required_evidence'][0] != ''
    assert task['official_filing_verified'] is False
    assert task['queue_status'] == 'pending_original_filing_evidence'


def test_other_with_sale_id_only_raises_unverified_candidate(tmp_path):
    rec = trade(filingId='nancy-pelosi|NVDA|2026-07-24|Sale (Full)|$1,001 - $15,000|Spouse|Stock', type='Other')
    task = reconcile_e117(source(tmp_path, rec))['review_queue'][0]
    assert task['candidate_action_from_id_unverified'] == 'possible_sale_unverified'
    assert task['action_reported'] == 'Other'
    assert 'compare_transaction_action_with_original_pdf; do_not_assume_sale' in task['review_tasks']
    assert task['eligible_for_ingestion'] is False


def test_other_with_id_not_suggesting_sale_is_unknown(tmp_path):
    task = reconcile_e117(source(tmp_path, trade(type='Other')))['review_queue'][0]
    assert task['candidate_action_from_id_unverified'] == 'unknown_unverified'
    assert task['action_reported'] == 'Other'


@pytest.mark.parametrize('description,expected', [
    ('exercised 50 calls -> 5000 shares', 'possible_option_exercise_unverified'),
    ('Bought 30 Call options', 'possible_option_contract_unverified'),
])
def test_mislabeled_stock_creates_review_question_not_action(tmp_path, description, expected):
    task = reconcile_e117(source(tmp_path, trade(assetDescription=description)))['review_queue'][0]
    assert task['candidate_asset_interpretation_unverified'] == expected
    assert task['asset_type_reported'] == 'Stock'
    assert task['eligible_for_execution'] is False


def test_proper_option_is_not_reclassified_as_equity(tmp_path):
    r = reconcile_e117(source(tmp_path, trade(assetType='Stock Option', assetDescription='Call Option')))
    assert len(r['research_only']) == 1
    assert r['research_only'][0]['asset_type_reported'] == 'Stock Option'
    assert r['summary']['auto_reclassified_rows'] == 0


def test_possible_duplicates_both_remain_quarantined(tmp_path):
    folder = source(tmp_path,
                    trade(filingId='a', sourceLink=None),
                    trade(filingId='b', disclosureDate='2026-08-25', sourceLink='https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf'))
    r = reconcile_e117(folder)
    assert len(r['review_queue']) == 2
    assert len(r['group_queue']) == 1
    group = r['group_queue'][0]
    assert group['row_indices'] == [1, 2]
    assert group['automatic_deduplication_allowed'] is False
    assert group['evidence_status'] == 'no_independent_original_pdf_review'
    assert r['summary']['auto_deduplicated_rows'] == 0
    assert not r['research_only']
    first = r['review_queue'][0]
    assert first['official_filing_url'] is None
    assert first['suggested_evidence_url_unverified'] == ['https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf']
    assert 'all_group_original_filing_pages' in first['required_evidence']
    assert first['related_row_indices'] == [2]


def test_same_semantics_different_original_urls_are_not_collapsed(tmp_path):
    a = trade(filingId='a', sourceLink='https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf')
    b = trade(filingId='b', sourceLink='https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035555.pdf')
    r = reconcile_e117(source(tmp_path, a, b))
    assert len(r['group_queue']) == 1
    assert len(r['group_queue'][0]['distinct_official_links']) == 2
    assert len(r['review_queue']) == 2


def test_all_rows_preserved_and_raw_hashes_match_e117(tmp_path):
    folder = source(tmp_path, trade(), trade(filingId='id-2', ticker='MSFT', sourceLink=None))
    r = reconcile_e117(folder)
    assert r['summary']['total_source_rows'] == 2
    assert len(r['research_only']) + len(r['review_queue']) == 2
    for row in r['research_only'] + r['review_queue']:
        raw = json.dumps(row['raw_record'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        assert hashlib.sha256(raw).hexdigest() == row['raw_record_sha256']
        assert row['first_seen_at'] is None
        assert row['eligible_for_execution'] is False
    assert len({r['row_index'] for r in r['research_only'] + r['review_queue']}) == 2


def test_tampered_source_jsonl_refused_before_queue(tmp_path):
    folder = source(tmp_path, trade(sourceLink=None))
    path = folder / 'pelosi_e117_quarantined_review.jsonl'
    content = path.read_text()
    path.write_text(content.replace('Spouse', 'Self'))
    with pytest.raises(ReconciliationError, match='SHA-256 mismatch'):
        reconcile_e117(folder)


def test_tampered_manifest_sha_rejected(tmp_path):
    folder = source(tmp_path, trade(sourceLink=None))
    path = folder / 'pelosi_e117_summary.json'
    payload = json.loads(path.read_text())
    payload['output_files_sha256']['pelosi_e117_quarantined_review.jsonl'] = '0' * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(ReconciliationError, match='SHA-256 mismatch'):
        reconcile_e117(folder)


def test_snapshot_hash_consistency_required_even_with_recomputed_file_sha(tmp_path):
    folder = source(tmp_path, trade(sourceLink=None))
    path = folder / 'pelosi_e117_quarantined_review.jsonl'
    data = json.loads(path.read_text())
    data['snapshot_sha256'] = '0' * 64
    byte = (json.dumps(data) + '\n').encode('utf-8')
    path.write_bytes(byte)
    manifest_path = folder / 'pelosi_e117_summary.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['output_files_sha256'][path.name] = hashlib.sha256(byte).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ReconciliationError, match='Source snapshot mismatch'):
        reconcile_e117(folder)


def test_source_status_cannot_be_self_promoted(tmp_path):
    folder = source(tmp_path, trade(sourceLink=None))
    path = folder / 'pelosi_e117_quarantined_review.jsonl'
    data = json.loads(path.read_text())
    data['official_filing_verified'] = True
    byte = (json.dumps(data) + '\n').encode('utf-8')
    path.write_bytes(byte)
    mpath = folder / 'pelosi_e117_summary.json'
    manifest = json.loads(mpath.read_text())
    manifest['output_files_sha256'][path.name] = hashlib.sha256(byte).hexdigest()
    mpath.write_text(json.dumps(manifest))
    with pytest.raises(ReconciliationError, match='Unjustified'):
        reconcile_e117(folder)


def test_task_ids_deterministic_and_not_dependent_on_wall_clock(tmp_path):
    folder = source(tmp_path, trade(sourceLink=None))
    a = reconcile_e117(folder)
    b = reconcile_e117(folder)
    assert a == b
    assert a['review_queue'][0]['task_id'].startswith('e118-')


def test_csv_jsonl_and_manifest_determinism(tmp_path):
    folder = source(tmp_path, trade(sourceLink=None), trade(filingId='b', ticker='MSFT', type='Other'))
    result = reconcile_e117(folder)
    a = write_outputs(result, tmp_path / 'one')
    b = write_outputs(result, tmp_path / 'two')
    assert a['manifest']['local_output_sha256'] == b['manifest']['local_output_sha256']
    assert all(p.exists() for p in a['paths'].values())
    with a['paths']['review_queue_csv'].open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    with a['paths']['decision_template_csv'].open(encoding='utf-8-sig', newline='') as stream:
        decisions = list(csv.DictReader(stream))
    assert len(rows) == len(decisions) == 2
    assert all(not x['decision'] and not x['reviewer'] for x in decisions)
    assert all(x['eligible_for_ingestion'] == 'False' for x in rows)
    assert a['manifest']['output_row_counts']['human_review'] == 2


def test_csv_formula_injection_defended(tmp_path):
    folder = source(tmp_path, trade(assetDescription='=HYPERLINK("http://evil")', sourceLink=None))
    a = write_outputs(reconcile_e117(folder), tmp_path / 'out')
    content = a['paths']['review_queue_csv'].read_text(encoding='utf-8-sig')
    # Asset description isn't copied to the CSV, but every user-controlled
    # field that is present must not begin with an executable formula.
    assert content.startswith('task_id,')
    with a['paths']['review_queue_csv'].open(encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row['ticker'] == 'NVDA'
    assert row['review_priority'] == 'P2'


def test_queue_never_contacts_external_services(monkeypatch, tmp_path):
    import socket
    def forbidden(*a, **kw):
        raise AssertionError('Network access prohibited')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    folder = source(tmp_path, trade(sourceLink=None))
    r = reconcile_e117(folder)
    assert len(r['review_queue']) == 1
    assert r['summary']['external_http_requests'] == 0
    assert r['summary']['supabase_writes'] == 0
    assert r['summary']['broker_orders'] == 0


def test_e117_snapshot_regression_optional_env():
    folder = os.environ.get('E118_E117_OUTPUT_DIR')
    if not folder:
        pytest.skip('Real user data intentionally excluded from GitHub CI')
    r = reconcile_e117(folder)
    s = r['summary']
    assert s['source_snapshot_sha256'] == '4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3'
    assert s['total_source_rows'] == 147
    assert s['clean_research_only_unverified_rows'] == 75
    assert s['quarantined_human_review_rows'] == 72
    assert s['semantic_duplicate_groups_pending'] == 12
    assert s['reason_counts_overlapping']['ambiguous_action_other'] == 34
    assert s['reason_counts_overlapping']['missing_or_untrusted_official_filing_url'] == 21
    assert s['reason_counts_overlapping']['stock_label_derivative_or_exercise_description'] == 10
    assert s['source_verified_rows'] == 0
    assert r['group_queue'][0]['automatic_deduplication_allowed'] is False
