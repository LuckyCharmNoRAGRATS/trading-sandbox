"""No-network E1.20 request-plan and locally supplied PDF preflight regression."""
from __future__ import annotations

import csv
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from congress_evidence_intake import (
    IntakeError, _official_link_parts, build_request_plan, write_request_plan, main,
)
from congress_adjudication import AdjudicationError, digest, canonical
from test_e119_adjudication import make_e118

URL_A = 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf'
URL_B = 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20026590.pdf'


def synthetic(tmp_path: Path, *, url_a=URL_A, url_b=None, group=False):
    folder = tmp_path / 'e118'
    make_e118(folder)
    q = folder / 'pelosi_e118_human_review_queue.jsonl'
    rows = [json.loads(x) for x in q.read_text().splitlines()]
    rows[0].update({
        'official_filing_url': url_a,
        'semantic_group_id': 'group-a' if group else None,
        'reason_codes': ['potential_semantic_duplicate'] if group else ['ambiguous_action_other'],
        'review_priority': 'P1',
        'ticker': 'NVDA',
        'transaction_date': '2026-07-24',
        'filing_date': '2026-08-24',
    })
    rows[1].update({
        'official_filing_url': url_b,
        'semantic_group_id': 'group-a' if group else None,
        'reason_codes': ['potential_semantic_duplicate', 'missing_or_untrusted_official_filing_url']
        if group else ['missing_or_untrusted_official_filing_url'],
        'review_priority': 'P2',
        'ticker': 'NVDA',
        'transaction_date': '2026-07-24',
        'filing_date': '2026-08-24',
        'suggested_evidence_url_unverified': [URL_A] if url_b is None else [],
    })
    q.write_bytes(b''.join(canonical(x) + b'\n' for x in rows))
    manifest_path = folder / 'pelosi_e118_manifest.json'
    m = json.loads(manifest_path.read_text())
    m['output_sha256'][q.name] = digest(q.read_bytes())
    manifest_path.write_text(json.dumps(m))
    return folder


def make_pdf(path: Path):
    buf = BytesIO()
    c = canvas.Canvas(buf, invariant=1, pageCompression=0)
    c.drawString(50, 700, 'SYNTHETIC PDF, NOT HOUSE FILING')
    c.showPage()
    c.save()
    path.write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


def test_provider_link_exact_path_only():
    assert _official_link_parts(URL_A) == ('2026', '20035143', '2026_20035143.pdf')
    for url in (
        'http://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf',
        'https://disclosures-clerk.house.gov.evil.test/public_disc/ptr-pdfs/2026/20035143.pdf',
        'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/../../bad.pdf',
        URL_A + '?file=evil', URL_A + '#x',
        'https://attacker@disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf',
        'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/NOTNUMERIC.pdf',
    ):
        assert _official_link_parts(url) is None, url


def test_missing_provenance_remains_missing_not_inherited(tmp_path):
    source = synthetic(tmp_path)
    r = build_request_plan(source)
    assert r['summary']['quarantined_tasks'] == 2
    assert r['summary']['linked_tasks'] == 1
    assert r['summary']['missing_or_untrusted_link_tasks'] == 1
    assert len(r['document_requests']) == 1
    assert len(r['missing_provenance_queue']) == 1
    missing = r['missing_provenance_queue'][0]
    assert missing['official_filing_url'] is None
    assert missing['suggested_evidence_url_unverified'] == [URL_A]
    assert missing['verified_link'] is False
    assert missing['eligible_for_ingestion'] is False


def test_deterministic_hash_link_and_provenance(tmp_path):
    source = synthetic(tmp_path)
    first = build_request_plan(source)
    second = build_request_plan(source)
    assert first == second
    row = first['document_requests'][0]
    assert row['source_url_unverified'] == URL_A
    assert row['expected_local_filename'] == '2026_20035143.pdf'
    assert row['request_id'].startswith('e120-')
    assert row['pdf_origin_authenticated'] is False
    assert row['local_pdf_status'] == 'awaiting_user_supplied_pdf'
    assert first['summary']['official_filing_verified'] == 0
    assert first['summary']['ingestion_approved'] is False
    assert first['summary']['trading_approved'] is False


def test_duplicate_group_preserved_never_resolved(tmp_path):
    source = synthetic(tmp_path, group=True)
    x = build_request_plan(source)
    assert len(x['duplicate_groups']) == 1
    g = x['duplicate_groups'][0]
    assert g['member_count'] == 2
    assert g['missing_link_row_indices'] == [2]
    assert g['resolved'] is False
    assert g['all_linked_documents_observed_locally'] is False


def test_distinct_linked_files_for_both_group_members(tmp_path):
    source = synthetic(tmp_path, url_b=URL_B, group=True)
    x = build_request_plan(source)
    assert x['summary']['distinct_document_requests'] == 2
    assert x['summary']['missing_or_untrusted_link_tasks'] == 0
    assert x['duplicate_groups'][0]['linked_source_url_count'] == 2
    assert x['duplicate_groups'][0]['resolved'] is False


def test_valid_local_pdf_parses_but_never_authenticates(tmp_path):
    source = synthetic(tmp_path)
    folder = tmp_path / 'pdf'
    folder.mkdir()
    sha = make_pdf(folder / '2026_20035143.pdf')
    x = build_request_plan(source, pdf_directory=folder)
    assert x['summary']['parseable_local_pdfs_origin_unverified'] == 1
    request = x['document_requests'][0]
    assert request['local_pdf_status'] == 'local_pdf_parseable_origin_unverified'
    assert request['local_pdf_sha256'] == sha
    assert request['local_pdf_page_count'] == 1
    assert request['pdf_origin_authenticated'] is False
    assert request['official_filing_verified'] is False
    assert request['eligible_for_execution'] is False


