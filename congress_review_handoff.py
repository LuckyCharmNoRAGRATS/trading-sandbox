"""E1.25: strictly offline Pelosi document and two-reviewer handoff.

Consumes only a completed, checksum-linked E1.23 research package. Re-runs
E1.24's all-HOLD release gate, cross-checks E1.18 tasks against E1.19 blank
review slots and E1.20 document/missing-link inventories, and creates a
self-hashed local work packet. It never authenticates House PDFs or emits a
release-ready row. No web/requests/SQL/broker/scheduler access exists here.
"""
from __future__ import annotations

import argparse
import csv
from io import StringIO
import json
from collections import defaultdict
from pathlib import Path
import shutil
import tempfile
from typing import Any

from congress_release_gate import (
    ReleaseGateError, _read, _jsonl, _sha,
    verify_research_package,
)
from congress_evidence_intake import _official_link_parts
from congress_adjudication import REVIEW_COLUMNS, ROLES

VERSION = 'E1.25.0'
MAX_BYTES = 20_000_000
MAX_ROWS = 499
DOC_COLUMNS = (
    'request_id', 'priority', 'expected_pdf_filename', 'candidate_house_url_unverified',
    'linked_task_count', 'linked_row_indices', 'linked_task_ids',
    'semantic_group_ids', 'action_ambiguities', 'derivative_ambiguities',
    'local_pdf_status', 'origin_authenticated', 'filing_verified',
    'ingestion_allowed', 'trading_allowed',
)
TASK_COLUMNS = (
    'task_id', 'row_index', 'review_priority', 'ticker', 'transaction_date',
    'filing_date', 'reported_action', 'reported_asset_type', 'owner_type',
    'asset_description', 'source_filing_id', 'raw_record_sha256',
    'source_snapshot_sha256', 'source_link_status', 'candidate_pdf_filename',
    'candidate_source_url_unverified', 'reason_codes', 'semantic_group_id',
    'review_status', 'reviewer_primary', 'reviewer_secondary',
    'pdf_origin_authenticated', 'official_filing_verified',
    'eligible_for_ingestion', 'eligible_for_execution', 'first_seen_at',
)
MISSING_COLUMNS = (
    'task_id', 'row_index', 'review_priority', 'ticker', 'transaction_date',
    'filing_date', 'reason_codes', 'independent_filing_lookup_needed',
    'candidate_source_url_unverified', 'official_filing_verified',
    'eligible_for_ingestion', 'eligible_for_execution',
)
GROUP_COLUMNS = (
    'semantic_group_id', 'member_count', 'row_indices', 'task_ids',
    'candidate_documents_unverified', 'missing_link_task_ids',
    'resolution_status', 'release_allowed',
)


class HandoffError(ValueError):
    """E1.25 handoff input is inconsistent or makes an unsafe claim."""


