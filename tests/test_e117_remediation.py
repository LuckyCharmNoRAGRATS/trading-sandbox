"""Offline E1.17 research quarantine tests; no network/database/broker imports."""
from __future__ import annotations
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from congress_remediation import (
    RemediationError, remediate_snapshot, write_research_outputs,
    _csv_safe, _event_id, _sha_obj,
)

FIXED_AT = datetime(2026, 11, 2, 3, 10, tzinfo=timezone.utc)
REAL_SHA = '4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3'


def trade(**kw):
    t = {
        'filingId': 'id-1', 'ticker': 'NVDA', 'memberName': 'Nancy Pelosi',
        'memberSlug': 'nancy-pelosi', 'owner': 'Spouse', 'assetType': 'Stock',
        'assetDescription': 'NVIDIA Common Stock', 'type': 'Purchase',
        'transactionDate': '2026-07-24', 'disclosureDate': '2026-08-24',
        'amountLabel': '$1,001 - $15,000', 'amountMin': 1001,
        'amountMid': 8001, 'amountMax': 15000,
        'ingestedAt': '2026-08-24T15:02:49.624Z',
        'sourceLink': 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf',
    }
    t.update(kw)
    return t


def envelope(*trades):
    return {
        'data': {'member': {'slug': 'nancy-pelosi', 'lifetimeFilings': 126},
                 'trades': list(trades)},
        'error': None,
        'meta': {'count': 1, 'fetchedAt': '2026-11-02T02:53:54.620Z',
                 'attribution': {'page': 'https://tracefour.com/trackers/pelosi'},
                 'license': 'https://creativecommons.org/licenses/by/4.0/'},
    }


def make(tmp_path, *trades):
    p = tmp_path / 'test-snapshot.json'
    p.write_text(json.dumps(envelope(*trades)), encoding='utf-8')
    return p


def test_clean_record_research_only_preserves_raw_provenance(tmp_path):
    p = make(tmp_path, trade())
    result = remediate_snapshot(p, assessed_at=FIXED_AT)
    assert len(result['clean']) == 1 and not result['quarantine']
    rec = result['clean'][0]
    assert rec['quality_status'] == 'structurally_clean_source_unverified'
    assert rec['owner_type'] == 'Spouse'
    assert rec['first_seen_at'] is None
    assert rec['official_filing_verified'] is False
    assert rec['eligible_for_ingestion'] is False
    assert rec['eligible_for_execution'] is False
    assert rec['raw_record'] == trade()
    assert rec['raw_record_sha256'] == _sha_obj(trade())
    assert rec['research_event_id'] == _event_id('id-1')
    assert rec['snapshot_sha256'] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert result['summary']['provider_meta_count_is_member_count'] == 1


def test_valid_stock_option_is_preserved_as_option_not_equity(tmp_path):
    result = remediate_snapshot(make(tmp_path, trade(assetType='Stock Option', assetDescription='Call Option')))
    assert len(result['clean']) == 1
    assert result['clean'][0]['asset_type_reported'] == 'Stock Option'
    assert result['clean'][0]['eligible_for_execution'] is False


def test_missing_link_quarantined_without_guessing_pdf(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(sourceLink=None)))['quarantine'][0]
    assert 'missing_or_untrusted_official_filing_url' in rec['reason_codes']
    assert rec['official_filing_url'] is None


def test_wrong_hostname_even_with_path_is_quarantined(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(sourceLink='https://example.com/public_disc/ptr-pdfs/2026/20035143.pdf')))['quarantine'][0]
    assert 'missing_or_untrusted_official_filing_url' in rec['reason_codes']


def test_other_with_sale_identifier_is_not_reinterpreted_as_sale(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(filingId='nancy-pelosi|NVDA|2026-07-24|Sale (Full)|$1,001 - $15,000|Spouse|Stock', type='Other')))['quarantine'][0]
    assert 'ambiguous_action_other' in rec['reason_codes']
    assert 'source_id_suggests_sale_but_type_other' in rec['reason_codes']
    assert rec['action_reported'] == 'Other'


def test_stock_label_call_description_quarantined_not_autocorrected(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(assetDescription='exercised 50 calls -> 5000 sh')))['quarantine'][0]
    assert 'stock_label_derivative_or_exercise_description' in rec['reason_codes']
    assert rec['asset_type_reported'] == 'Stock'


def test_semantic_duplicates_both_quarantined_without_dropping_rows(tmp_path):
    p = make(tmp_path, trade(filingId='source-a'), trade(filingId='source-b', disclosureDate='2026-08-25'))
    result = remediate_snapshot(p)
    assert len(result['clean']) == 0 and len(result['quarantine']) == 2
    a, b = result['quarantine']
    assert a['semantic_group_id'] == b['semantic_group_id']
    assert a['semantic_group_size'] == b['semantic_group_size'] == 2
    assert result['summary']['potential_semantic_duplicate_groups'] == 1


def test_duplicate_provider_filing_ids_all_quarantined(tmp_path):
    result = remediate_snapshot(make(tmp_path, trade(), trade(ticker='MSFT')))
    assert len(result['quarantine']) == 2
    assert all('duplicate_source_filing_id' in row['reason_codes'] for row in result['quarantine'])


def test_naive_timestamp_is_quarantined_not_assigned_utc(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(ingestedAt='2026-08-24 15:02:49')))['quarantine'][0]
    assert rec['upstream_ingested_at_utc'] is None
    assert rec['first_seen_at'] is None
    assert 'upstream_timestamp_missing_or_timezone_naive' in rec['reason_codes']


