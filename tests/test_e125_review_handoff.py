"""E1.25: no-network read-only reviewer packet, independent source lineage."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from congress_review_handoff import (
    HandoffError, build_handoff, write_handoff, main, _safe_cell,
)
from congress_adjudication import REVIEW_COLUMNS, ROLES, read_review_csv
from test_e124_release_gate import fixture as e124_fixture, regenerate_all_manifest_hashes

ARCHIVE_ENV = 'E125_E123_PACKAGE_DIR'
REAL_SHA = '4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3'


def _update_csv(path: Path, update):
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    rows, columns = update(rows, columns)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _append_source_files_to_manifests(root: Path, *relative_paths: str):
    for rel in relative_paths:
        base = rel.split('/', 1)[0]
        manifest_filename, key = {
            'e119': ('pelosi_e119_manifest.json', 'output_sha256'),
            'e120': ('pelosi_e120_manifest.json', 'local_artifact_sha256'),
        }[base]
        path = root / base / manifest_filename
        data = json.loads(path.read_text())
        data[key][Path(rel).name] = ''
        path.write_text(json.dumps(data), encoding='utf-8')
        outer_path = root / 'e123_manifest.json'
        outer = json.loads(outer_path.read_text())
        outer['artifact_hashes_sha256'][rel] = ''
        outer_path.write_text(json.dumps(outer), encoding='utf-8')
    regenerate_all_manifest_hashes(root)


def synthetic(tmp_path):
    root = e124_fixture(tmp_path)
    task = json.loads((root / 'e118/pelosi_e118_human_review_queue.jsonl').read_text())
    assert task['task_id'] == 'e118-task-2'

    with (root / 'e119/pelosi_e119_two_reviewer_template.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        w.writeheader()
        for role in ROLES:
            w.writerow({'task_id': task['task_id'], 'row_index': task['row_index'], 'role': role})

    missing_cols = (
        'task_id', 'row_index', 'review_priority', 'ticker', 'transaction_date', 'filing_date',
        'reason_codes', 'suggested_evidence_url_unverified', 'required_next_action',
        'official_filing_url', 'verified_link', 'pdf_origin_authenticated',
        'eligible_for_ingestion', 'eligible_for_execution',
    )
    task['review_priority'] = 'P2'
    for path in ('e118/pelosi_e118_human_review_queue.jsonl',):
        (root / path).write_text(json.dumps(task) + '\n', encoding='utf-8')
    path = root / 'e120/pelosi_e120_missing_provenance_queue.csv'
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=missing_cols)
        w.writeheader()
        w.writerow({'task_id': task['task_id'], 'row_index': 2, 'review_priority': 'P2',
                    'ticker': 'MSFT', 'transaction_date': '2026-05-01',
                    'filing_date': '2026-06-01',
                    'reason_codes': json.dumps(task['reason_codes']),
                    'verified_link': False, 'pdf_origin_authenticated': False,
                    'eligible_for_ingestion': False, 'eligible_for_execution': False})
    # E1.20's empty group queue is required as a provenance artifact.
    group_path = root / 'e120/pelosi_e120_duplicate_group_evidence.csv'
    group_path.write_text('semantic_group_id,row_indices,member_count,linked_source_urls_unverified,missing_link_row_indices,resolved\n')
    _append_source_files_to_manifests(root,
        'e119/pelosi_e119_two_reviewer_template.csv',
        'e120/pelosi_e120_duplicate_group_evidence.csv')
    return root


def real_path():
    return os.getenv(ARCHIVE_ENV)


def real_or_skip():
    folder = real_path()
    if not folder:
        pytest.skip('User-provided archived E1.23 evidence is not committed to public GitHub CI')
    return Path(folder)


def test_synthetic_missing_source_only_keeps_full_hold(tmp_path):
    p = build_handoff(synthetic(tmp_path))
    s = p['summary']
    assert (s['total_source_records'], s['clean_source_unverified_on_hold'],
            s['quarantined_review_tasks']) == (2, 1, 1)
    assert (s['candidate_documents_to_request'], s['missing_source_link_tasks'],
            s['two_reviewer_blank_slots']) == (0, 1, 2)
    assert p['tasks'][0]['candidate_pdf_filename'] is None
    assert not p['tasks'][0]['eligible_for_ingestion']


def test_generates_deterministic_and_e119_compatible_template(tmp_path):
    p = build_handoff(synthetic(tmp_path))
    a = write_handoff(p, tmp_path / 'out1')
    b = write_handoff(p, tmp_path / 'out2')
    assert a['manifest'] == b['manifest']
    for key in a['paths']:
        assert a['paths'][key].read_bytes() == b['paths'][key].read_bytes()
    assert read_review_csv(a['paths']['two_reviewer_template']) == []
    assert hashlib.sha256(a['paths']['two_reviewer_template'].read_bytes()).hexdigest() == p['summary']['source_e119_blank_template_sha256']
    for key, sha in a['manifest']['local_output_sha256'].items():
        assert hashlib.sha256((tmp_path / 'out1' / key).read_bytes()).hexdigest() == sha
    assert a['manifest']['approved_for_trading_rows'] == 0


def test_refuses_output_overwrite(tmp_path):
    r = build_handoff(synthetic(tmp_path))
    write_handoff(r, tmp_path / 'created')
    with pytest.raises(HandoffError, match='new directory'):
        write_handoff(r, tmp_path / 'created')


def test_refuses_symlink_output(tmp_path):
    r = build_handoff(synthetic(tmp_path))
    (tmp_path / 'link').symlink_to(tmp_path / 'target', target_is_directory=True)
    with pytest.raises(HandoffError, match='new directory'):
        write_handoff(r, tmp_path / 'link')


def test_rejects_missing_two_reviewer_slot_rehashed_manifests(tmp_path):
    root = synthetic(tmp_path)
    f = root / 'e119/pelosi_e119_two_reviewer_template.csv'
    _update_csv(f, lambda rows, cols: (rows[:1], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='reviewer slots'):
        build_handoff(root)


def test_rejects_duplicated_reviewer_role_rehashed_manifests(tmp_path):
    root = synthetic(tmp_path)
    f = root / 'e119/pelosi_e119_two_reviewer_template.csv'
    _update_csv(f, lambda rows, cols: ([{**r, 'role': 'primary'} for r in rows], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='reviewer slot'):
        build_handoff(root)


def test_rejects_fabricated_reviewer_name_even_when_rehashed(tmp_path):
    root = synthetic(tmp_path)
    f = root / 'e119/pelosi_e119_two_reviewer_template.csv'
    _update_csv(f, lambda rows, cols: ([{**r, 'reviewer_id': 'fake_jane'} for r in rows], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='unexpectedly contains decisions'):
        build_handoff(root)


def test_rejects_forged_provenance_on_missing_task(tmp_path):
    root = synthetic(tmp_path)
    f = root / 'e120/pelosi_e120_missing_provenance_queue.csv'
    _update_csv(f, lambda rows, cols: ([{**r, 'verified_link': 'True'} for r in rows], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='incorrectly linked or promoted'):
        build_handoff(root)


def test_rejects_dropped_missing_row_even_if_manifest_hashes_rewritten(tmp_path):
    root = synthetic(tmp_path)
    f = root / 'e120/pelosi_e120_missing_provenance_queue.csv'
    _update_csv(f, lambda rows, cols: ([], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='silently dropped tasks'):
        build_handoff(root)


def test_rejects_unapproved_provider_data_reclassification(tmp_path):
    root = synthetic(tmp_path)
    f = root / 'e117/pelosi_e117_quarantined_review.jsonl'
    data = json.loads(f.read_text())
    data['eligible_for_ingestion'] = True
    f.write_text(json.dumps(data)+'\n')
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='E1.24 hold gate refused'):
        build_handoff(root)


def test_no_http_or_database_connections_in_cli(tmp_path, monkeypatch, capsys):
    import socket
    monkeypatch.setattr(socket.socket, 'connect',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('Unexpected network')))
    root = synthetic(tmp_path)
    assert main([str(root), '--output-dir', str(tmp_path / 'out')]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['source_rows'] == 2
    assert payload['human_review_tasks'] == 1
    assert payload['reviewer_slots'] == 2


def test_csv_formula_injection_escaped():
    assert _safe_cell('=WEBSERVICE("https://example.org")').startswith("'=")
    assert _safe_cell('  @cmd').startswith("'  @")
    assert _safe_cell('normal') == 'normal'


def test_real_e123_147_row_binder_optional():
    root = real_or_skip()
    p = build_handoff(root)
    s = p['summary']
    assert s['source_snapshot_sha256'] == REAL_SHA
    assert (s['total_source_records'], s['clean_source_unverified_on_hold'],
            s['quarantined_review_tasks']) == (147, 75, 72)
    assert (s['candidate_documents_to_request'], s['document_linked_tasks'],
            s['missing_source_link_tasks'], s['semantic_duplicate_groups_unresolved'],
            s['two_reviewer_blank_slots']) == (17, 51, 21, 12, 144)
    assert p['documents'][0]['expected_pdf_filename'] == '2023_20022260.pdf'
    assert p['documents'][0]['linked_task_count'] == 11
    assert p['documents'][1]['expected_pdf_filename'] == '2026_20033725.pdf'
    assert p['documents'][1]['linked_task_count'] == 10
    assert all(not t['eligible_for_ingestion'] and t['first_seen_at'] is None for t in p['tasks'])
    assert s['reviewer_decisions_submitted'] == 0


def _copy_real(tmp_path):
    root = real_or_skip()
    clone = tmp_path / 'copy'
    shutil.copytree(root, clone)
    return clone


def test_real_rejects_document_hijack_even_if_manifests_rehashed(tmp_path):
    root = _copy_real(tmp_path)
    f = root / 'e120/pelosi_e120_house_pdf_request_manifest.csv'
    _update_csv(f, lambda rows, cols: ([{**r, 'linked_task_ids': '[]'} if i == 0 else r
                                       for i, r in enumerate(rows)], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='Document task claims'):
        build_handoff(root)


def test_real_rejects_group_member_suppression_even_if_rehashed(tmp_path):
    root = _copy_real(tmp_path)
    f = root / 'e120/pelosi_e120_duplicate_group_evidence.csv'
    _update_csv(f, lambda rows, cols: ([{**r, 'member_count': '1'} if i == 0 else r
                                       for i, r in enumerate(rows)], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='Semantic group member'):
        build_handoff(root)


def test_real_rejects_candidate_doc_claimed_authenticated_even_if_rehashed(tmp_path):
    root = _copy_real(tmp_path)
    f = root / 'e120/pelosi_e120_house_pdf_request_manifest.csv'
    _update_csv(f, lambda rows, cols: ([{**r, 'pdf_origin_authenticated': 'True'} if i == 0 else r
                                       for i, r in enumerate(rows)], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='falsely claims verified evidence'):
        build_handoff(root)


def synthetic_linked(tmp_path):
    """Build a fully synthetic, consistent E1.23 plan for one linked HOLD task."""
    root = synthetic(tmp_path)
    held = root / 'e117/pelosi_e117_quarantined_review.jsonl'
    e118 = root / 'e118/pelosi_e118_human_review_queue.jsonl'
    original = json.loads(held.read_text())
    task = json.loads(e118.read_text())
    url = 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000001.pdf'
    for item in (original, task):
        item['raw_record']['sourceLink'] = url
        item['raw_record']['type'] = 'Other'
        item['raw_record_sha256'] = hashlib.sha256(json.dumps(item['raw_record'], sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        item['official_filing_url'] = url
        item['action_reported'] = 'Other'
        item['reason_codes'] = ['ambiguous_action_other']
    held.write_text(json.dumps(original,sort_keys=True)+'\n')
    e118.write_text(json.dumps(task,sort_keys=True)+'\n')
    docs = root / 'e120/pelosi_e120_house_pdf_request_manifest.csv'
    from congress_evidence_intake import REQUEST_COLUMNS
    with docs.open('w', encoding='utf-8-sig', newline='') as fp:
        writer = csv.DictWriter(fp, fieldnames=REQUEST_COLUMNS)
        writer.writeheader()
        writer.writerow({
            'request_id': 'e120-synthetic-1', 'review_priority': 'P2',
            'source_url_unverified': url, 'expected_local_filename': '2026_20000001.pdf',
            'source_document_year': '2026', 'source_document_id': '20000001',
            'linked_task_count': 1, 'linked_row_indices': json.dumps([2]),
            'linked_task_ids': json.dumps(['e118-task-2']),
            'semantic_group_ids': '[]', 'action_ambiguities': 1,
            'derivative_ambiguities': 0,
            'local_pdf_status': 'awaiting_user_supplied_pdf',
            'pdf_origin_authenticated': False, 'official_filing_verified': False,
            'eligible_for_ingestion': False, 'eligible_for_execution': False,
        })
    missing = root / 'e120/pelosi_e120_missing_provenance_queue.csv'
    _update_csv(missing, lambda rows, cols: ([], cols))
    man = root / 'e120/pelosi_e120_manifest.json'
    data = json.loads(man.read_text())
    data['distinct_document_requests'] = 1
    data['missing_or_untrusted_link_tasks'] = 0
    man.write_text(json.dumps(data))
    regenerate_all_manifest_hashes(root)
    return root


def test_synthetic_candidate_document_is_never_authenticated(tmp_path):
    p = build_handoff(synthetic_linked(tmp_path))
    assert len(p['documents']) == 1
    doc = p['documents'][0]
    assert doc['expected_pdf_filename'] == '2026_20000001.pdf'
    assert doc['linked_row_indices'] == [2]
    assert doc['action_ambiguities'] == 1
    assert doc['origin_authenticated'] is False
    assert p['tasks'][0]['source_link_status'] == 'provider_link_candidate_unverified'
    assert p['summary']['approved_for_ingestion_rows'] == 0


def test_synthetic_rejects_hijacked_document_task_reference(tmp_path):
    root = synthetic_linked(tmp_path)
    f = root / 'e120/pelosi_e120_house_pdf_request_manifest.csv'
    _update_csv(f, lambda rows, cols: ([{**r, 'linked_task_ids': '["unknown"]'} for r in rows], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='Document task claims'):
        build_handoff(root)


def test_synthetic_candidate_document_forged_pdf_hash_rejected(tmp_path):
    root = synthetic_linked(tmp_path)
    f = root / 'e120/pelosi_e120_house_pdf_request_manifest.csv'
    _update_csv(f, lambda rows, cols: ([{**r, 'local_pdf_sha256': 'a'*64} for r in rows], cols))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(HandoffError, match='falsely claims verified evidence'):
        build_handoff(root)
