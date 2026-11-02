"""E2.1 data contract: synthetic-only, immutable and revision-aware, offline."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket

import pytest

from congress_e2_data_contract import (
    SyntheticDataset, E21ContractError, SCHEMA_VERSION, ADJUSTMENT_POLICY, SYNTHETIC,
    validate_synthetic_dataset, load_synthetic_snapshot, snapshot_asof,
    study_synthetic_snapshot,
)

BEFORE = datetime(2026, 11, 2, 7, tzinfo=timezone.utc)
ASOF = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def fixture():
    event = {"dataset_kind": SYNTHETIC, "event_id": "demo-1",
             "member": "Synthetic Example", "ticker": "DEMO", "owner": "Spouse",
             "asset_type": "Stock", "action": "Purchase",
             "first_seen_at": "2026-10-30T18:00:00Z",
             "semantic_duplicate_resolved": True, "official_filing_verified": False,
             "transaction_date": "2026-10-29", "filing_date": "2026-10-30",
             "synthetic_source_id": "fake-filing-1"}
    bars = [
        {"dataset_kind": SYNTHETIC, "ticker": "DEMO", "revision": 1,
         "bar_time": "2026-10-30T16:00:00Z", "available_at": "2026-10-30T16:01:00Z", "adjusted_close": 100.0},
        {"dataset_kind": SYNTHETIC, "ticker": "DEMO", "revision": 1,
         "bar_time": "2026-11-01T16:00:00Z", "available_at": "2026-11-01T16:01:00Z", "adjusted_close": 105.0},
        {"dataset_kind": SYNTHETIC, "ticker": "DEMO", "revision": 1,
         "bar_time": "2026-11-02T09:00:00Z", "available_at": "2026-11-02T09:01:00Z", "adjusted_close": 110.0},
    ]
    return {"schema_version": SCHEMA_VERSION, "dataset_id": "demo-dataset-001",
            "dataset_kind": SYNTHETIC, "price_adjustment_policy": ADJUSTMENT_POLICY,
            "events": [event], "bar_revisions": bars}


def good():
    return validate_synthetic_dataset(fixture())


def test_deterministic_canonical_hash_across_order_and_timestamp_format():
    first = fixture()
    second = deepcopy(first)
    second["events"][0]["first_seen_at"] = "2026-10-30T19:00:00+01:00"
    second["bar_revisions"] = list(reversed(second["bar_revisions"]))
    assert validate_synthetic_dataset(first).canonical_sha256 == validate_synthetic_dataset(second).canonical_sha256


def test_e20_study_bridge_strictly_post_observation():
    report = study_synthetic_snapshot(good(), asof=ASOF)
    assert report["e21_schema_version"] == SCHEMA_VERSION
    assert report["synthetic_events_evaluated"] == 1
    assert report["real_events_evaluated"] == 0
    assert report["rows"][0]["reference_bar_time"] == "2026-11-01T16:00:00+00:00"
    assert report["rows"][0]["illustrative_forward_price_change_fraction"] == pytest.approx(110/105 - 1)
    assert report["trading_approved"] is False
    assert report["ingestion_approved"] is False
    assert report["trade_signal"] is False
    assert report["broker_orders"] == 0
    assert report["external_http_requests"] == 0
    assert report["source_authentication_completed"] is False


def test_external_input_cannot_mutate_immutable_dataset():
    payload = fixture()
    frozen = validate_synthetic_dataset(payload)
    checksum = frozen.canonical_sha256
    payload["events"][0]["ticker"] = "EVIL"
    assert frozen.records()["events"][0]["ticker"] == "DEMO"
    copy = frozen.records()
    copy["events"][0]["ticker"] = "EVIL"
    assert frozen.records()["events"][0]["ticker"] == "DEMO"
    assert frozen.canonical_sha256 == checksum


def test_asof_hides_unavailable_bars_and_future_revisions():
    payload = fixture()
    correction = deepcopy(payload["bar_revisions"][1])
    correction.update(revision=2, adjusted_close=106, available_at="2026-11-02T08:00:00Z")
    payload["bar_revisions"].append(correction)
    dataset = validate_synthetic_dataset(payload)
    old = snapshot_asof(dataset, asof=BEFORE)
    assert len(old["bars"]) == 2
    assert next(b for b in old["bars"] if b["bar_time"].startswith("2026-11-01"))["adjusted_close"] == 105
    assert "bar_revisions" not in old
    new = snapshot_asof(dataset, asof=ASOF)
    assert next(b for b in new["bars"] if b["bar_time"].startswith("2026-11-01"))["adjusted_close"] == 106
    assert len(new["bars"]) == 3
    assert new["visible_snapshot_sha256"] != old["visible_snapshot_sha256"]


def test_latest_asof_revision_if_known_before_followup_bar():
    data = fixture()
    correction = deepcopy(data["bar_revisions"][1])
    correction.update(revision=2, adjusted_close=106, available_at="2026-11-02T08:00:00Z")
    data["bar_revisions"].append(correction)
    report = study_synthetic_snapshot(validate_synthetic_dataset(data), asof=ASOF)
    assert report["rows"][0]["illustrative_forward_price_change_fraction"] == pytest.approx(110/106 - 1)


def test_late_revision_not_retroactively_referenced_after_future_close():
    data = fixture()
    correction = deepcopy(data["bar_revisions"][1])
    correction.update(revision=2, adjusted_close=106, available_at="2026-11-02T10:00:00Z")
    data["bar_revisions"].append(correction)
    with pytest.raises(E21ContractError, match="not available before the forward bar"):
        study_synthetic_snapshot(validate_synthetic_dataset(data), asof=ASOF)


def test_newer_revision_not_visible_before_its_availability():
    data = fixture()
    correction = deepcopy(data["bar_revisions"][1])
    correction.update(revision=2, adjusted_close=106, available_at="2026-11-03T08:00:00Z")
    data["bar_revisions"].append(correction)
    report = study_synthetic_snapshot(validate_synthetic_dataset(data), asof=ASOF)
    assert report["rows"][0]["illustrative_forward_price_change_fraction"] == pytest.approx(110/105 - 1)


def test_local_file_sha_and_no_network(tmp_path, monkeypatch):
    source = tmp_path / "fixture.json"
    source.write_text(json.dumps(fixture()), encoding="utf-8")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(socket.socket, "connect", lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError("Network forbidden")))
    result = load_synthetic_snapshot(source, expected_file_sha256=checksum)
    assert result.source_file_sha256 == checksum
    assert study_synthetic_snapshot(result, asof=ASOF)["external_http_requests"] == 0


def test_invalid_source_digest_fails(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture()), encoding="utf-8")
    with pytest.raises(E21ContractError, match="SHA-256 mismatch"):
        load_synthetic_snapshot(path, expected_file_sha256="0"*64)


def test_symlink_rejected(tmp_path):
    actual = tmp_path / "real.json"
    actual.write_text(json.dumps(fixture()), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(actual)
    with pytest.raises(E21ContractError, match="regular local"):
        load_synthetic_snapshot(link)


def test_url_path_does_not_fetch():
    with pytest.raises(E21ContractError, match="regular local"):
        load_synthetic_snapshot("https://tracefour.com/v1/congress/nancy-pelosi")


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"E2.1.0-synthetic","schema_version":"other"}',encoding="utf-8")
    with pytest.raises(E21ContractError, match="Duplicate JSON"):
        load_synthetic_snapshot(path)


def test_nonfinite_json_number_rejected(tmp_path):
    path = tmp_path / "nan.json"
    path.write_text(json.dumps(fixture()).replace('105.0', 'NaN'),encoding="utf-8")
    with pytest.raises(E21ContractError, match="Nonfinite JSON"):
        load_synthetic_snapshot(path)


@pytest.mark.parametrize("key,bad",[("schema_version","E2.99"), ("dataset_kind","tracefour"),
 ("price_adjustment_policy","live_adjusted_close")])
def test_wrong_schema_origin_or_adjustment_rejected(key,bad):
    data = fixture(); data[key]=bad
    with pytest.raises(E21ContractError):
        validate_synthetic_dataset(data)


@pytest.mark.parametrize("key,bad",[("official_filing_verified",True),("official_filing_verified",None),
 ("semantic_duplicate_resolved",False),("member","Nancy Pelosi"),
 ("asset_type","Call Option"),("action","Other"),("dataset_kind","tracefour")])
def test_unverified_or_real_event_flags_refused(key,bad):
    data = fixture(); data["events"][0][key]=bad
    with pytest.raises(E21ContractError):
        validate_synthetic_dataset(data)


def test_source_link_or_provider_field_not_smuggled_in():
    data = fixture(); data["events"][0]["sourceLink"] = "https://disclosures-clerk.house.gov/..."
    with pytest.raises(E21ContractError, match="exact"):
        validate_synthetic_dataset(data)


@pytest.mark.parametrize("traded,filing,seen",[
    ("2026-11-01","2026-10-30","2026-11-02T00:00:00Z"),
    ("2026-10-29","2026-11-03","2026-11-02T00:00:00Z"),
    ("2026-10-29","2026-10-30","2026-10-29T12:00:00Z"),
])
def test_bad_transaction_filing_observation_order_rejected(traded,filing,seen):
    data = fixture(); data["events"][0].update(transaction_date=traded, filing_date=filing, first_seen_at=seen)
    with pytest.raises(E21ContractError, match="chronology"):
        validate_synthetic_dataset(data)


def test_naive_date_rejected():
    data = fixture(); data["events"][0]["first_seen_at"]="2026-10-30T18:00:00"
    with pytest.raises(E21ContractError,match="timezone-aware"):
        validate_synthetic_dataset(data)


def test_future_event_at_asof_rejected():
    with pytest.raises(E21ContractError,match="Future first-seen"):
        snapshot_asof(good(), asof=datetime(2026,10,1,tzinfo=timezone.utc))


def test_duplicate_event_id_rejected():
    data = fixture(); data["events"].append(deepcopy(data["events"][0]))
    with pytest.raises(E21ContractError,match="Duplicate event"):
        validate_synthetic_dataset(data)


def test_duplicate_semantic_event_different_id_rejected():
    data=fixture(); item=deepcopy(data["events"][0]);item["event_id"]="demo-2";data["events"].append(item)
    with pytest.raises(E21ContractError,match="Duplicate event"):
        validate_synthetic_dataset(data)


def test_duplicate_bar_revision_number_rejected():
    data=fixture();data["bar_revisions"].append(deepcopy(data["bar_revisions"][1]))
    with pytest.raises(E21ContractError,match="revision"):
        validate_synthetic_dataset(data)


def test_skipped_revision_rejected():
    data=fixture();item=deepcopy(data["bar_revisions"][1]);item.update(revision=3,available_at="2026-11-02T08:00:00Z");data["bar_revisions"].append(item)
    with pytest.raises(E21ContractError,match="revision"):
        validate_synthetic_dataset(data)


def test_revision_available_before_original_rejected():
    data=fixture();item=deepcopy(data["bar_revisions"][1]);item.update(revision=2,available_at="2026-11-01T16:00:30Z");data["bar_revisions"].append(item)
    with pytest.raises(E21ContractError,match="revision availability"):
        validate_synthetic_dataset(data)


def test_bar_availability_cannot_predate_price_time():
    data=fixture();data["bar_revisions"][1]["available_at"]="2026-11-01T15:59:00Z"
    with pytest.raises(E21ContractError,match="before its bar time"):
        validate_synthetic_dataset(data)


@pytest.mark.parametrize("value",[0,-1,True,float("nan"),float("inf"),"100"])
def test_nonfinite_or_nonpositive_price_refused(value):
    data=fixture();data["bar_revisions"][1]["adjusted_close"] = value
    with pytest.raises(E21ContractError,match="Positive finite"):
        validate_synthetic_dataset(data)


def test_boolean_revision_refused():
    data=fixture();data["bar_revisions"][1]["revision"] = True
    with pytest.raises(E21ContractError,match="Revision must"):
        validate_synthetic_dataset(data)


def test_no_database_trade_network_or_scheduler_entrypoints():
    import congress_e2_data_contract as module
    for attr in ("fetch", "connect_from_env", "submit_order", "sync", "create_client", "schedule"):
        assert not hasattr(module, attr)


def test_event_source_sha_in_visible_manifest_not_a_real_document_signature():
    result = snapshot_asof(good(), asof=ASOF)
    assert result["approved_for_trading"] is False
    assert result["approved_for_ingestion"] is False
    assert result["real_event_count"] == 0
    assert len(result["visible_snapshot_sha256"])==64


def test_all_future_bars_must_fail_closed():
    data=fixture()
    data["events"][0].update(transaction_date="2026-10-28",filing_date="2026-10-29",
                             first_seen_at="2026-10-29T18:00:00Z")
    with pytest.raises(E21ContractError,match="No synthetic market bars"):
        snapshot_asof(validate_synthetic_dataset(data), asof=datetime(2026,10,30,15,tzinfo=timezone.utc))


def test_asof_naive_rejected():
    with pytest.raises(E21ContractError,match="timezone-aware"):
        snapshot_asof(good(), asof=datetime(2026,11,2,12))


def test_invalid_horizon_from_e20_rejected():
    with pytest.raises(E21ContractError,match="forward_bars"):
        study_synthetic_snapshot(good(),asof=ASOF,forward_bars=0)


def test_manually_forged_dataset_hash_refused():
    good_dataset = good()
    forged = SyntheticDataset(good_dataset.dataset_id, "0" * 64,
                              good_dataset.source_file_sha256, good_dataset._canonical_payload)
    with pytest.raises(E21ContractError, match="provenance digest mismatch"):
        snapshot_asof(forged, asof=ASOF)


def test_manually_forged_schema_refused_even_if_hash_matches():
    valid = good()
    payload = valid.records()
    payload["events"][0]["official_filing_verified"] = True
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    forged = SyntheticDataset(valid.dataset_id, hashlib.sha256(raw).hexdigest(), None, raw)
    with pytest.raises(E21ContractError, match="must not assert"):
        snapshot_asof(forged, asof=ASOF)


def test_committed_fixture_matches_strict_contract_and_revisions():
    path=Path(__file__).resolve().parent / "fixtures/e21_synthetic_demo.json"
    dataset=load_synthetic_snapshot(path)
    assert dataset.source_file_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert study_synthetic_snapshot(dataset,asof=ASOF)["rows"][0]["illustrative_forward_price_change_fraction"] == pytest.approx(110/106-1)