def test_unknown_action_does_not_create_trade_signal(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(type='Mystery')))['quarantine'][0]
    assert 'unknown_action_type' in rec['reason_codes']
    assert not rec['eligible_for_execution']


def test_inverted_dates_quarantined(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(disclosureDate='2026-07-01')))['quarantine'][0]
    assert 'invalid_or_inverted_dates' in rec['reason_codes']


def test_invalid_amount_bounds_quarantined(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(amountMin=20000)))['quarantine'][0]
    assert 'invalid_amount_bounds' in rec['reason_codes']


def test_invalid_member_is_quarantined(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(memberName='Someone Else')))['quarantine'][0]
    assert 'member_identity_mismatch' in rec['reason_codes']


def test_missing_attribution_blocks_entire_snapshot(tmp_path):
    value = envelope(trade())
    del value['meta']['attribution']
    p = tmp_path / 'bad.json'
    p.write_text(json.dumps(value))
    with pytest.raises(RemediationError, match='attribution'):
        remediate_snapshot(p)


def test_500_cap_blocks_entire_snapshot(tmp_path):
    with pytest.raises(RemediationError, match='truncated'):
        remediate_snapshot(make(tmp_path, *[trade(filingId=f'id-{i}') for i in range(500)]))


def test_invalid_json_blocks_entire_snapshot(tmp_path):
    p = tmp_path / 'broken.json'
    p.write_text('<html>Error</html>')
    with pytest.raises(RemediationError, match='UTF-8 JSON'):
        remediate_snapshot(p)


def test_naive_assessment_time_rejected(tmp_path):
    with pytest.raises(RemediationError, match='timezone aware'):
        remediate_snapshot(make(tmp_path, trade()), assessed_at=datetime(2026, 11, 2))


def test_csv_formula_injection_escaped():
    for text in ('=1+1', '+HYPERLINK(1)', '-42', '@name', '  =1+1', '\t=cmd'):
        assert _csv_safe(text).startswith("'")
    assert _csv_safe('NVDA') == 'NVDA'


def test_jsonl_csv_outputs_integrity_and_determinism(tmp_path):
    src = make(tmp_path, trade(), trade(filingId='id-2', sourceLink=None, ticker='MSFT'))
    result = remediate_snapshot(src, assessed_at=FIXED_AT)
    a = write_research_outputs(result, tmp_path / 'one')
    b = write_research_outputs(result, tmp_path / 'two')
    assert a['manifest']['output_files_sha256'] == b['manifest']['output_files_sha256']
    for status in ('clean', 'quarantine'):
        csv_path = a['paths'][f'{status}_csv']
        jl_path = a['paths'][f'{status}_jsonl']
        with csv_path.open(encoding='utf-8-sig') as f:
            csv_rows = list(csv.DictReader(f))
        lines = [json.loads(line) for line in jl_path.read_text().splitlines()]
        assert len(csv_rows) == len(lines) == 1
        assert lines[0]['raw_record']['ticker'] == csv_rows[0]['ticker']
        assert lines[0]['eligible_for_execution'] is False
    assert a['manifest']['total_rows'] == 2
    assert a['manifest']['supabase_writes'] == 0
    assert a['manifest']['orders_submitted'] == 0


@pytest.mark.skipif(not os.environ.get('E117_SNAPSHOT_PATH'), reason='User source file is not checked into the repository')
def test_real_user_snapshot_matches_prior_audit_and_no_data_loss(tmp_path):
    path = Path(os.environ['E117_SNAPSHOT_PATH'])
    result = remediate_snapshot(path, assessed_at=FIXED_AT)
    s = result['summary']
    assert s['source_snapshot_sha256'] == REAL_SHA
    assert s['total_rows'] == 147
    assert s['structurally_clean_unverified_rows'] == 75
    assert s['quarantined_rows'] == 72
    assert s['potential_semantic_duplicate_groups'] == 12
    assert s['quarantine_reason_counts_overlapping']['potential_semantic_duplicate'] == 24
    assert s['quarantine_reason_counts_overlapping']['missing_or_untrusted_official_filing_url'] == 21
    assert s['quarantine_reason_counts_overlapping']['ambiguous_action_other'] == 34
    assert s['quarantine_reason_counts_overlapping']['stock_label_derivative_or_exercise_description'] == 10
    assert [r['row_index'] for r in sorted(result['clean']+result['quarantine'],key=lambda x:x['row_index'])] == list(range(1,148))
    out = write_research_outputs(result, tmp_path / 'out')
    assert len(out['manifest']['output_files_sha256']) == 4
    assert all(rec['first_seen_at'] is None for rec in result['clean']+result['quarantine'])
    assert all(not rec['eligible_for_ingestion'] and not rec['eligible_for_execution'] for rec in result['clean']+result['quarantine'])


def test_malformed_port_is_quarantined_not_crashing(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(sourceLink='https://disclosures-clerk.house.gov:invalid/public_disc/ptr-pdfs/2026/20035143.pdf')))['quarantine'][0]
    assert 'missing_or_untrusted_official_filing_url' in rec['reason_codes']


def test_future_filing_quarantined(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(transactionDate='2027-01-01', disclosureDate='2027-01-10')), assessed_at=FIXED_AT)['quarantine'][0]
    assert 'future_transaction_or_filing_date' in rec['reason_codes']


def test_infinite_amount_quarantined(tmp_path):
    rec = remediate_snapshot(make(tmp_path, trade(amountMax=float('inf'))))['quarantine'][0]
    assert 'invalid_amount_bounds' in rec['reason_codes']
