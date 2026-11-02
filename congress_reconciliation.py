"""E1.18 OFFLINE review queue for quarantined Tracefour Pelosi disclosures.

Consumes E1.17 immutable source records + SHA-256 manifest. Does not resolve
an original filing without a provided, independently reviewed document; does
not silently merge apparent duplicates, reclassify stock/options or actions,
retroactively create first_seen_at, import broker/database clients, or trade.

The 75 structurally clean records remain *source unverified*. No quarantined
record is promoted by this pipeline, even when another record cites a PDF URL.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from congress_remediation import _csv_safe

VERSION = 'E1.18.1'
SOURCE_FILES = {
    'clean': 'pelosi_e117_clean_research.jsonl',
    'quarantine': 'pelosi_e117_quarantined_review.jsonl',
}
SOURCE_SALE = re.compile(r'\|Sale(?:\s*\([^|]*\))?\|', re.IGNORECASE)
OPTION_EVIDENCE = re.compile(r'\b(?:calls?|puts?|options?|leaps?)\b', re.IGNORECASE)
EXERCISE_EVIDENCE = re.compile(r'\bexercis(?:e|es|ed|ing)\b', re.IGNORECASE)
HEX_SHA = re.compile(r'[0-9a-f]{64}\Z')
MAX_SOURCE_BYTES = 4_000_000
MAX_RECORDS = 499

QUEUE_COLS = [
    'task_id', 'queue_status', 'review_priority', 'row_index', 'ticker',
    'transaction_date', 'filing_date', 'owner_type', 'asset_type_reported',
    'action_reported', 'amount_label', 'source_filing_id', 'official_filing_url',
    'suggested_evidence_url_unverified', 'candidate_action_from_id_unverified',
    'candidate_asset_interpretation_unverified', 'semantic_group_id',
    'semantic_group_size', 'related_row_indices', 'reason_codes', 'review_tasks',
    'required_evidence', 'source_snapshot_sha256', 'raw_record_sha256',
    'e117_research_event_id', 'provider', 'provider_endpoint', 'attribution_url',
    'license_url', 'first_seen_at', 'official_filing_verified',
    'eligible_for_ingestion', 'eligible_for_execution'
]
GROUP_COLS = [
    'group_id', 'row_indices', 'tickers', 'transaction_dates', 'actions',
    'distinct_filing_dates', 'distinct_official_links', 'number_of_rows',
    'evidence_status', 'recommended_review', 'automatic_deduplication_allowed'
]
DECISION_COLS = [
    'task_id', 'row_index', 'semantic_group_id', 'reviewer', 'reviewed_at_utc',
    'decision', 'original_filing_pdf_sha256', 'filing_page_or_transaction',
    'review_notes', 'second_reviewer', 'second_reviewed_at_utc'
]
VALID_REASONS = {
    'missing_or_untrusted_official_filing_url',
    'upstream_timestamp_missing_or_timezone_naive',
    'ambiguous_action_other', 'source_id_suggests_sale_but_type_other',
    'stock_label_derivative_or_exercise_description',
    'potential_semantic_duplicate', 'missing_source_filing_id',
    'duplicate_source_filing_id', 'member_identity_mismatch',
    'invalid_ticker', 'unknown_asset_type', 'unknown_action_type',
    'invalid_or_inverted_dates', 'future_transaction_or_filing_date',
    'invalid_amount_bounds',
}


class ReconciliationError(ValueError):
    """Fail closed on source-data tampering or malformed evidence."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_bytes(path: Path, *, allow_empty: bool = False) -> bytes:
    if not path.is_file() or not (0 <= path.stat().st_size <= MAX_SOURCE_BYTES if allow_empty else 0 < path.stat().st_size <= MAX_SOURCE_BYTES):
        raise ReconciliationError(f'Missing/empty/oversized E1.17 evidence: {path.name}')
    return path.read_bytes()


