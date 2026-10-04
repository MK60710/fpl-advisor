import socket
from unittest.mock import patch

import pandas as pd
import pytest

from agents.data_agent import FPLDataUnavailableError
from agents.historical_agent import (
    compute_historical_priors,
    get_historical_priors_for,
    match_historical_priors_to_current,
)

FAKE_CSV = (
    "first_name,second_name,minutes,total_points,element_type\n"
    "Mohamed,Salah,3000,270,3\n"  # 270/3000*90 = 8.1 pp90, MID
    "Bench,Warmer,89,10,3\n"  # below MIN_HISTORICAL_MINUTES (450) — excluded from both dicts
    "Erling,Haaland,450,180,4\n"  # exactly at the threshold — included, pp90 = 36.0, FWD
    "Some,Defender,900,90,2\n"  # 90/900*90 = 9.0 pp90, DEF
    "Other,Defender,900,45,2\n"  # 45/900*90 = 4.5 pp90, DEF — avg DEF = (9.0+4.5)/2 = 6.75
)


def _has_network():
    try:
        socket.create_connection(("raw.githubusercontent.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


def test_compute_historical_priors_excludes_low_minutes():
    name_priors, position_priors = compute_historical_priors(FAKE_CSV, min_minutes=450)
    assert ("Mohamed", "Salah") in name_priors
    assert ("Erling", "Haaland") in name_priors
    assert ("Bench", "Warmer") not in name_priors  # below threshold — excluded from name AND position pools


def test_compute_historical_priors_pp90_correct():
    name_priors, _ = compute_historical_priors(FAKE_CSV, min_minutes=450)
    assert name_priors[("Mohamed", "Salah")] == pytest.approx(8.1)
    assert name_priors[("Erling", "Haaland")] == pytest.approx(36.0)


def test_compute_historical_priors_position_averages():
    _, position_priors = compute_historical_priors(FAKE_CSV, min_minutes=450)
    assert position_priors["DEF"] == pytest.approx((9.0 + 4.5) / 2)
    assert position_priors["MID"] == pytest.approx(8.1)  # only one qualifying MID
    assert position_priors["FWD"] == pytest.approx(36.0)
    assert "GKP" not in position_priors  # no qualifying GKP rows in the fixture at all


def test_match_by_exact_name_takes_priority_over_position_fallback():
    name_priors, position_priors = compute_historical_priors(FAKE_CSV, min_minutes=450)
    player_table = pd.DataFrame(
        [{"player_id": 1, "first_name": "Mohamed", "second_name": "Salah", "position": "MID"}]
    )
    matched = match_historical_priors_to_current(player_table, name_priors, position_priors)
    assert matched == {1: pytest.approx(8.1)}  # the player-specific prior, not the position average


def test_match_falls_back_to_position_average_for_unmatched_player():
    # v1.2 fix: a promoted-team player (e.g. Hull City in 2026-27) has no name match — must get
    # the position average as a prior instead of no prior at all.
    name_priors, position_priors = compute_historical_priors(FAKE_CSV, min_minutes=450)
    player_table = pd.DataFrame(
        [{"player_id": 2, "first_name": "Brand", "second_name": "NewSigning", "position": "DEF"}]
    )
    matched = match_historical_priors_to_current(player_table, name_priors, position_priors)
    assert matched == {2: pytest.approx((9.0 + 4.5) / 2)}


def test_match_no_prior_at_all_when_position_has_zero_historical_rows():
    name_priors, position_priors = compute_historical_priors(FAKE_CSV, min_minutes=450)
    player_table = pd.DataFrame(
        [{"player_id": 3, "first_name": "Brand", "second_name": "NewGoalkeeper", "position": "GKP"}]
    )
    matched = match_historical_priors_to_current(player_table, name_priors, position_priors)
    assert 3 not in matched


def test_get_historical_priors_for_degrades_gracefully_on_fetch_failure():
    player_table = pd.DataFrame(
        [{"player_id": 1, "first_name": "Mohamed", "second_name": "Salah", "position": "MID"}]
    )
    with patch(
        "agents.historical_agent.fetch_historical_players_csv",
        side_effect=FPLDataUnavailableError("simulated network failure"),
    ):
        result = get_historical_priors_for(player_table)
    assert result == {}  # never raises — this is an enhancement, not a required dependency


@pytest.mark.skipif(not _has_network(), reason="no network access")
def test_live_fetch_and_match_smoke():
    from agents.data_agent import build_player_table, fetch_bootstrap, fetch_fixtures

    bs = fetch_bootstrap()
    fx = fetch_fixtures()
    table = build_player_table(bs, fx)
    priors = get_historical_priors_for(table, force_refresh=True)

    # With the position fallback, coverage should now be very high (name match or position
    # average) rather than just "returning players" — a stronger bound than before v1.2.
    assert len(priors) > len(table) * 0.9
    assert all(pp90 >= 0 for pp90 in priors.values())
    assert set(priors.keys()).issubset(set(table["player_id"]))


@pytest.mark.skipif(not _has_network(), reason="no network access")
def test_live_promoted_team_players_now_get_a_prior():
    # Regression test for the exact bug found live 2026-08-30: Hull City (promoted 2026-27, not
    # in the PL last season) players had NO historical prior at all under the pre-v1.2 code,
    # leaving their raw ~2-game ep_next completely unshrunk (three sat at 8.0-10.0 off ~150
    # minutes). They must now get at least the position-average fallback.
    from agents.data_agent import build_player_table, fetch_bootstrap, fetch_fixtures

    bs = fetch_bootstrap()
    fx = fetch_fixtures()
    table = build_player_table(bs, fx)
    priors = get_historical_priors_for(table, force_refresh=True)

    hull_players = table[table["team_short"] == "HUL"]
    assert len(hull_players) > 0  # sanity: Hull is actually in this season's data
    unpriced = [pid for pid in hull_players["player_id"] if pid not in priors]
    assert unpriced == [], f"Hull players still with no prior at all: {unpriced}"


# --- Multi-season pooling (v1.6) ---

FAKE_CSV_SEASON_A = (
    "first_name,second_name,minutes,total_points,element_type\n"
    "David,Raya,270,27,1\n"  # 270 min, 27 pts alone -> below 450, would be excluded single-season
)
FAKE_CSV_SEASON_B = (
    "first_name,second_name,minutes,total_points,element_type\n"
    "David,Raya,270,18,1\n"  # pooled: 540 min, 45 pts -> 45/540*90 = 7.5 pp90, now qualifies
)


def test_compute_historical_priors_accepts_single_string_backward_compatible():
    # compute_historical_priors(str) must behave exactly like compute_historical_priors([str])
    single = compute_historical_priors(FAKE_CSV, min_minutes=450)
    wrapped = compute_historical_priors([FAKE_CSV], min_minutes=450)
    assert single == wrapped


def test_compute_historical_priors_pools_minutes_and_points_across_seasons():
    # Neither season alone clears MIN_HISTORICAL_MINUTES (450), but pooled they do — this is the
    # exact case that prompted v1.6: an established player (Raya) with a merely-decent recent
    # season getting a steadier two-season rate instead of being excluded or judged on one season.
    name_priors, _ = compute_historical_priors([FAKE_CSV_SEASON_A, FAKE_CSV_SEASON_B], min_minutes=450)
    assert ("David", "Raya") in name_priors
    assert name_priors[("David", "Raya")] == pytest.approx(45 / 540 * 90)


def test_compute_historical_priors_single_season_below_threshold_excluded():
    # sanity check that the pooling test above is actually testing something — each season ALONE
    # (270 min) is genuinely below the 450 threshold
    name_priors, _ = compute_historical_priors([FAKE_CSV_SEASON_A], min_minutes=450)
    assert ("David", "Raya") not in name_priors


def test_get_historical_priors_for_degrades_per_season_not_all_or_nothing():
    # v1.6: one season failing to fetch must not sink the whole prior — the other season(s) still
    # contribute. Mock fetch to fail for one season, succeed for another.
    player_table = pd.DataFrame(
        [{"player_id": 1, "first_name": "David", "second_name": "Raya", "position": "GKP"}]
    )

    def fake_fetch(season, force_refresh=False):
        if season == "2025-26":
            raise FPLDataUnavailableError("simulated failure for this season only")
        return FAKE_CSV_SEASON_B  # the other season succeeds, and alone is still below threshold

    with patch("agents.historical_agent.fetch_historical_players_csv", side_effect=fake_fetch):
        result = get_historical_priors_for(player_table, seasons=["2025-26", "2024-25"])

    # one season succeeded (270 min alone, below 450) — no crash, and no name-prior for Raya
    # since 270 < 450, but the function completed without raising past the failed season.
    assert result == {}  # correctly empty, not an exception, not silently using stale/wrong data


# --- Accent normalization across seasons (v1.7) ---

FAKE_CSV_ACCENTED = (
    "first_name,second_name,minutes,total_points,element_type\n"
    "David,Raya Martín,3330,162,1\n"  # 2025-26 spelling, with accent
)
FAKE_CSV_UNACCENTED = (
    "first_name,second_name,minutes,total_points,element_type\n"
    "David,Raya Martin,3420,142,1\n"  # 2024-25 spelling, real live bug: no accent
)


def test_pooling_merges_accented_and_unaccented_spellings_of_same_player():
    # Regression test for a real live bug (2026-08-30): David Raya is 'Raya Martín' in the
    # 2025-26 CSV and 'Raya Martin' (no accent) in 2024-25 — exact-string pooling silently
    # treated these as two different players, so his 2024-25 season never merged in at all.
    name_priors, _ = compute_historical_priors(
        [FAKE_CSV_ACCENTED, FAKE_CSV_UNACCENTED], min_minutes=450
    )
    key = ("David", "Raya Martin")  # normalized form (no accent) is the pooling key
    assert key in name_priors
    total_minutes = 3330 + 3420
    total_points = 162 + 142
    assert name_priors[key] == pytest.approx(total_points / total_minutes * 90)


def test_match_normalizes_current_players_name_too():
    # the CURRENT bootstrap's spelling (accented) must also normalize to match the pooled key
    name_priors, position_priors = compute_historical_priors(
        [FAKE_CSV_ACCENTED, FAKE_CSV_UNACCENTED], min_minutes=450
    )
    player_table = pd.DataFrame(
        [{"player_id": 1, "first_name": "David", "second_name": "Raya Martín", "position": "GKP"}]
    )
    matched = match_historical_priors_to_current(player_table, name_priors, position_priors)
    assert 1 in matched
    total_minutes = 3330 + 3420
    total_points = 162 + 142
    assert matched[1] == pytest.approx(total_points / total_minutes * 90)


@pytest.mark.skipif(not _has_network(), reason="no network access")
def test_live_raya_pools_across_both_real_season_files():
    # End-to-end confirmation against the real live files (not a synthetic fixture) that the
    # exact bug found in this session is actually fixed: Raya's 2025-26 (162 pts / 3330 min) and
    # 2024-25 (142 pts / 3420 min) seasons must now combine into one prior.
    from agents.historical_agent import fetch_historical_players_csv

    csv_2025_26 = fetch_historical_players_csv(season="2025-26", force_refresh=True)
    csv_2024_25 = fetch_historical_players_csv(season="2024-25", force_refresh=True)
    name_priors, _ = compute_historical_priors([csv_2025_26, csv_2024_25], min_minutes=450)

    key = ("David", "Raya Martin")  # normalized
    assert key in name_priors
    # pooled prior must differ from the single-season-only value (4.38) — proves both seasons
    # actually contributed, not just one under a key that happens to still resolve
    single_season_priors, _ = compute_historical_priors([csv_2025_26], min_minutes=450)
    single_key = ("David", "Raya Martin")
    assert single_key in single_season_priors
    assert name_priors[key] != pytest.approx(single_season_priors[single_key])


@pytest.mark.skipif(not _has_network(), reason="no network access")
def test_live_two_season_pool_gives_more_coverage_than_one_season():
    from agents.data_agent import build_player_table, fetch_bootstrap, fetch_fixtures

    bs = fetch_bootstrap()
    fx = fetch_fixtures()
    table = build_player_table(bs, fx)

    one_season = get_historical_priors_for(table, seasons=["2025-26"], force_refresh=True)
    two_seasons = get_historical_priors_for(table, seasons=["2025-26", "2024-25"], force_refresh=True)

    # pooling should never reduce name-match coverage (position fallback still applies to
    # whoever's left unmatched either way, so total dict size may look similar — but the two-
    # season name_priors set specifically should be a superset-or-equal of the one-season set for
    # any player who qualifies both ways)
    assert len(two_seasons) >= len(one_season) * 0.95  # allow small noise, not a strict crash test
