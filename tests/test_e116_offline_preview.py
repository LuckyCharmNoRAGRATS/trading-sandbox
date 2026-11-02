"""No-network tests for validating an already-downloaded local JSON file."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from congress_preview import PreviewError, analyze_file, main

NOW = datetime(2026, 9, 9, 19, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / 'fixtures' / 'tracefour_pelosi_synthetic.json'


def save(tmp_path, payload):
    path = tmp_path / 'saved-response.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def get_fixture():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def test_synthetic_preview_injects_clock_no_network():
    report = analyze_file(FIXTURE, now=NOW)
    assert report['status'] == 'offline_file_validated_only'
    assert report['disclosure_count'] == 1
    assert report['unique_filing_ids'] == 1
    assert report['asset_type_counts'] == {'stock': 1}
    assert report['median_disclosure_lag_days'] == 26
    assert report['supabase_writes'] == 0
    assert report['live_http_requests'] == 0
    assert report['ingestion_approved'] is False


def test_real_time_freshness_not_bypassed_by_own_timestamp():
    with pytest.raises(PreviewError, match='stale'):
        analyze_file(FIXTURE)


def test_stale_cache_rejected(tmp_path):
    payload = get_fixture()
    payload['meta']['fetchedAt'] = '2026-09-07T00:00:00Z'
    with pytest.raises(PreviewError, match='stale'):
        analyze_file(save(tmp_path, payload), now=NOW)


def test_future_cache_rejected(tmp_path):
    payload = get_fixture()
    payload['meta']['fetchedAt'] = '2026-09-09T20:00:00Z'
    with pytest.raises(PreviewError, match='future'):
        analyze_file(save(tmp_path, payload), now=NOW)


def test_malformed_json_rejected(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('<html>404 not found</html>')
    with pytest.raises(PreviewError, match='UTF-8 JSON'):
        analyze_file(path, now=NOW)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(PreviewError, match='does not exist'):
        analyze_file(tmp_path / 'missing.json', now=NOW)


def test_wrong_member_rejected(tmp_path):
    payload = get_fixture()
    payload['data']['member']['slug'] = 'someone-else'
    with pytest.raises(PreviewError, match='identity'):
        analyze_file(save(tmp_path, payload), now=NOW)


def test_duplicate_id_rejected(tmp_path):
    payload = get_fixture()
    payload['data']['trades'].append(copy.deepcopy(payload['data']['trades'][0]))
    with pytest.raises(PreviewError, match='Duplicate source filing'):
        analyze_file(save(tmp_path, payload), now=NOW)


def test_invalid_disclosure_date_rejected(tmp_path):
    payload = get_fixture()
    payload['data']['trades'][0]['disclosureDate'] = '2026-05-20'
    with pytest.raises(PreviewError, match='predates'):
        analyze_file(save(tmp_path, payload), now=NOW)


def test_option_is_reported_not_treated_equity(tmp_path):
    payload = get_fixture()
    payload['data']['trades'][0]['assetType'] = 'Stock Option'
    report = analyze_file(save(tmp_path, payload), now=NOW)
    assert report['asset_type_counts'] == {'stock option': 1}
    assert report['trading_approved'] is False


def test_500_boundary_rejected(tmp_path):
    payload = get_fixture()
    payload['data']['trades'] = [payload['data']['trades'][0]] * 500
    with pytest.raises(PreviewError, match='500-record'):
        analyze_file(save(tmp_path, payload), now=NOW)


def test_offline_cli_rejects_stale_fixture(capsys):
    with pytest.raises(SystemExit) as exc:
        main([str(FIXTURE)])
    assert exc.value.code == 1
    assert 'NOT VERIFIED' in capsys.readouterr().err


def test_invalid_sample_limit():
    with pytest.raises(ValueError, match='sample_limit'):
        analyze_file(FIXTURE, now=NOW, sample_limit=99)