def _load_e117(directory: Path) -> tuple[dict, dict[str, list[dict]]]:
    try:
        manifest = json.loads(_read_bytes(directory / 'pelosi_e117_summary.json'))
    except (UnicodeError, ValueError) as exc:
        raise ReconciliationError('Invalid E1.17 manifest') from exc
    if not isinstance(manifest, dict):
        raise ReconciliationError('Manifest must be an object')
    if manifest.get('status') != 'NO_GO_RESEARCH_ONLY_SOURCE_UNVERIFIED':
        raise ReconciliationError('Source is not E1.17 NO-GO research data')
    if manifest.get('total_rows', 0) > MAX_RECORDS or manifest.get('total_rows', 0) < 1:
        raise ReconciliationError('Invalid source count or 500-row boundary')
    checksums = manifest.get('output_files_sha256')
    if not isinstance(checksums, dict):
        raise ReconciliationError('Missing source checksum manifest')
    partitions: dict[str, list[dict]] = {}
    for key, filename in SOURCE_FILES.items():
        raw = _read_bytes(directory / filename, allow_empty=True)
        if checksums.get(filename) != _sha(raw):
            raise ReconciliationError(f'E1.17 source SHA-256 mismatch: {filename}')
        try:
            data = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
        except (UnicodeError, ValueError) as exc:
            raise ReconciliationError(f'Malformed E1.17 JSONL: {filename}') from exc
        if not all(isinstance(x, dict) for x in data):
            raise ReconciliationError('E1.17 JSONL contains non-object records')
        partitions[key] = data
        expected = manifest.get('output_file_rows', {}).get(filename)
        if expected != len(data):
            raise ReconciliationError(f'E1.17 row count mismatch: {filename}')
    clean, quarantine = partitions['clean'], partitions['quarantine']
    if len(clean) + len(quarantine) != manifest['total_rows']:
        raise ReconciliationError('Incomplete E1.17 row partition')
    seen = set()
    for status, rows in partitions.items():
        expected_status = ('structurally_clean_source_unverified' if status == 'clean'
                           else 'quarantined_review_required')
        for row in rows:
            index = row.get('row_index')
            if isinstance(index, bool) or not isinstance(index, int) or index in seen:
                raise ReconciliationError('Duplicate or invalid E1.17 row index')
            seen.add(index)
            if row.get('quality_status') != expected_status:
                raise ReconciliationError('E1.17 status mismatch')
            reasons = row.get('reason_codes')
            if not isinstance(reasons, list) or (bool(reasons) != (status == 'quarantine')):
                raise ReconciliationError('E1.17 quarantine reasons missing or contradictory')
            if any(reason not in VALID_REASONS for reason in reasons):
                raise ReconciliationError('Unknown E1.17 quality reason')
            source_raw = row.get('raw_record')
            if not isinstance(source_raw, dict) or row.get('raw_record_sha256') != _sha(_canonical(source_raw)):
                raise ReconciliationError('Original raw-record SHA-256 mismatch')
            if row.get('snapshot_sha256') != manifest.get('source_snapshot_sha256'):
                raise ReconciliationError('Source snapshot mismatch')
            if row.get('first_seen_at') is not None or row.get('official_filing_verified') is not False:
                raise ReconciliationError('Unjustified time or official source attestation')
            if row.get('eligible_for_execution') is not False or row.get('eligible_for_ingestion') is not False:
                raise ReconciliationError('Source claims unsafe release permission')
    if seen != set(range(1, manifest['total_rows'] + 1)):
        raise ReconciliationError('Discontinuous E1.17 row indexes')
    return manifest, partitions


def _candidate_action(row: dict) -> str:
    sid = str(row.get('source_filing_id') or '')
    if row.get('action_reported') != 'Other':
        return ''
    return 'possible_sale_unverified' if SOURCE_SALE.search(sid) else 'unknown_unverified'


def _candidate_asset(row: dict) -> str:
    text = str(row.get('asset_description') or '')
    if row.get('asset_type_reported') != 'Stock':
        return ''
    if EXERCISE_EVIDENCE.search(text):
        return 'possible_option_exercise_unverified'
    if OPTION_EVIDENCE.search(text):
        return 'possible_option_contract_unverified'
    return ''


def _priority(reasons: set[str]) -> str:
    if reasons.intersection({
        'stock_label_derivative_or_exercise_description',
        'ambiguous_action_other', 'unknown_action_type',
        'invalid_or_inverted_dates', 'future_transaction_or_filing_date',
        'member_identity_mismatch', 'duplicate_source_filing_id',
    }):
        return 'P1'
    if reasons.intersection({'potential_semantic_duplicate', 'missing_or_untrusted_official_filing_url',
                             'missing_source_filing_id', 'unknown_asset_type'}):
        return 'P2'
    return 'P3'


