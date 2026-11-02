"""Synthetic, offline E1.19 adjudication test suite."""
from __future__ import annotations

from io import BytesIO
import csv
import hashlib
import json
from pathlib import Path
import os

from reportlab.pdfgen import canvas
import pytest

from congress_adjudication import (
    AdjudicationError, adjudicate, canonical, digest, load_e118,
    verify_ledger, write_outputs, read_review_csv, main,
)

EXCERPT = 'NVDA Purchase 2026-07-24 500001 to 1000000 stock shares'


def make_pdf(folder: Path, *, text=EXCERPT, pages=1) -> tuple[str, str]:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792), pageCompression=0, invariant=1)
    for i in range(pages):
        pdf.drawString(50, 700, text if i == 0 else 'Page two contains a different trade')
        pdf.showPage()
    pdf.save()
    filename = 'synthetic-not-official.pdf'
    data = buffer.getvalue()
    (folder / filename).write_bytes(data)
    return filename, hashlib.sha256(data).hexdigest()


def raw(ticker='NVDA'):
    return {'memberName': 'Nancy Pelosi', 'ticker': ticker,
            'sourceLink': 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/test.pdf',
            'transactionDate': '2026-07-24', 'disclosureDate': '2026-08-24'}


def task(idx=1, ticker='NVDA', gid='group-a'):
    source = raw(ticker)
    return {
        'task_id': f'e118-{idx}', 'row_index': idx,
        'queue_status': 'pending_original_filing_evidence',
        'snapshot_sha256': 'a' * 64,
        'raw_record': source, 'raw_record_sha256': digest(canonical(source)),
        'semantic_group_id': gid, 'source_filing_id': f'filing-{idx}',
        'transaction_date': '2026-07-24', 'ticker': ticker,
        'eligible_for_ingestion': False, 'eligible_for_execution': False,
        'official_filing_verified': False, 'first_seen_at': None,
    }


def clean(idx=3):
    return {'row_index': idx, 'first_seen_at': None,
            'official_filing_verified': False, 'eligible_for_execution': False,
            'eligible_for_ingestion': False}