def _safe_cell(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (list, dict, tuple)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text


def _read_csv(path: Path, columns: tuple[str, ...] | None = None) -> list[dict[str, str]]:
    try:
        data = _read(path).decode('utf-8-sig')
        lines = csv.DictReader(StringIO(data, newline=""))
        actual = list(lines.fieldnames or [])
        if not actual or len(actual) != len(set(actual)):
            raise HandoffError('CSV missing or duplicate columns: ' + path.name)
        if columns is not None and actual != list(columns):
            raise HandoffError('Unexpected CSV template columns: ' + path.name)
        rows = list(lines)
        if len(rows) > MAX_ROWS or any(None in r or any(v is None for v in r.values()) for r in rows):
            raise HandoffError('Malformed or oversized CSV: ' + path.name)
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise HandoffError('Unreadable CSV: ' + path.name) from exc


def _decode_list(value: Any, name: str) -> list:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise HandoffError('Invalid JSON list in ' + name) from exc
    if not isinstance(parsed, list) or len(parsed) > MAX_ROWS:
        raise HandoffError('Expected JSON list in ' + name)
    return parsed


def _bool_false(value: Any) -> bool:
    return value in (False, 'False', 'false') and value is not None


def _source_paths(root: Path) -> dict[str, Path]:
    return {
        'e118': root / 'e118/pelosi_e118_human_review_queue.jsonl',
        'e119_template': root / 'e119/pelosi_e119_two_reviewer_template.csv',
        'e120_docs': root / 'e120/pelosi_e120_house_pdf_request_manifest.csv',
        'e120_missing': root / 'e120/pelosi_e120_missing_provenance_queue.csv',
        'e120_groups': root / 'e120/pelosi_e120_duplicate_group_evidence.csv',
    }


def build_handoff(e123_directory: str | Path) -> dict:
    """Prepare and validate a pending-only human review packet in memory."""
    root = Path(e123_directory)
    try:
        gate = verify_research_package(root)
    except (ReleaseGateError, OSError, ValueError) as exc:
        raise HandoffError(f'E1.24 hold gate refused E1.23 package: {exc}') from exc
    summary = gate['summary']
    hold_index = {r['row_index']: r for r in gate['hold_queue']}
    if summary['approved_for_ingestion_rows'] != 0 or summary['approved_for_trading_rows'] != 0:
        raise HandoffError('An upstream record was marked releasable')
    paths = _source_paths(root)
    tasks = _jsonl(paths['e118'])
    templates = _read_csv(paths['e119_template'], tuple(REVIEW_COLUMNS))
    docs = _read_csv(paths['e120_docs'])
    missing = _read_csv(paths['e120_missing'])
    groups_csv = _read_csv(paths['e120_groups'])

    if not tasks or len(tasks) != summary['quarantined_rows'] or len(tasks) > MAX_ROWS:
        raise HandoffError('E1.18 task-count conservation failed')
    by_id = {}
    for task in tasks:
        tid = task.get('task_id')
        if not isinstance(tid, str) or not tid or tid in by_id:
            raise HandoffError('Duplicate or malformed task ID')
        source = hold_index.get(task.get('row_index'))
        if not source or source['release_status'] != 'HOLD_QUARANTINED' or source['raw_record_sha256'] != task.get('raw_record_sha256'):
            raise HandoffError('Review task does not match release hold')
        if task.get('review_priority') not in {'P1', 'P2'}:
            raise HandoffError('Unknown review priority')
        by_id[tid] = task

    # A completely blank two-reviewer template is evidence that no decisions
    # have been invented. Bind both exact roles to every original task.
    slots = set()
    for slot in templates:
        tid, role = slot.get('task_id'), slot.get('role')
        if tid not in by_id or role not in ROLES or (tid, role) in slots:
            raise HandoffError('Template has unknown/duplicate reviewer slot')
        if slot.get('row_index') != str(by_id[tid]['row_index']):
            raise HandoffError('Template row index disagrees with task')
        if any(str(slot.get(field) or '').strip() for field in REVIEW_COLUMNS
               if field not in ('task_id', 'role', 'row_index')):
            raise HandoffError('Review template unexpectedly contains decisions or evidence')
        slots.add((tid, role))
    if slots != {(tid, role) for tid in by_id for role in ROLES}:
        raise HandoffError('One or more independent reviewer slots are missing')

    # Rebuild the claimed coverage from task fields, not from provider link
    # counts or self-declared E1.20 totals. A URL in this list is a *hint*.
    expected_urls: dict[str, list[dict]] = defaultdict(list)
    expected_missing = {}
    expected_groups = defaultdict(list)
    for task in tasks:
        if task.get('semantic_group_id'):
            expected_groups[task['semantic_group_id']].append(task)
        url = task.get('official_filing_url')
        if _official_link_parts(url) is None:
            expected_missing[task['task_id']] = task
        else:
            expected_urls[url].append(task)

    docs_by_url = {}
    document_rows = []
    for row in docs:
        url = row.get('source_url_unverified')
        if url in docs_by_url or url not in expected_urls:
            raise HandoffError('Document inventory added/duplicated an untrusted URL')
        parts = _official_link_parts(url)
        if parts is None or row.get('expected_local_filename') != parts[2]:
            raise HandoffError('Document filename does not match source URL')
        linked = expected_urls[url]
        tids = sorted(t['task_id'] for t in linked)
        indexes = sorted(t['row_index'] for t in linked)
        if (_decode_list(row.get('linked_task_ids'), 'linked_task_ids') != tids
                or _decode_list(row.get('linked_row_indices'), 'linked_row_indices') != indexes
                or row.get('linked_task_count') != str(len(linked))
                or row.get('review_priority') != ('P1' if any(t['review_priority'] == 'P1' for t in linked) else 'P2')):
            raise HandoffError('Document task claims contradict E1.18 source evidence')
        gids = sorted({t['semantic_group_id'] for t in linked if t.get('semantic_group_id')})
        if _decode_list(row.get('semantic_group_ids'), 'semantic_group_ids') != gids:
            raise HandoffError('Incorrect semantic groups assigned to document')
        ambiguous = sum('ambiguous_action_other' in t.get('reason_codes', []) for t in linked)
        derivative = sum('stock_label_derivative_or_exercise_description' in t.get('reason_codes', []) for t in linked)
        if row.get('action_ambiguities') != str(ambiguous) or row.get('derivative_ambiguities') != str(derivative):
            raise HandoffError('Document ambiguity counts differ from source')
        if (row.get('local_pdf_status') != 'awaiting_user_supplied_pdf'
                or row.get('local_pdf_sha256') not in ('', None)
                or not all(_bool_false(row.get(k)) for k in
                            ('pdf_origin_authenticated', 'official_filing_verified',
                             'eligible_for_ingestion', 'eligible_for_execution'))):
            raise HandoffError('Document inventory falsely claims verified evidence')
        docs_by_url[url] = row
        document_rows.append({
            'request_id': row['request_id'], 'priority': row['review_priority'],
            'expected_pdf_filename': parts[2], 'candidate_house_url_unverified': url,
            'linked_task_count': len(linked), 'linked_row_indices': indexes,
            'linked_task_ids': tids, 'semantic_group_ids': gids,
            'action_ambiguities': ambiguous, 'derivative_ambiguities': derivative,
            'local_pdf_status': 'awaiting_user_supplied_pdf',
            'origin_authenticated': False, 'filing_verified': False,
            'ingestion_allowed': False, 'trading_allowed': False,
        })
    if set(docs_by_url) != set(expected_urls):
        raise HandoffError('Document inventory omitted required candidate URLs')
    document_rows.sort(key=lambda d: (d['priority'] != 'P1', -d['linked_task_count'], d['expected_pdf_filename']))

    missing_by_id = {}
    missing_rows = []
    for row in missing:
        tid = row.get('task_id')
        if tid in missing_by_id or tid not in expected_missing:
            raise HandoffError('Unexpected/duplicate missing-source-link task')
        task = expected_missing[tid]
        if (row.get('row_index') != str(task['row_index'])
                or row.get('review_priority') != task['review_priority']
                or row.get('ticker') != (task.get('ticker') or '')
                or row.get('transaction_date') != (task.get('transaction_date') or '')
                or _decode_list(row.get('reason_codes'), 'missing reason_codes') != task.get('reason_codes', [])
                or row.get('official_filing_url') not in ('', None)
                or not all(_bool_false(row.get(k)) for k in
                            ('verified_link', 'pdf_origin_authenticated',
                             'eligible_for_ingestion', 'eligible_for_execution'))):
            raise HandoffError('Missing-provenance task incorrectly linked or promoted')
        missing_by_id[tid] = row
        missing_rows.append({
            'task_id': tid, 'row_index': task['row_index'],
            'review_priority': task['review_priority'], 'ticker': task.get('ticker'),
            'transaction_date': task.get('transaction_date'),
            'filing_date': task.get('filing_date'),
            'reason_codes': task.get('reason_codes', []),
            'independent_filing_lookup_needed': True,
            'candidate_source_url_unverified': None,
            'official_filing_verified': False, 'eligible_for_ingestion': False,
            'eligible_for_execution': False,
        })
    if set(missing_by_id) != set(expected_missing):
        raise HandoffError('Missing-provenance inventory silently dropped tasks')
    missing_rows.sort(key=lambda r: (r['review_priority'] != 'P1', r['row_index']))

    # Bind the 12 suspected duplicate groups to the *full source population*;
    # never resolve pairs merely because their rows share a PDF filename.
    group_rows = []
    seen_group_ids = set()
    for row in groups_csv:
        gid = row.get('semantic_group_id')
        if gid in seen_group_ids or gid not in expected_groups:
            raise HandoffError('Invalid/duplicate semantic group in source review plan')
        members = expected_groups[gid]
        if len(members) < 2:
            raise HandoffError('Singleton semantic duplicate group')
        row_indices = sorted(t['row_index'] for t in members)
        if (_decode_list(row.get('row_indices'), 'group row_indices') != row_indices
                or row.get('member_count') != str(len(members))
                or not _bool_false(row.get('resolved'))):
            raise HandoffError('Semantic group member accounting mismatch')
        urls = sorted({t['official_filing_url'] for t in members
                       if _official_link_parts(t.get('official_filing_url'))})
        absent = sorted(t['row_index'] for t in members
                        if _official_link_parts(t.get('official_filing_url')) is None)
        if (_decode_list(row.get('linked_source_urls_unverified'), 'group URLs') != urls
                or _decode_list(row.get('missing_link_row_indices'), 'group missing links') != absent):
            raise HandoffError('Semantic group PDF and missing-link claims mismatch')
        seen_group_ids.add(gid)
        group_rows.append({
            'semantic_group_id': gid, 'member_count': len(members),
            'row_indices': row_indices, 'task_ids': sorted(t['task_id'] for t in members),
            'candidate_documents_unverified': sorted({docs_by_url[url]['expected_local_filename'] for url in urls}),
            'missing_link_task_ids': sorted(t['task_id'] for t in members
                                            if _official_link_parts(t.get('official_filing_url')) is None),
            'resolution_status': 'unresolved_requires_independent_original_transaction_lines',
            'release_allowed': False,
        })
    if seen_group_ids != set(expected_groups):
        raise HandoffError('E1.20 semantic group queue omitted a potential duplicate')
    group_rows.sort(key=lambda r: r['semantic_group_id'])

    task_rows = []
    by_document = {url: row['expected_local_filename'] for url, row in docs_by_url.items()}
    for task in sorted(tasks, key=lambda t: t['row_index']):
        url = task.get('official_filing_url')
        has = url in by_document
        task_rows.append({
            'task_id': task['task_id'], 'row_index': task['row_index'],
            'review_priority': task['review_priority'], 'ticker': task.get('ticker'),
            'transaction_date': task.get('transaction_date'), 'filing_date': task.get('filing_date'),
            'reported_action': task.get('action_reported'),
            'reported_asset_type': task.get('asset_type_reported'),
            'owner_type': task.get('owner_type'), 'asset_description': task.get('asset_description'),
            'source_filing_id': task.get('source_filing_id'),
            'raw_record_sha256': task['raw_record_sha256'],
            'source_snapshot_sha256': summary['source_snapshot_sha256'],
            'source_link_status': 'provider_link_candidate_unverified' if has else 'missing_original_link',
            'candidate_pdf_filename': by_document[url] if has else None,
            'candidate_source_url_unverified': url if has else None,
            'reason_codes': task.get('reason_codes', []),
            'semantic_group_id': task.get('semantic_group_id'),
            'review_status': 'awaiting_two_independent_reviewers',
            'reviewer_primary': None, 'reviewer_secondary': None,
            'pdf_origin_authenticated': False, 'official_filing_verified': False,
            'eligible_for_ingestion': False, 'eligible_for_execution': False,
            'first_seen_at': None,
        })

    if (len(document_rows) != summary['requested_filing_documents']
            or len(missing_rows) != summary['missing_link_tasks']
            or len(group_rows) != summary['pending_duplicate_groups']
            or sum(r['linked_task_count'] for r in document_rows) + len(missing_rows) != len(task_rows)):
        raise HandoffError('Cross-phase request counts disagree with E1.24 release gate')
    return {
        'summary': {
            'version': VERSION, 'decision': 'NO_GO_UNVERIFIED_REVIEW_EVIDENCE',
            'source_snapshot_sha256': summary['source_snapshot_sha256'],
            'source_e123_manifest_sha256': summary['source_e123_manifest_sha256'],
            'source_e119_blank_template_sha256': _sha(_read(paths['e119_template'])),
            'total_source_records': summary['total_source_rows'],
            'clean_source_unverified_on_hold': summary['source_unverified_research_rows'],
            'quarantined_review_tasks': len(task_rows),
            'candidate_documents_to_request': len(document_rows),
            'document_linked_tasks': sum(r['linked_task_count'] for r in document_rows),
            'missing_source_link_tasks': len(missing_rows),
            'semantic_duplicate_groups_unresolved': len(group_rows),
            'two_reviewer_blank_slots': len(slots),
            'reviewer_decisions_submitted': 0, 'original_house_pdfs_authenticated': 0,
            'first_seen_at_established': False,
            'approved_for_ingestion_rows': 0, 'approved_for_trading_rows': 0,
            'external_requests': 0, 'supabase_writes': 0, 'broker_orders': 0,
            'scheduled_sync_enabled': False, 'source_authenticated': False,
        },
        'documents': document_rows, 'tasks': task_rows,
        'missing': missing_rows, 'duplicate_groups': group_rows,
        'blank_review_template_bytes': _read(paths['e119_template']),
    }


def _write_csv(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    with path.open('w', encoding='utf-8-sig', newline='') as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _safe_cell(row.get(name)) for name in fields})