def _review_steps(reasons: set[str]) -> tuple[list[str], list[str]]:
    tasks = []
    evidence = ['original_house_filing_pdf_sha256', 'filing_pdf_page_or_transaction_reference']
    if 'missing_or_untrusted_official_filing_url' in reasons:
        tasks.append('locate_official_pdf_independently; do_not_inherit_link_from_other_row')
        evidence.append('independently_verified_official_house_filing_url')
    if 'ambiguous_action_other' in reasons:
        tasks.append('compare_transaction_action_with_original_pdf; do_not_assume_sale')
    if 'stock_label_derivative_or_exercise_description' in reasons:
        tasks.append('verify_equity_vs_option_vs_exercise_in_original_pdf')
    if 'potential_semantic_duplicate' in reasons:
        tasks.append('compare_all_group_filing_rows; distinct_trade_vs_amendment_vs_duplicate')
        evidence.append('all_group_original_filing_pages')
    if 'upstream_timestamp_missing_or_timezone_naive' in reasons:
        tasks.append('obtain_timezone_documentation; do_not_infer_utc')
    for x in sorted(reasons):
        if x not in {
            'missing_or_untrusted_official_filing_url', 'ambiguous_action_other',
            'stock_label_derivative_or_exercise_description', 'potential_semantic_duplicate',
            'upstream_timestamp_missing_or_timezone_naive', 'source_id_suggests_sale_but_type_other'
        }:
            tasks.append(f'review_{x}')
    return sorted(set(tasks)), sorted(set(evidence))


def reconcile_e117(directory: str | Path) -> dict:
    """Produce a human review queue. No quarantined item is automatically released.

    The queue can propose *questions and candidate interpretation strings only*.
    Absence of original House PDF bytes prevents making a factual resolution.
    """
    manifest, partitions = _load_e117(Path(directory))
    all_records = partitions['clean'] + partitions['quarantine']
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in all_records:
        if row.get('semantic_group_id'):
            groups[row['semantic_group_id']].append(row)
    for gid, members in groups.items():
        if (len(members) < 2 or any('potential_semantic_duplicate' not in row['reason_codes'] for row in members)
                or any(row['semantic_group_size'] != len(members) for row in members)):
            raise ReconciliationError(f'Broken E1.17 semantic group: {gid}')

    review_queue = []
    for row in sorted(partitions['quarantine'], key=lambda x: x['row_index']):
        reasons = set(row['reason_codes'])
        gid = row.get('semantic_group_id')
        related = [other for other in groups.get(gid, []) if other['row_index'] != row['row_index']]
        candidate_urls = sorted({other['official_filing_url'] for other in related if other.get('official_filing_url')})
        tasks, evidence = _review_steps(reasons)
        task_id = 'e118-' + _sha(_canonical({
            'snapshot': manifest['source_snapshot_sha256'], 'index': row['row_index'],
            'raw_hash': row['raw_record_sha256'], 'algorithm': VERSION,
        }))[:24]
        item = {
            'task_id': task_id, 'queue_status': 'pending_original_filing_evidence',
            'review_priority': _priority(reasons), 'row_index': row['row_index'],
            'ticker': row.get('ticker'), 'transaction_date': row.get('transaction_date'),
            'filing_date': row.get('filing_date'), 'owner_type': row.get('owner_type'),
            'asset_type_reported': row.get('asset_type_reported'),
            'asset_description': row.get('asset_description'),
            'action_reported': row.get('action_reported'),
            'amount_label': row.get('amount_label'),
            'source_filing_id': row.get('source_filing_id'),
            'official_filing_url': row.get('official_filing_url'),
            'suggested_evidence_url_unverified': candidate_urls,
            'candidate_action_from_id_unverified': _candidate_action(row),
            'candidate_asset_interpretation_unverified': _candidate_asset(row),
            'semantic_group_id': gid, 'semantic_group_size': row.get('semantic_group_size'),
            'related_row_indices': [other['row_index'] for other in related],
            'reason_codes': sorted(reasons), 'review_tasks': tasks, 'required_evidence': evidence,
            'source_snapshot_sha256': manifest['source_snapshot_sha256'],
            'raw_record_sha256': row['raw_record_sha256'],
            'e117_research_event_id': row.get('research_event_id'),
            'provider': row.get('provider'), 'provider_endpoint': row.get('provider_endpoint'),
            'attribution_url': row.get('attribution_url'), 'license_url': row.get('license_url'),
            'first_seen_at': None, 'official_filing_verified': False,
            'eligible_for_ingestion': False, 'eligible_for_execution': False,
            'raw_record': row.get('raw_record'),
        }
        review_queue.append(item)
    if len(review_queue) != manifest['quarantined_rows']:
        raise ReconciliationError('Lost quarantined records')

    group_queue = []
    for gid, members in sorted(groups.items()):
        members = sorted(members, key=lambda x: x['row_index'])
        group_queue.append({
            'group_id': gid, 'row_indices': [x['row_index'] for x in members],
            'tickers': sorted({x.get('ticker') for x in members}),
            'transaction_dates': sorted({x.get('transaction_date') for x in members}),
            'actions': sorted({x.get('action_reported') for x in members}),
            'distinct_filing_dates': sorted({x.get('filing_date') for x in members}),
            'distinct_official_links': sorted({x['official_filing_url'] for x in members if x.get('official_filing_url')}),
            'number_of_rows': len(members),
            'evidence_status': 'no_independent_original_pdf_review',
            'recommended_review': 'compare_original_documents; may_be_amendment_or_distinct_trade',
            'automatic_deduplication_allowed': False,
        })
    return {
        'summary': {
            'version': VERSION,
            'source_snapshot_sha256': manifest['source_snapshot_sha256'],
            'e117_manifest_sha256': _sha(_read_bytes(Path(directory) / 'pelosi_e117_summary.json')),
            'provider': manifest['provider'],
            'provider_attribution_url': manifest['attribution_url'],
            'license_url': manifest['license_url'],
            'total_source_rows': manifest['total_rows'],
            'clean_research_only_unverified_rows': len(partitions['clean']),
            'quarantined_human_review_rows': len(review_queue),
            'semantic_duplicate_groups_pending': len(group_queue),
            'auto_reclassified_rows': 0, 'auto_deduplicated_rows': 0,
            'source_verified_rows': 0,
            'original_pdf_bytes_reviewed': 0,
            'first_seen_at_estimated': False,
            'ingestion_approved': False, 'execution_approved': False,
            'external_http_requests': 0, 'supabase_writes': 0, 'broker_orders': 0,
            'reason_counts_overlapping': dict(sorted(Counter(code for x in review_queue for code in x['reason_codes']).items())),
            'review_priorities': dict(sorted(Counter(x['review_priority'] for x in review_queue).items())),
            'decision': 'NO_GO_EVIDENCE_REQUIRED',
        },
        'research_only': [
            {**row, 'e118_label': 'structurally_clean_official_source_unverified',
             'official_filing_verified': False, 'eligible_for_ingestion': False,
             'eligible_for_execution': False, 'first_seen_at': None}
            for row in sorted(partitions['clean'], key=lambda x: x['row_index'])
        ],
        'review_queue': review_queue,
        'group_queue': group_queue,
    }