def test_pdf_magic_invalid_is_rejected_without_false_sha(tmp_path):
    source = synthetic(tmp_path)
    d = tmp_path / 'pdf'
    d.mkdir()
    (d / '2026_20035143.pdf').write_text('not a pdf')
    result = build_request_plan(source, pdf_directory=d)
    request = result['document_requests'][0]
    assert request['local_pdf_status'] == 'rejected_local_pdf_validation'
    assert request['local_pdf_sha256'] is None
    assert request['pdf_origin_authenticated'] is False


def test_symlinked_pdf_rejected(tmp_path):
    source = synthetic(tmp_path)
    d = tmp_path / 'pdf'
    d.mkdir()
    true_path = d / 'elsewhere.pdf'
    make_pdf(true_path)
    (d / '2026_20035143.pdf').symlink_to(true_path)
    item = build_request_plan(source, pdf_directory=d)['document_requests'][0]
    assert item['local_pdf_status'] == 'rejected_not_regular_file_or_symlink'


def test_unrelated_file_not_used_as_evidence(tmp_path):
    source = synthetic(tmp_path)
    d = tmp_path / 'pdf'
    d.mkdir()
    make_pdf(d / 'irrelevant.pdf')
    result = build_request_plan(source, pdf_directory=d)
    assert result['summary']['parseable_local_pdfs_origin_unverified'] == 0
    assert result['document_requests'][0]['local_pdf_status'] == 'awaiting_user_supplied_pdf'


def test_missing_pdf_directory_refused(tmp_path):
    source = synthetic(tmp_path)
    with pytest.raises(IntakeError, match='absent'):
        build_request_plan(source, pdf_directory=tmp_path / 'not-there')


def test_tampered_e118_input_rejected(tmp_path):
    source = synthetic(tmp_path)
    q = source / 'pelosi_e118_human_review_queue.jsonl'
    q.write_text(q.read_text().replace('NVDA', 'TSLA', 1))
    with pytest.raises(AdjudicationError, match='SHA-256 mismatch'):
        build_request_plan(source)


def test_broken_semantic_group_rejected(tmp_path):
    source = synthetic(tmp_path, group=True)
    q = source / 'pelosi_e118_human_review_queue.jsonl'
    rows = [json.loads(v) for v in q.read_text().splitlines()]
    rows[1]['reason_codes'] = ['missing_or_untrusted_official_filing_url']
    q.write_bytes(b''.join(canonical(x) + b'\n' for x in rows))
    m = source / 'pelosi_e118_manifest.json'
    data = json.loads(m.read_text())
    data['output_sha256'][q.name] = digest(q.read_bytes())
    m.write_text(json.dumps(data))
    with pytest.raises(IntakeError, match='semantic'):
        build_request_plan(source)


def test_files_repeatably_hash_identically(tmp_path):
    source = synthetic(tmp_path)
    x = build_request_plan(source)
    a = write_request_plan(x, tmp_path / 'first')
    b = write_request_plan(x, tmp_path / 'second')
    assert a['manifest'] == b['manifest']
    assert len(a['manifest']['local_artifact_sha256']) == 3
    for k, path in a['paths'].items():
        assert path.exists()
        if k != 'manifest':
            assert a['manifest']['local_artifact_sha256'][path.name] == digest(path.read_bytes())


def test_csv_formula_injection_prevented(tmp_path):
    source = synthetic(tmp_path)
    q = source / 'pelosi_e118_human_review_queue.jsonl'
    rows = [json.loads(x) for x in q.read_text().splitlines()]
    rows[1]['ticker'] = '=2+2'
    q.write_bytes(b''.join(canonical(x) + b'\n' for x in rows))
    mpath = source / 'pelosi_e118_manifest.json'
    manifest = json.loads(mpath.read_text())
    manifest['output_sha256'][q.name] = digest(q.read_bytes())
    mpath.write_text(json.dumps(manifest))
    results = write_request_plan(build_request_plan(source), tmp_path / 'export')
    with results['paths']['manual_provenance'].open(encoding='utf-8-sig') as f:
        row = next(csv.DictReader(f))
    assert row['ticker'] == "'=2+2"


def test_local_cli_no_network_and_zero_release(tmp_path, monkeypatch, capsys):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: (_ for _ in ()).throw(AssertionError('network attempted')))
    source = synthetic(tmp_path)
    assert main([str(source), '--output-dir', str(tmp_path / 'out')]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['summary']['requested_external_urls_accessed'] == 0
    assert printed['summary']['supabase_writes'] == 0
    assert printed['summary']['broker_orders'] == 0


def test_real_snapshot_72_tasks_optional_env():
    folder = os.environ.get('E120_E118_DIR')
    if not folder:
        pytest.skip('User-supplied data kept out of repository and CI')
    x = build_request_plan(folder)
    s = x['summary']
    assert s['source_snapshot_sha256'] == '4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3'
    assert (s['total_source_rows'], s['quarantined_tasks']) == (147, 72)
    assert (s['linked_tasks'], s['missing_or_untrusted_link_tasks']) == (51, 21)
    assert s['distinct_document_requests'] == 17
    assert s['potential_duplicate_groups'] == 12
    assert s['house_pdf_origin_authenticated'] == 0
    assert s['official_filing_verified'] == 0
    assert not s['trading_approved'] and not s['ingestion_approved']
