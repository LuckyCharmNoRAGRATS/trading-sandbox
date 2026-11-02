"""E1.24 offline evidence/release gate for an E1.23 Pelosi research package.

It reads a local, hash-linked E1.17→E1.23 package and emits a deterministic
research hold plan. It contains no SQL, HTTP, Supabase, schedule or order code.
No provider record is authorized for ingestion or execution, even when the
upstream provider returned HTTP 200 or the E1.17 research status is 'clean'.
"""
from __future__ import annotations

from collections import Counter
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

VERSION = 'E1.24.0'
MAX_FILE = 20_000_000
MAX_ROWS = 499
SHA = re.compile(r'[0-9a-f]{64}\Z')
REQUIRED = {
    'e117/pelosi_e117_summary.json',
    'e117/pelosi_e117_clean_research.jsonl',
    'e117/pelosi_e117_quarantined_review.jsonl',
    'e118/pelosi_e118_manifest.json',
    'e118/pelosi_e118_human_review_queue.jsonl',
    'e118/pelosi_e118_research_only.jsonl',
    'e118/pelosi_e118_semantic_duplicate_groups.csv',
    'e119/pelosi_e119_manifest.json',
    'e119/pelosi_e119_review_status.csv',
    'e119/pelosi_e119_audit_ledger.jsonl',
    'e120/pelosi_e120_manifest.json',
    'e120/pelosi_e120_house_pdf_request_manifest.csv',
    'e120/pelosi_e120_missing_provenance_queue.csv',
}
COLUMNS = [
    'row_index', 'research_event_id', 'ticker', 'transaction_date',
    'filing_date', 'asset_type_reported', 'action_reported', 'source_filing_id',
    'source_snapshot_sha256', 'raw_record_sha256', 'semantic_group_id',
    'quality_status', 'release_status', 'release_blockers',
    'first_seen_at', 'original_filing_verified', 'eligible_for_ingestion',
    'eligible_for_execution',
]


