import socket

import pytest

from agents.optimizer import validate_squad
from agents.recommend import _compute_data_mode, build_recommendation


def _has_network():
    try:
        socket.create_connection(("fantasy.premierleague.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


def test_compute_data_mode_skipped_source_does_not_count_as_degraded():
    # v1.8: a deliberately-skipped source (transfer rumors outside a transfer window) must not
    # make an otherwise-clean run show as "degraded" — that would be misleading (implies a real
    # failure when nothing actually went wrong).
    status = {
        "fantasyfootballfix": {"status": "ok", "note_count": 2},
        "fantasyfootballscout": {"status": "ok", "note_count": 1},
        "bbc_gossip": {"status": "skipped", "note_count": 0},
    }
    assert _compute_data_mode(status) == "full"


def test_compute_data_mode_real_failure_still_shows_degraded():
    status = {
        "fantasyfootballfix": {"status": "ok", "note_count": 2},
        "fantasyfootballscout": {"status": "failed", "note_count": 0},
        "bbc_gossip": {"status": "skipped", "note_count": 0},
    }
    assert _compute_data_mode(status) == "degraded"


def test_compute_data_mode_all_skipped_or_ok_variants():
    assert _compute_data_mode({"a": {"status": "skipped", "note_count": 0}}) == "full"


@pytest.mark.skipif(not _has_network(), reason="no network access")
def test_build_recommendation_live_end_to_end():
    # Real gameweek right now (GW2) is before the GW3+ gate — GW3 exercises the full live
    # pipeline without waiting for the calendar, per recommend.py's own smoke-test convention.
    rec, player_table = build_recommendation(current_gw=3, force_refresh=True)

    validate_squad(rec, player_table=player_table)  # re-validated independently of build_recommendation's own internal call

    assert rec["target_gw"] == 3
    assert rec["deadline_epoch"] > 0
    assert rec["data_mode"] in {"full", "degraded", "official_only"}
    assert rec["cost_tenths"] <= 1000
    assert rec["bank_tenths"] == 1000 - rec["cost_tenths"]
    assert len(rec["research_notes_by_player"]) >= 0  # may legitimately be empty if all sources are down right now
    assert set(rec["source_status"].keys()) == {"fantasyfootballfix", "fantasyfootballscout", "bbc_gossip"}