def _csv_write(path: Path, rows: list[dict], cols: list[str]):
    with path.open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=cols, extrasaction='ignore')
        writer.writeheader()
        for item in rows:
            writer.writerow({key: _csv_safe(item.get(key)) for key in cols})


def write_outputs(result: dict, target_dir: str | Path) -> dict:
    """Write only local research outputs. No database, network, or broker IO."""
    folder = Path(target_dir)
    folder.mkdir(parents=True, exist_ok=True)
    outputs = {
        'review_queue_csv': folder / 'pelosi_e118_human_review_queue.csv',
        'review_queue_jsonl': folder / 'pelosi_e118_human_review_queue.jsonl',
        'semantic_group_csv': folder / 'pelosi_e118_semantic_duplicate_groups.csv',
        'research_jsonl': folder / 'pelosi_e118_research_only.jsonl',
        'decision_template_csv': folder / 'pelosi_e118_review_decision_template.csv',
        'manifest': folder / 'pelosi_e118_manifest.json',
    }
    _csv_write(outputs['review_queue_csv'], result['review_queue'], QUEUE_COLS)
    _csv_write(outputs['semantic_group_csv'], result['group_queue'], GROUP_COLS)
    decision_rows = [
        {'task_id': x['task_id'], 'row_index': x['row_index'],
         'semantic_group_id': x['semantic_group_id'],
         'reviewer': '', 'reviewed_at_utc': '', 'decision': '',
         'original_filing_pdf_sha256': '', 'filing_page_or_transaction': '',
         'review_notes': '', 'second_reviewer': '', 'second_reviewed_at_utc': ''}
        for x in result['review_queue']
    ]
    _csv_write(outputs['decision_template_csv'], decision_rows, DECISION_COLS)
    for output_key, rows in [('review_queue_jsonl', result['review_queue']),
                             ('research_jsonl', result['research_only'])]:
        with outputs[output_key].open('w', encoding='utf-8') as out:
            for x in rows:
                out.write(_canonical(x).decode('utf-8') + '\n')
    hashes = {path.name: _sha(path.read_bytes()) for key, path in outputs.items() if key != 'manifest'}
    manifest = {**result['summary'], 'local_output_sha256': dict(sorted(hashes.items())),
                'output_row_counts': {'human_review': len(result['review_queue']),
                                      'groups': len(result['group_queue']),
                                      'research_only': len(result['research_only'])}}
    outputs['manifest'].write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
                                   encoding='utf-8')
    return {'manifest': manifest, 'paths': outputs}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('e117_output_dir', type=Path, help='Directory with the E1.17 checksum manifest + JSONL')
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = reconcile_e117(args.e117_output_dir)
        written = write_outputs(result, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'E1.18 NO-GO: {exc}\n')
    print(json.dumps(written['manifest'], indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