class ReleaseGateError(ValueError):
    """Unexpected source change, incomplete provenance or unsafe release claim."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _read(path: Path, *, empty_ok: bool = False) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ReleaseGateError('Unsafe or missing local artifact: ' + path.name)
    size = path.stat().st_size
    if size > MAX_FILE or (size == 0 and not empty_ok):
        raise ReleaseGateError('Missing, empty or oversized local artifact: ' + path.name)
    raw = path.read_bytes()
    if len(raw) != size:
        raise ReleaseGateError('Artifact changed while reading: ' + path.name)
    return raw


def _json(path: Path) -> dict:
    try:
        body = json.loads(_read(path))
    except (UnicodeError, ValueError) as exc:
        raise ReleaseGateError('Invalid JSON manifest: ' + path.name) from exc
    if not isinstance(body, dict):
        raise ReleaseGateError('Manifest is not a JSON object')
    return body


def _jsonl(path: Path) -> list[dict]:
    try:
        lines = _read(path, empty_ok=True).decode('utf-8').splitlines()
        result = [json.loads(line) for line in lines if line.strip()]
    except (UnicodeError, ValueError) as exc:
        raise ReleaseGateError('Invalid JSONL: ' + path.name) from exc
    if len(result) > MAX_ROWS or any(not isinstance(x, dict) for x in result):
        raise ReleaseGateError('Invalid JSONL row limit or object type')
    return result


def _legacy_or_new(manifest: dict, old: str, new: str) -> Any:
    if old in manifest and new in manifest and manifest[old] != manifest[new]:
        raise ReleaseGateError('Contradictory E1.18 manifest aliases: ' + old)
    return manifest.get(old, manifest.get(new))


def _assert_sha_map(root: Path, stage: str, values: Any) -> None:
    if not isinstance(values, dict):
        raise ReleaseGateError('Missing nested checksum map: ' + stage)
    for name, expected in values.items():
        if (not isinstance(name, str) or not re.fullmatch(r'pelosi_[a-zA-Z0-9_.-]+', name)
                or not isinstance(expected, str) or not SHA.fullmatch(expected)):
            raise ReleaseGateError('Unsafe nested checksum key or hash')
        if _sha(_read(root / stage / name, empty_ok=name.endswith('.jsonl'))) != expected:
            raise ReleaseGateError('Nested artifact SHA-256 mismatch: ' + name)


def _frozen(row: dict, snapshot_sha: str) -> None:
    if (row.get('snapshot_sha256') != snapshot_sha or row.get('first_seen_at') is not None
            or row.get('official_filing_verified') is not False
            or row.get('eligible_for_ingestion') is not False
            or row.get('eligible_for_execution') is not False):
        raise ReleaseGateError('Source contains unjustified provenance or authorization')
    raw = row.get('raw_record')
    if (not isinstance(raw, dict) or not isinstance(row.get('raw_record_sha256'), str)
            or _sha(_canonical(raw)) != row['raw_record_sha256']):
        raise ReleaseGateError('Original source row checksum mismatch')


def verify_research_package(directory: str | Path) -> dict:
    """Read and attest evidence from a completed E1.23 local package.

    Provides an all-on-hold dry-run.  No SQL/Supabase/client is instantiated.
    Hashes detect mismatch within locally supplied files, NOT official origin.
    """
    root = Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ReleaseGateError('Expected a local, nonsymlink E1.23 package directory')
    outer = _json(root / 'e123_manifest.json')
    if outer.get('version') != 'E1.23.0' or outer.get('status') != 'NO_GO_READ_ONLY_RESEARCH':
        raise ReleaseGateError('Unexpected E1.23 package version or safety status')
    fingerprint = outer.get('source_snapshot_sha256')
    if not isinstance(fingerprint, str) or not SHA.fullmatch(fingerprint):
        raise ReleaseGateError('Missing source snapshot fingerprint')
    for key in ('ingestion_approved', 'execution_approved', 'historical_completeness_verified',
                'first_seen_at_established', 'scheduled_sync_enabled'):
        if outer.get(key) is not False:
            raise ReleaseGateError('Unexpected E1.23 release or completeness claim: ' + key)
    for key in ('external_http_requests', 'supabase_writes', 'broker_orders',
                'pdf_files_authenticated', 'reviewer_evidence_events'):
        if outer.get(key) != 0:
            raise ReleaseGateError('Unexpected E1.23 side-effect or evidence claim: ' + key)
    files = outer.get('artifact_hashes_sha256')
    if not isinstance(files, dict) or not REQUIRED.issubset(set(files)):
        raise ReleaseGateError('Required E1.23 source evidence not present')
    if len(files) > 60:
        raise ReleaseGateError('Unexpectedly large artifact manifest')
    for relative, digest in files.items():
        if (not isinstance(relative, str) or not re.fullmatch(r'e1(?:17|18|19|20)/pelosi_[a-zA-Z0-9_.-]+', relative)
                or not isinstance(digest, str) or not SHA.fullmatch(digest)):
            raise ReleaseGateError('Unsafe E1.23 artifact path or digest')
        path = root / relative
        if _sha(_read(path, empty_ok=relative.endswith('.jsonl'))) != digest:
            raise ReleaseGateError('Artifact SHA-256 mismatch: ' + relative)
    e117 = _json(root / 'e117/pelosi_e117_summary.json')
    e118 = _json(root / 'e118/pelosi_e118_manifest.json')
    e119 = _json(root / 'e119/pelosi_e119_manifest.json')
    e120 = _json(root / 'e120/pelosi_e120_manifest.json')
    if e117.get('status') != 'NO_GO_RESEARCH_ONLY_SOURCE_UNVERIFIED':
        raise ReleaseGateError('Unexpected E1.17 research status')
    _assert_sha_map(root, 'e117', e117.get('output_files_sha256'))
    _assert_sha_map(root, 'e118', _legacy_or_new(e118, 'output_sha256', 'local_output_sha256'))
    _assert_sha_map(root, 'e119', e119.get('output_sha256'))
    _assert_sha_map(root, 'e120', e120.get('local_artifact_sha256'))
    if not (e117.get('source_snapshot_sha256') == fingerprint
            and _legacy_or_new(e118, 'source_sha256', 'source_snapshot_sha256') == fingerprint
            and e119.get('source_snapshot_sha256') == fingerprint
            and e120.get('source_snapshot_sha256') == fingerprint):
        raise ReleaseGateError('Cross-phase snapshot lineage mismatch')
    rows_clean = _jsonl(root / 'e117/pelosi_e117_clean_research.jsonl')
    rows_quarantine = _jsonl(root / 'e117/pelosi_e117_quarantined_review.jsonl')
    e118_clean = _jsonl(root / 'e118/pelosi_e118_research_only.jsonl')
    e118_quarantine = _jsonl(root / 'e118/pelosi_e118_human_review_queue.jsonl')
    count = len(rows_clean) + len(rows_quarantine)
    if not (0 < count < 500 and count == e117.get('total_rows')
            == outer.get('total_source_rows')
            == _legacy_or_new(e118, 'source_rows', 'total_source_rows')
            and len(rows_clean) == e117.get('structurally_clean_unverified_rows')
            == outer.get('structurally_clean_source_unverified')
            == len(e118_clean) == _legacy_or_new(e118, 'clean_unverified', 'clean_research_only_unverified_rows')
            and len(rows_quarantine) == e117.get('quarantined_rows')
            == outer.get('quarantined_human_review_required')
            == len(e118_quarantine) == _legacy_or_new(e118, 'review_queue', 'quarantined_human_review_rows')):
        raise ReleaseGateError('Cross-phase accounting mismatch')
    source_rows = {}
    for status, rows in [('clean', rows_clean), ('quarantine', rows_quarantine)]:
        for row in rows:
            _frozen(row, fingerprint)
            idx = row.get('row_index')
            if type(idx) is not int or idx in source_rows:
                raise ReleaseGateError('Invalid or duplicate source row index')
            if status == 'clean' and (row.get('reason_codes') != [] or
                  row.get('quality_status') != 'structurally_clean_source_unverified'):
                raise ReleaseGateError('False clean-row classification')
            if status == 'quarantine' and (not isinstance(row.get('reason_codes'), list)
                  or not row['reason_codes'] or row.get('quality_status') != 'quarantined_review_required'):
                raise ReleaseGateError('Missing quarantine review reasons')
            source_rows[idx] = row
    if set(source_rows) != set(range(1, count + 1)):
        raise ReleaseGateError('Source row indexes discontinuous')
    if len(e118_clean) + len(e118_quarantine) != count:
        raise ReleaseGateError('E1.18 changed source row population')
    tasks = set()
    seen_e118_indexes = set()
    for status, rows in [('clean', e118_clean), ('quarantine', e118_quarantine)]:
        for row in rows:
            _frozen(row, fingerprint)
            if row.get('row_index') in seen_e118_indexes:
                raise ReleaseGateError('Duplicate E1.18 row index across partitions')
            seen_e118_indexes.add(row.get('row_index'))
            original = source_rows.get(row.get('row_index'))
            if original is None:
                raise ReleaseGateError('New or unknown row in E1.18')
            for field in ('raw_record_sha256', 'research_event_id', 'quality_status',
                          'reason_codes', 'source_filing_id', 'official_filing_url',
                          'action_reported', 'asset_type_reported', 'ticker',
                          'transaction_date', 'filing_date', 'semantic_group_id'):
                if row.get(field) != original.get(field):
                    raise ReleaseGateError('E1.18 silently changed source evidence: ' + field)
            if status == 'quarantine':
                task_id = row.get('task_id')
                if (not isinstance(task_id, str) or not task_id.startswith('e118-')
                        or task_id in tasks or row.get('queue_status') != 'pending_original_filing_evidence'):
                    raise ReleaseGateError('Invalid E1.18 review task')
                tasks.add(task_id)
            elif row.get('quality_status') != 'structurally_clean_source_unverified':
                raise ReleaseGateError('E1.18 clean-row classification changed')
    if seen_e118_indexes != set(source_rows):
        raise ReleaseGateError('E1.18 did not conserve original row identities')
    if (not isinstance(e119.get('status_counts'), dict)
            or e119.get('status_counts', {}).get('awaiting_two_reviewers') != len(tasks)
            or e119.get('review_events') != 0
            or e119.get('source_authenticated_pdf_count') != 0
            or e119.get('ingestion_approved') is not False
            or e119.get('execution_approved') is not False):
        raise ReleaseGateError('E1.19 has reviews, claimed source evidence or unsafe status')
    review_csv = _read(root / 'e119/pelosi_e119_review_status.csv').decode('utf-8-sig')
    try:
        reviewed = list(csv.DictReader(review_csv.splitlines()))
    except csv.Error as exc:
        raise ReleaseGateError('Invalid review status CSV') from exc
    if len(reviewed) != len(tasks) or {row.get('task_id') for row in reviewed} != tasks:
        raise ReleaseGateError('E1.19 reviewer queue incomplete')
    if any(row.get('review_status') != 'awaiting_two_reviewers' or row.get('eligible_for_ingestion') != 'False'
           or row.get('eligible_for_execution') != 'False'
           or (row.get('official_filing_verified') not in (None, 'False'))
           or (row.get('original_house_pdf_authenticated') not in (None, 'False'))
           for row in reviewed):
        raise ReleaseGateError('E1.19 review status unexpectedly advanced')
    if _read(root / 'e119/pelosi_e119_audit_ledger.jsonl', empty_ok=True):
        raise ReleaseGateError('Unexpected reviewer audit event contents')
    if (e120.get('house_pdf_origin_authenticated') != 0
            or e120.get('official_filing_verified') != 0
            or e120.get('reviewer_decisions') != 0
            or e120.get('supabase_writes') != 0
            or e120.get('broker_orders') != 0):
        raise ReleaseGateError('E1.20 claims evidence or external side effects')
    if (e120.get('quarantined_tasks') != len(tasks)
            or e120.get('potential_duplicate_groups') != outer.get('potential_duplicate_groups')):
        raise ReleaseGateError('E1.20 review and duplicate counts inconsistent')
    # Nothing here is fit for ingestion, despite provider HTTP200 reported earlier.
    plan = []
    for idx, row in sorted(source_rows.items()):
        unverified = row.get('quality_status') == 'structurally_clean_source_unverified'
        reasons = (['original_filing_unauthenticated', 'historical_first_seen_unavailable',
                    'release_not_authorized'] if unverified else
                   sorted(set(row['reason_codes']) | {'original_filing_unauthenticated',
                                                      'human_adjudication_pending',
                                                      'release_not_authorized'}))
        plan.append({
            'row_index': idx, 'research_event_id': row.get('research_event_id'),
            'ticker': row.get('ticker'), 'transaction_date': row.get('transaction_date'),
            'filing_date': row.get('filing_date'), 'asset_type_reported': row.get('asset_type_reported'),
            'action_reported': row.get('action_reported'),
            'source_filing_id': row.get('source_filing_id'),
            'source_snapshot_sha256': fingerprint, 'raw_record_sha256': row['raw_record_sha256'],
            'semantic_group_id': row.get('semantic_group_id'),
            'quality_status': row['quality_status'],
            'release_status': ('HOLD_SOURCE_UNVERIFIED' if unverified else 'HOLD_QUARANTINED'),
            'release_blockers': reasons,
            'first_seen_at': None, 'original_filing_verified': False,
            'eligible_for_ingestion': False, 'eligible_for_execution': False,
        })
    summary = {
        'version': VERSION, 'decision': 'NO_GO_OFFLINE_HOLD_ONLY',
        'source_snapshot_sha256': fingerprint, 'source_e123_manifest_sha256': _sha(_read(root / 'e123_manifest.json')),
        'total_source_rows': count, 'source_unverified_research_rows': len(rows_clean),
        'quarantined_rows': len(rows_quarantine), 'human_review_tasks_pending': len(tasks),
        'pending_duplicate_groups': outer['potential_duplicate_groups'],
        'requested_filing_documents': e120.get('distinct_document_requests'),
        'missing_link_tasks': e120.get('missing_or_untrusted_link_tasks'),
        'approved_for_ingestion_rows': 0, 'approved_for_trading_rows': 0,
        'release_holds_by_status': dict(sorted(Counter(x['release_status'] for x in plan).items())),
        'unverified_filing_count': count, 'first_seen_at_historical_estimates': 0,
        'source_available_for_readonly_research': True,
        'external_http_requests': 0, 'supabase_writes': 0, 'broker_orders': 0,
        'scheduled_sync_enabled': False, 'first_seen_at_established': False,
        'source_authenticated': False, 'official_filing_verified': False,
        'ingestion_approved': False, 'execution_approved': False,
    }
    return {'summary': summary, 'hold_queue': plan}


def _csv_safe(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    s = str(value)
    return "'" + s if s.lstrip().startswith(('=', '+', '-', '@')) else s


def write_hold_report(report: dict, target_dir: str | Path) -> dict:
    """Write an immutable local-only audit report and an all-HOLD preview."""
    output = Path(target_dir)
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        raise ReleaseGateError('Expected a new local output directory in a real parent')
    stage = Path(tempfile.mkdtemp(prefix='.e124-stage-', dir=output.parent))
    try:
        csv_path = stage / 'pelosi_e124_hold_queue.csv'
        jsonl_path = stage / 'pelosi_e124_hold_queue.jsonl'
        with csv_path.open('w', encoding='utf-8-sig', newline='') as fp:
            writer = csv.DictWriter(fp, fieldnames=COLUMNS)
            writer.writeheader()
            for row in report['hold_queue']:
                writer.writerow({name: _csv_safe(row.get(name)) for name in COLUMNS})
        with jsonl_path.open('w', encoding='utf-8') as fp:
            for row in report['hold_queue']:
                fp.write(_canonical(row).decode('utf-8') + '\n')
        checksums = {p.name: _sha(_read(p)) for p in [csv_path, jsonl_path]}
        manifest = dict(report['summary'], local_artifacts_sha256=dict(sorted(checksums.items())))
        (stage / 'pelosi_e124_release_readiness.json').write_text(
            json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        if output.exists() or output.is_symlink():
            raise ReleaseGateError('Output destination appeared mid-write')
        os.replace(stage, output)
        return {'manifest': manifest, 'paths': {'csv': output / csv_path.name,
                                                'jsonl': output / jsonl_path.name,
                                                'manifest': output / 'pelosi_e124_release_readiness.json'}}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='E1.24 offline E1.23 evidence release gate (no database/trading)')
    parser.add_argument('e123_directory', type=Path, help='existing local E1.23 output folder')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        checked = verify_research_package(args.e123_directory)
        out = write_hold_report(checked, args.output_dir)
    except (ReleaseGateError, OSError, ValueError) as exc:
        parser.exit(2, f'E1.24 RELEASE BLOCKED: {exc}\n')
    print(json.dumps({'decision': out['manifest']['decision'],
                      'rows_on_hold': len(checked['hold_queue']),
                      'approved_rows': 0, 'output_dir': str(args.output_dir)}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