def make_e118(folder: Path, *, nqueue=2, nclean=1):
    folder.mkdir(parents=True, exist_ok=True)
    queue = [task(i) for i in range(1, nqueue + 1)]
    research = [clean(nqueue + i) for i in range(1, nclean + 1)]
    q = folder / 'pelosi_e118_human_review_queue.jsonl'
    q.write_bytes(b''.join(canonical(x) + b'\n' for x in queue))
    p = folder / 'pelosi_e118_research_only.jsonl'
    p.write_bytes(b''.join(canonical(x) + b'\n' for x in research))
    groups = folder / 'pelosi_e118_semantic_duplicate_groups.csv'
    groups.write_text('group_id,number_of_rows\ngroup-a,2\n', encoding='utf-8')
    manifest = {
        'source_rows': len(queue) + len(research),
        'source_sha256': 'a' * 64,
        'clean_unverified': len(research), 'review_queue': len(queue),
        'original_pdf_files_reviewed': 0, 'auto_promotions': 0,
        'approval_for_ingestion': False, 'approval_for_trading': False,
        'output_sha256': {file.name: digest(file.read_bytes()) for file in [q, p, groups]},
    }
    (folder / 'pelosi_e118_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return queue


def decision(task_id='e118-1', role='primary', reviewer='analyst1', **kwargs):
    x = {
        'task_id': task_id, 'row_index': int(task_id.rsplit('-', 1)[1]),
        'role': role, 'reviewer_id': reviewer,
        'reviewed_at_utc': '2026-11-02T09:00:00Z',
        'pdf_filename': 'synthetic-not-official.pdf',
        'pdf_sha256': '', 'page_number': 1,
        'transaction_excerpt': EXCERPT,
        'action_verdict': 'buy', 'asset_verdict': 'stock',
        'duplicate_verdict': 'unknown', 'notes': 'synthetic PDF check only',
    }
    x.update(kwargs)
    return x


def checked(tmp_path):
    src = tmp_path / 'src'
    docs = tmp_path / 'pdfs'
    docs.mkdir()
    make_e118(src)
    name, sha = make_pdf(docs)
    first = decision(pdf_sha256=sha)
    second = decision(role='secondary', reviewer='analyst2', pdf_sha256=sha)
    return src, docs, first, second


def test_no_pdf_or_reviews_keeps_all_tasks_pending(tmp_path):
    src, docs, first, second = checked(tmp_path)
    r = adjudicate(src, [], docs)
    assert r['summary']['review_task_count'] == 2
    assert r['summary']['review_events'] == 0
    assert r['summary']['status_counts'] == {'awaiting_two_reviewers': 2}
    assert r['summary']['verified_trading_signals'] == 0
    assert r['summary']['source_authenticated_pdf_count'] == 0
    assert r['summary']['ingestion_approved'] is False
    assert r['summary']['execution_approved'] is False


def test_one_valid_local_pdf_review_waits_for_second(tmp_path):
    src, docs, one, second = checked(tmp_path)
    r = adjudicate(src, [one], docs)
    assert r['summary']['review_events'] == 1
    assert r['summary']['status_counts']['awaiting_second_reviewer'] == 1
    assert r['ledger'][0]['evidence']['excerpt_present_in_pdf_page'] is True
    assert r['ledger'][0]['evidence']['pdf_origin_authenticated'] is False
    assert r['summary']['source_authenticated_pdf_count'] == 0


def test_two_distinct_reviewers_concordant_only_offline(tmp_path):
    src, docs, one, second = checked(tmp_path)
    r = adjudicate(src, [one, second], docs)
    assert r['summary']['status_counts']['two_reviewers_concordant_offline_only'] == 1
    assert r['summary']['duplicate_groups_pending_evidence'] == 1
    assert r['review_queue'][0]['official_filing_verified'] is False
    assert r['review_queue'][0]['eligible_for_execution'] is False
    assert r['summary']['auto_promoted'] == 0
    assert verify_ledger(r['ledger'], genesis_hash=r['summary']['genesis_hash']) == r['summary']['ledger_head_hash']


def test_duplicate_reviewer_identity_case_insensitive_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    second['reviewer_id'] = 'ANALYST1'
    with pytest.raises(AdjudicationError, match='share a reviewer identity'):
        adjudicate(src, [one, second], docs)


def test_duplicate_role_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    second['role'] = 'primary'
    with pytest.raises(AdjudicationError, match='Duplicate reviewer role'):
        adjudicate(src, [one, second], docs)


def test_disagreement_is_explicit_not_autocorrected(tmp_path):
    src, docs, one, second = checked(tmp_path)
    second['duplicate_verdict'] = 'same_transaction'
    r = adjudicate(src, [one, second], docs)
    assert r['summary']['status_counts']['reviewer_disagreement'] == 1
    assert r['summary']['auto_promoted'] == 0


def test_pdf_hash_mismatch_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    one['pdf_sha256'] = '0' * 64
    with pytest.raises(AdjudicationError, match='do not match'):
        adjudicate(src, [one], docs)


def test_pdf_missing_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    (docs / one['pdf_filename']).unlink()
    with pytest.raises(AdjudicationError, match='Missing'):
        adjudicate(src, [one], docs)


def test_non_pdf_filename_and_traversal_block(tmp_path):
    src, docs, one, second = checked(tmp_path)
    for path in ['../secret.pdf', '/tmp/any.pdf', 'bad.txt']:
        one['pdf_filename'] = path
        with pytest.raises(AdjudicationError, match='basename'):
            adjudicate(src, [one], docs)


def test_pdf_page_reference_invalid_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    one['page_number'] = 99
    with pytest.raises(AdjudicationError, match='outside'):
        adjudicate(src, [one], docs)


def test_fabricated_transaction_excerpt_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    one['transaction_excerpt'] = 'NVDA a purchase on an invented date 2029-01-01'
    with pytest.raises(AdjudicationError, match='not found'):
        adjudicate(src, [one], docs)


def test_wrong_pdf_page_with_valid_excerpt_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    name, sha = make_pdf(docs, pages=2)
    one['pdf_sha256'] = sha
    one['page_number'] = 2
    with pytest.raises(AdjudicationError, match='not found'):
        adjudicate(src, [one], docs)


def test_pdf_excerpt_without_matching_asset_identity_rejected(tmp_path):
    src, docs, one, second = checked(tmp_path)
    text = 'MSFT Purchase 2026-07-24 500001 to 1000000 stock shares'
    _, sha = make_pdf(docs, text=text)
    one['transaction_excerpt'] = text
    one['pdf_sha256'] = sha
    with pytest.raises(AdjudicationError, match='task ticker'):
        adjudicate(src, [one], docs)


def test_pdf_excerpt_without_source_transaction_date_rejected(tmp_path):
    src, docs, one, second = checked(tmp_path)
    text = 'NVDA Purchase 2026-08-01 500001 to 1000000 stock shares'
    _, sha = make_pdf(docs, text=text)
    one['transaction_excerpt'] = text
    one['pdf_sha256'] = sha
    with pytest.raises(AdjudicationError, match='transaction date'):
        adjudicate(src, [one], docs)


def test_unsupported_verdict_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    one['asset_verdict'] = 'auto_buy'
    with pytest.raises(AdjudicationError, match='Unknown asset_verdict'):
        adjudicate(src, [one], docs)


def test_unknown_task_and_edited_row_index_block(tmp_path):
    src, docs, one, second = checked(tmp_path)
    one['task_id'] = 'e118-invented'
    with pytest.raises(AdjudicationError, match='unknown'):
        adjudicate(src, [one], docs)
    one['task_id'] = 'e118-1'
    one['row_index'] = 2
    with pytest.raises(AdjudicationError, match='row index mismatch'):
        adjudicate(src, [one], docs)


def test_naive_reviewer_timestamp_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    one['reviewed_at_utc'] = '2026-11-02T09:00:00'
    with pytest.raises(AdjudicationError, match='explicit timezone'):
        adjudicate(src, [one], docs)


def test_e118_source_hash_tamper_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    f = src / 'pelosi_e118_human_review_queue.jsonl'
    f.write_bytes(f.read_bytes() + b'\n')
    with pytest.raises(AdjudicationError, match='SHA-256 mismatch'):
        adjudicate(src, [], docs)


def test_manifest_research_file_hash_tamper_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    f = src / 'pelosi_e118_research_only.jsonl'
    f.write_text(f.read_text().replace('"row_index":3', '"row_index":4'))
    with pytest.raises(AdjudicationError, match='SHA-256 mismatch'):
        load_e118(src)


def test_tampered_ledger_link_or_evidence_blocks(tmp_path):
    src, docs, one, second = checked(tmp_path)
    r = adjudicate(src, [one, second], docs)
    altered = json.loads(json.dumps(r['ledger']))
    altered[1]['previous_hash'] = '0' * 64
    with pytest.raises(AdjudicationError, match='Broken ledger'):
        verify_ledger(altered, genesis_hash=r['summary']['genesis_hash'])
    altered = json.loads(json.dumps(r['ledger']))
    altered[0]['evidence']['asset_verdict'] = 'option'
    with pytest.raises(AdjudicationError, match='event hash mismatch'):
        verify_ledger(altered, genesis_hash=r['summary']['genesis_hash'])


def test_ledger_order_independent_of_decision_input_order(tmp_path):
    src, docs, one, second = checked(tmp_path)
    a = adjudicate(src, [one, second], docs)
    b = adjudicate(src, [second, one], docs)
    assert a == b
    assert [x['evidence']['role'] for x in a['ledger']] == ['primary', 'secondary']


def test_output_files_deterministic_and_blank_template(tmp_path):
    src, docs, one, second = checked(tmp_path)
    result = adjudicate(src, [], docs)
    a = write_outputs(result, tmp_path / 'out-a')
    b = write_outputs(result, tmp_path / 'out-b')
    assert a['manifest']['output_sha256'] == b['manifest']['output_sha256']
    with a['paths']['template'].open('r', encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    assert set(x['role'] for x in rows) == {'primary', 'secondary'}
    assert all(x['reviewer_id'] == '' and x['pdf_sha256'] == '' for x in rows)
    assert a['paths']['ledger'].read_bytes() == b''
    assert a['manifest']['output_sha256'][a['paths']['ledger'].name] == digest(b'')


def test_cli_without_evidence_creates_pending_queue(tmp_path, capsys):
    src, docs, one, second = checked(tmp_path)
    assert main([str(src), '--output-dir', str(tmp_path / 'output')]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['summary']['review_task_count'] == 2
    assert out['summary']['review_events'] == 0


def test_generated_blank_csv_roundtrips_without_fake_decisions(tmp_path, capsys):
    src, docs, one, second = checked(tmp_path)
    output = write_outputs(adjudicate(src, [], docs), tmp_path / 'templates')
    assert read_review_csv(output['paths']['template']) == []
    assert main([str(src), '--reviews-csv', str(output['paths']['template']),
                 '--output-dir', str(tmp_path / 'roundtrip')]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['summary']['review_events'] == 0
    assert result['summary']['review_task_count'] == 2


def test_completed_csv_review_roundtrips_with_real_pdf_bytes(tmp_path, capsys):
    src, docs, one, second = checked(tmp_path)
    blank = write_outputs(adjudicate(src, [], docs), tmp_path / 'template')
    with blank['paths']['template'].open('r', encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    rows[0].update({k: str(v) for k,v in one.items()})
    with blank['paths']['template'].open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    assert main([str(src), '--reviews-csv', str(blank['paths']['template']),
                 '--pdf-dir', str(docs), '--output-dir', str(tmp_path / 'reviewed')]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['summary']['review_events'] == 1
    assert result['summary']['source_authenticated_pdf_count'] == 0


def test_partly_completed_csv_review_never_silently_disappears(tmp_path):
    src, docs, one, second = checked(tmp_path)
    blank = write_outputs(adjudicate(src, [], docs), tmp_path / 'template')
    with blank['paths']['template'].open('r', encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    rows[0]['reviewer_id'] = 'analyst1'
    with blank['paths']['template'].open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(AdjudicationError, match='requires integer row/page'):
        read_review_csv(blank['paths']['template'])


def test_cli_refuses_reviews_without_pdf_directory(tmp_path):
    src, docs, one, second = checked(tmp_path)
    decisions_file = tmp_path / 'decisions.json'
    decisions_file.write_text(json.dumps({'reviews': [one]}))
    with pytest.raises(SystemExit) as exc:
        main([str(src), '--reviews-json', str(decisions_file), '--output-dir', str(tmp_path / 'result')])
    assert exc.value.code == 2


def test_no_outbound_network_during_run(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('NETWORK')))
    src, docs, one, second = checked(tmp_path)
    r = adjudicate(src, [one, second], docs)
    assert r['summary']['external_fetches'] == 0
    assert r['summary']['supabase_writes'] == 0
    assert r['summary']['broker_orders'] == 0


def test_real_e118_quarantine_regression_opt_in():
    location = os.environ.get('E119_E118_DIR')
    if not location:
        pytest.skip('Private user dataset intentionally absent from repository CI')
    manifest, queue = load_e118(location)
    assert len(queue) == 72
    r = adjudicate(location, [], location)
    assert r['summary']['review_task_count'] == 72
    assert r['summary']['status_counts'] == {'awaiting_two_reviewers': 72}
    assert r['summary']['duplicate_groups_pending_evidence'] == 12
    assert r['summary']['source_snapshot_sha256'] == '4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3'