def write_handoff(report: dict, destination: str | Path) -> dict:
    """Atomically publish local user-only review files; never overwrite."""
    out = Path(destination)
    if out.exists() or out.is_symlink() or not out.parent.is_dir() or out.parent.is_symlink():
        raise HandoffError('Output must be a new directory in an existing real parent')
    staged = Path(tempfile.mkdtemp(prefix='.e125-review-stage-', dir=out.parent))
    try:
        files = {
            'document_requests': staged / 'pelosi_e125_document_requests.csv',
            'task_board': staged / 'pelosi_e125_human_task_board.csv',
            'missing_links': staged / 'pelosi_e125_missing_provenance.csv',
            'duplicate_groups': staged / 'pelosi_e125_duplicate_groups.csv',
            'two_reviewer_template': staged / 'pelosi_e125_two_reviewer_template.csv',
            'manifest': staged / 'pelosi_e125_manifest.json',
        }
        for name, rows, cols in [
            ('document_requests', report['documents'], DOC_COLUMNS),
            ('task_board', report['tasks'], TASK_COLUMNS),
            ('missing_links', report['missing'], MISSING_COLUMNS),
            ('duplicate_groups', report['duplicate_groups'], GROUP_COLUMNS),
        ]:
            _write_csv(files[name], rows, cols)
        files['two_reviewer_template'].write_bytes(report['blank_review_template_bytes'])
        checksums = {path.name: _sha(_read(path)) for key, path in files.items() if key != 'manifest'}
        manifest = {**report['summary'], 'local_output_sha256': dict(sorted(checksums.items()))}
        files['manifest'].write_bytes(json.dumps(manifest, ensure_ascii=False,
                                          sort_keys=True, indent=2).encode('utf-8') + b'\n')
        staged.rename(out)
        return {'manifest': manifest,
                'paths': {key: out / file.name for key, file in files.items()}}
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('e123_directory', type=Path, help='complete, archived local E1.23 output directory')
    p.add_argument('--output-dir', type=Path, required=True,
                   help='new local review-packet folder; no network, database or PDFs')
    args = p.parse_args(argv)
    try:
        report = build_handoff(args.e123_directory)
        written = write_handoff(report, args.output_dir)
    except (HandoffError, OSError, ValueError, ReleaseGateError) as exc:
        p.exit(2, f'E1.25 NO-GO: {exc}\n')
    print(json.dumps({'decision': written['manifest']['decision'],
                      'source_rows': written['manifest']['total_source_records'],
                      'human_review_tasks': written['manifest']['quarantined_review_tasks'],
                      'filing_documents_to_request': written['manifest']['candidate_documents_to_request'],
                      'reviewer_slots': written['manifest']['two_reviewer_blank_slots'],
                      'output_directory': str(args.output_dir)}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
