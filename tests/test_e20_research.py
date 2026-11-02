"""E2.0 isolated synthetic-only, point-in-time and hold-gate tests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

from congress_e2_research import E2GateError, synthetic_close_to_close_study, assess_real_source_hold

CUTOFF = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def event(**updates):
    x = {
        "dataset_kind": "synthetic_fixture_only", "event_id": "demo-1",
        "member": "Synthetic Example", "ticker": "DEMO", "owner": "Spouse",
        "asset_type": "Stock", "action": "Purchase",
        "first_seen_at": "2026-10-30T18:00:00Z",
        "semantic_duplicate_resolved": True, "official_filing_verified": False,
    }
    x.update(updates)
    return x


def prices(**overrides):
    out = [
        {"dataset_kind": "synthetic_fixture_only", "ticker": "DEMO",
         "bar_time": "2026-10-30T16:00:00Z", "available_at": "2026-10-30T16:01:00Z", "adjusted_close": 100.},
        {"dataset_kind": "synthetic_fixture_only", "ticker": "DEMO",
         "bar_time": "2026-11-01T16:00:00Z", "available_at": "2026-11-01T16:01:00Z", "adjusted_close": 105.},
        {"dataset_kind": "synthetic_fixture_only", "ticker": "DEMO",
         "bar_time": "2026-11-02T09:00:00Z", "available_at": "2026-11-02T09:01:00Z", "adjusted_close": 110.},
    ]
    for index, fields in overrides.items():
        out[int(index)].update(fields)
    return out


def run(xs=None, bs=None, **kw):
    return synthetic_close_to_close_study(xs if xs is not None else [event()],
                                          bs if bs is not None else prices(),
                                          asof=CUTOFF, **kw)


def test_only_post_public_observation_closes_used():
    result = run()
    assert result["status"] == "SYNTHETIC_EDUCATIONAL_EVENT_STUDY_ONLY"
    assert result["synthetic_events_evaluated"] == 1
    assert result["real_events_evaluated"] == 0
    assert result["rows"][0]["reference_bar_time"] == "2026-11-01T16:00:00+00:00"
    assert result["rows"][0]["illustrative_forward_price_change_fraction"] == pytest.approx(110/105 - 1)
    assert result["rows"][0]["owner_type"] == "Spouse"
    assert result["rows"][0]["trade_signal"] is False
    assert result["broker_orders"] == 0


def test_real_provider_row_never_accepted_as_synthetic_event():
    with pytest.raises(E2GateError, match="synthetic fixture"):
        run([event(dataset_kind="tracefour")])


def test_real_provider_prices_never_accepted():
    p = prices()
    p[0]["dataset_kind"] = "alpaca"
    with pytest.raises(E2GateError, match="synthetic fixture"):
        run(bs=p)


def test_unverified_original_observation_is_not_inferred_from_filing_date():
    with pytest.raises(E2GateError, match="first_seen_at"):
        run([event(first_seen_at=None)])


def test_naive_first_seen_rejected():
    with pytest.raises(E2GateError, match="timezone-aware"):
        run([event(first_seen_at="2026-10-30T18:00:00")])


def test_asof_naive_rejected():
    with pytest.raises(E2GateError, match="timezone-aware"):
        synthetic_close_to_close_study([event()], prices(), asof=datetime(2026, 11, 2))


def test_future_first_seen_rejected():
    with pytest.raises(E2GateError, match="after asof"):
        run([event(first_seen_at="2027-01-01T00:00:00Z")])


@pytest.mark.parametrize("bad_asset,bad_action",[("Stock Option","Purchase"),("Stock","Other"),("Bond","Purchase")])
def test_non_equity_or_ambiguous_action_rejected(bad_asset,bad_action):
    with pytest.raises(E2GateError, match="common stock"):
        run([event(asset_type=bad_asset,action=bad_action)])


def test_real_verification_claim_rejected():
    with pytest.raises(E2GateError, match="real verified filing"):
        run([event(official_filing_verified=True)])


def test_duplicate_resolution_required():
    with pytest.raises(E2GateError, match="duplicate"):
        run([event(semantic_duplicate_resolved=False)])


def test_duplicate_ids_not_silently_deduplicated():
    with pytest.raises(E2GateError, match="duplicate"):
        run([event(),event()])


def test_duplicate_bar_times_rejected():
    xs=prices(); xs.append(deepcopy(xs[1]))
    with pytest.raises(E2GateError, match="Duplicate synthetic"):
        run(bs=xs)


def test_price_availability_before_price_timestamp_rejected():
    with pytest.raises(E2GateError, match="availability"):
        run(bs=prices(**{"1":{"available_at":"2026-11-01T15:59:00Z"}}))


def test_unavailable_future_bar_is_not_retrospectively_used():
    with pytest.raises(E2GateError, match="asof"):
        run(bs=prices(**{"2":{"available_at":"2026-11-03T00:00:00Z"}}))


@pytest.mark.parametrize("value",[0,-20,float("nan"),float("inf"),"110"])
def test_price_requires_positive_finite_numeric(value):
    with pytest.raises(E2GateError, match="positive finite"):
        run(bs=prices(**{"1":{"adjusted_close":value}}))


def test_late_reference_bar_revision_does_not_create_lookahead():
    revisions = prices(**{"1": {"available_at": "2026-11-02T10:00:00Z"}})
    with pytest.raises(E2GateError, match="not available before the forward bar"):
        run(bs=revisions)


def test_requires_follow_up_prices_not_fabricated():
    with pytest.raises(E2GateError, match="Insufficient"):
        run(bs=prices()[:2])


def test_rejects_invalid_horizon_and_boolean():
    for v in (0,21,True,1.5):
        with pytest.raises(E2GateError, match="forward_bars"):
            run(forward_bars=v)


def test_no_execution_or_network_modules_exposed():
    import congress_e2_research
    for attr in ("submit_order","fetch_trades","fetch_tracefour","connect_from_env","create_client","send_order"):
        assert not hasattr(congress_e2_research,attr)


def test_real_archived_hold_optional():
    root=os.environ.get("E20_ARCHIVED_E123_DIR")
    if not root:
        pytest.skip("Private user source E1.23 research package not committed to public CI")
    report=assess_real_source_hold(Path(root))
    assert report["real_disclosure_records_observed"]==147
    assert report["unverified_source_records_held"]==147
    assert report["real_event_study_events_eligible"]==0
    assert report["evidence_review_tasks_pending"]==72
    assert report["unresolved_duplicate_groups"]==12
    assert report["source_document_requests"]==17
    assert report["missing_link_review_tasks"]==21
    assert report["source_snapshot_sha256"]=="4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3"
