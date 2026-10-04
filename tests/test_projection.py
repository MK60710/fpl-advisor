import pandas as pd
import pytest

from agents.projection import MINUTES_FULL_CONFIDENCE, FPLTooEarlyError, compute_projected_points


def _player_row(
    player_id, status="a", ep_next=5.0, fdr=3.0, minutes=1000, chance_of_playing_next_round=None,
    total_points=0, event_points=0, penalties_order=None, direct_freekicks_order=None, starts=5,
):
    return {
        "player_id": player_id, "status": status, "ep_next": ep_next,
        "fixture_difficulty_next5": fdr, "minutes": minutes,
        "chance_of_playing_next_round": chance_of_playing_next_round,
        "total_points": total_points, "event_points": event_points,
        "penalties_order": penalties_order, "direct_freekicks_order": direct_freekicks_order,
        "starts": starts,
    }


def test_raises_before_gw3():
    df = pd.DataFrame([_player_row(1)])
    with pytest.raises(FPLTooEarlyError):
        compute_projected_points(df, [], current_gw=1)
    with pytest.raises(FPLTooEarlyError):
        compute_projected_points(df, [], current_gw=2)


def test_gw3_does_not_raise():
    df = pd.DataFrame([_player_row(1)])
    result = compute_projected_points(df, [], current_gw=3)
    assert len(result) == 1


def test_fixture_term_direction():
    # same ep_next, easy fixture (fdr=1) should project higher than hard fixture (fdr=5)
    df = pd.DataFrame([_player_row(1, fdr=1.0), _player_row(2, fdr=5.0)])
    result = compute_projected_points(df, [], current_gw=3)
    easy = result[result["player_id"] == 1]["projected_points"].iloc[0]
    hard = result[result["player_id"] == 2]["projected_points"].iloc[0]
    assert easy > hard
    # neutral fdr=3 case should equal the raw ep_next (multiplier == 1.0)
    neutral_df = pd.DataFrame([_player_row(3, fdr=3.0, ep_next=4.0)])
    neutral_result = compute_projected_points(neutral_df, [], current_gw=3)
    assert neutral_result["projected_points"].iloc[0] == pytest.approx(4.0)


def test_nudge_caps_with_more_than_three_notes_from_distinct_sources():
    df = pd.DataFrame([_player_row(1, ep_next=10.0, fdr=3.0)])
    # 5 distinct-source positive notes — uncapped would be 0.05*5=0.25, must cap at 0.15
    notes = [
        {"player_id": 1, "source": f"source{i}", "note_type": "captain_pick", "text": "t", "polarity": "positive"}
        for i in range(5)
    ]
    result = compute_projected_points(df, notes, current_gw=3)
    assert result["projected_points"].iloc[0] == pytest.approx(10.0 * 1.15)


def test_nudge_worked_example_from_framework():
    # framework.md's worked example: 2 positive + 1 negative -> nudge=0.05 -> adjusted=base*1.05
    df = pd.DataFrame([_player_row(1, ep_next=10.0, fdr=3.0)])
    notes = [
        {"player_id": 1, "source": "s1", "note_type": "differential", "text": "t", "polarity": "positive"},
        {"player_id": 1, "source": "s2", "note_type": "differential", "text": "t", "polarity": "positive"},
        {"player_id": 1, "source": "s3", "note_type": "rotation", "text": "t", "polarity": "negative"},
    ]
    result = compute_projected_points(df, notes, current_gw=3)
    assert result["projected_points"].iloc[0] == pytest.approx(10.0 * 1.05)


def test_disputed_availability_overrides_positive_nudge():
    # status 'a' (no official issue) but 2 independent sources report a negative injury note —
    # must drop into the 0.4x band even with a simultaneous positive nudge from other notes.
    df = pd.DataFrame([_player_row(1, status="a", ep_next=10.0, fdr=3.0)])
    notes = [
        {"player_id": 1, "source": "s1", "note_type": "injury", "text": "t", "polarity": "negative"},
        {"player_id": 1, "source": "s2", "note_type": "injury", "text": "t", "polarity": "negative"},
        {"player_id": 1, "source": "s3", "note_type": "captain_pick", "text": "t", "polarity": "positive"},
    ]
    result = compute_projected_points(df, notes, current_gw=3)
    row = result.iloc[0]
    assert bool(row["disputed_availability"]) is True
    # nudge: 1 positive, 2 negative -> clamp(0.05*(1-2), -0.15, 0.15) = -0.05 -> adjusted=9.5
    # then disputed multiplier 0.4x -> 3.8
    assert row["projected_points"] == pytest.approx(10.0 * 0.95 * 0.4)


def test_disputed_availability_requires_two_independent_sources():
    # only ONE source reports negative injury — must NOT trigger disputed availability
    df = pd.DataFrame([_player_row(1, status="a", ep_next=10.0, fdr=3.0)])
    notes = [{"player_id": 1, "source": "s1", "note_type": "injury", "text": "t", "polarity": "negative"}]
    result = compute_projected_points(df, notes, current_gw=3)
    row = result.iloc[0]
    assert bool(row["disputed_availability"]) is False
    assert row["projected_points"] == pytest.approx(10.0 * 0.95)  # just the -5% nudge, no override


def test_no_double_discount_for_unavailable_players():
    # Regression test for the fixed double-discount bug: a status in {i,s,u} player's ep_next
    # already reflects unavailability (live-verified in framework.md) — projected_points must
    # equal ep_next * fixture_multiplier exactly, with no additional override multiply on top.
    for status in ("i", "s", "u"):
        df = pd.DataFrame([_player_row(1, status=status, ep_next=0.0, fdr=3.0)])
        result = compute_projected_points(df, [], current_gw=3)
        assert result["projected_points"].iloc[0] == pytest.approx(0.0)

    # Also confirm a nonzero ep_next for a 'd' (doubtful) status isn't silently re-discounted —
    # doubtful players can still have a real nonzero ep_next reflecting partial availability.
    df = pd.DataFrame([_player_row(1, status="d", ep_next=3.0, fdr=3.0)])
    result = compute_projected_points(df, [], current_gw=3)
    assert result["projected_points"].iloc[0] == pytest.approx(3.0)


def test_shrinkage_blend_never_applies_to_unavailable_player():
    # Regression test for a real live bug (2026-08-30): Osula (status='i', chance_of_playing_
    # next_round=0, ep_next correctly 0.0) still landed at 7.23 projected points because his low
    # current-season minutes (55) gave the historical/position-fallback prior ~88% weight,
    # completely overriding a correctly-zeroed unavailability signal. The blend must never apply
    # regardless of how large the historical prior is.
    df = pd.DataFrame(
        [_player_row(1, status="i", ep_next=0.0, fdr=3.0, minutes=55, chance_of_playing_next_round=0)]
    )
    priors = {1: 50.0}  # deliberately extreme — must have zero effect on an unavailable player
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx(0.0)


def test_shrinkage_blend_blocked_by_zero_chance_even_with_status_available():
    # chance_of_playing_next_round can be explicitly 0 even while status still reads 'a' (a
    # same-day API inconsistency window) — the chance field alone must be enough to block the blend.
    df = pd.DataFrame(
        [_player_row(1, status="a", ep_next=0.5, fdr=3.0, minutes=55, chance_of_playing_next_round=0)]
    )
    priors = {1: 50.0}
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx(0.5)  # blend blocked, base stays ep_next


def test_shrinkage_blend_applies_normally_when_available():
    # sanity check: the fix doesn't break the normal case — 'a' status, no chance-of-playing doubt
    df = pd.DataFrame(
        [_player_row(1, status="a", ep_next=2.0, fdr=3.0, minutes=90, chance_of_playing_next_round=None)]
    )
    priors = {1: 10.0}
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    # shrinkage_weight = 90/450 = 0.2 -> 0.2*2.0 + 0.8*10.0 = 8.4
    assert result["projected_points"].iloc[0] == pytest.approx(8.4)


def test_shrinkage_blend_applies_for_doubtful_with_nonzero_chance():
    # 'd' (doubtful) with a real nonzero chance (e.g. 75%) should still be eligible — the blend
    # exists precisely to help stabilize an uncertain-but-real case, not just fully-fit players.
    df = pd.DataFrame(
        [_player_row(1, status="d", ep_next=2.0, fdr=3.0, minutes=90, chance_of_playing_next_round=75)]
    )
    priors = {1: 10.0}
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx(8.4)


def test_projected_points_never_negative():
    df = pd.DataFrame([_player_row(1, ep_next=1.0, fdr=5.0)])
    notes = [
        {"player_id": 1, "source": f"s{i}", "note_type": "rotation", "text": "t", "polarity": "negative"}
        for i in range(10)
    ]
    result = compute_projected_points(df, notes, current_gw=3)
    assert result["projected_points"].iloc[0] >= 0.0


def test_raises_on_unexpected_status():
    df = pd.DataFrame([_player_row(1, status="n")])  # 'n' confirmed NOT to occur live — defensive check
    with pytest.raises(AssertionError):
        compute_projected_points(df, [], current_gw=3)


# --- Historical shrinkage blend (v1.1) ---


def test_shrinkage_blend_pure_historical_at_zero_minutes():
    # zero current-season minutes -> shrinkage_weight=0 -> base is entirely the historical prior
    df = pd.DataFrame([_player_row(1, ep_next=2.0, fdr=3.0, minutes=0)])
    priors = {1: 10.0}
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx(10.0)


def test_shrinkage_blend_pure_ep_next_at_full_confidence():
    # minutes >= MINUTES_FULL_CONFIDENCE -> shrinkage_weight=1 -> base is entirely ep_next,
    # historical prior has zero influence regardless of its value
    df = pd.DataFrame([_player_row(1, ep_next=2.0, fdr=3.0, minutes=MINUTES_FULL_CONFIDENCE)])
    priors = {1: 999.0}  # deliberately extreme — must have no effect at full confidence
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx(2.0)


def test_shrinkage_blend_halfway_point():
    # minutes = half of MINUTES_FULL_CONFIDENCE -> shrinkage_weight=0.5 -> straight average
    df = pd.DataFrame([_player_row(1, ep_next=4.0, fdr=3.0, minutes=MINUTES_FULL_CONFIDENCE // 2)])
    priors = {1: 8.0}
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx((4.0 + 8.0) / 2)


def test_shrinkage_blend_no_prior_leaves_ep_next_unmodified():
    # a player with no historical match (genuine debutant) is untouched, even with priors passed
    # for OTHER players and even at zero minutes — no guess, ever
    df = pd.DataFrame([_player_row(1, ep_next=3.0, fdr=3.0, minutes=0)])
    priors = {999: 50.0}  # some other player_id, not this one
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx(3.0)


def test_shrinkage_blend_none_priors_behaves_like_empty_dict():
    df = pd.DataFrame([_player_row(1, ep_next=3.0, fdr=3.0, minutes=0)])
    result = compute_projected_points(df, [], current_gw=3, historical_priors=None)
    assert result["projected_points"].iloc[0] == pytest.approx(3.0)


# --- Zero-starts blend gate (v2.1) ---


def test_shrinkage_blend_blocked_by_zero_starts_even_when_available():
    # Regression test for a real live bug (2026-09-19): Fábio Carvalho (BRE) had 3 minutes, 0
    # starts across 5 gameweeks, status='a', chance=100 (officially fine) — but his historical
    # prior (7.96) got 99.3% blend weight off his near-zero minutes, landing him at ~7.75
    # projected points despite not featuring in his club's matchday plans all season. The blend
    # must not apply when starts == 0, regardless of how large the historical prior is.
    df = pd.DataFrame(
        [_player_row(1, status="a", ep_next=1.2, fdr=3.0, minutes=3, starts=0)]
    )
    priors = {1: 7.96}
    result = compute_projected_points(df, [], current_gw=6, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx(1.2)  # blend blocked, base stays ep_next


def test_shrinkage_blend_applies_at_exactly_one_start():
    # boundary check: starts == 1 (the minimum) must still allow the blend to apply normally
    df = pd.DataFrame(
        [_player_row(1, status="a", ep_next=2.0, fdr=3.0, minutes=90, starts=1)]
    )
    priors = {1: 10.0}
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    # shrinkage_weight = 90/450 = 0.2 -> 0.2*2.0 + 0.8*10.0 = 8.4
    assert result["projected_points"].iloc[0] == pytest.approx(8.4)


def test_shrinkage_blend_blocked_by_zero_starts_real_nmecha_case():
    # Regression test using Nmecha's real data from the same live session: 50 minutes, 0 starts,
    # ep_next 1.2, historical prior 6.17 — same pattern as Carvalho, found on the same audit.
    df = pd.DataFrame(
        [_player_row(1, status="a", ep_next=1.2, fdr=3.0, minutes=50, starts=0)]
    )
    priors = {1: 6.17}
    result = compute_projected_points(df, [], current_gw=6, historical_priors=priors)
    assert result["projected_points"].iloc[0] == pytest.approx(1.2)


# --- Outlier-game dampening (v1.9) ---


def test_outlier_dampening_real_bruno_fernandes_case():
    # Regression test using Bruno Fernandes' actual real data (live-verified 2026-08-30, prompted
    # by Mihir's own observation): GW1 = 2 points, GW2 = a hat-trick, 23 points. total_points=25,
    # event_points=23 -> 92% of the season from one game -> must trigger the discount.
    df = pd.DataFrame(
        [_player_row(1, ep_next=12.5, fdr=3.0, minutes=180, total_points=25, event_points=23)]
    )
    priors = {1: 6.9}  # his real historical prior at the time
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)

    # WITHOUT dampening: shrinkage_weight = 180/450 = 0.4 -> 0.4*12.5 + 0.6*6.9 = 9.14
    undampened = 0.4 * 12.5 + 0.6 * 6.9
    # WITH dampening: effective_minutes = 180*0.5=90 -> weight=90/450=0.2 -> 0.2*12.5+0.8*6.9=8.02
    dampened = 0.2 * 12.5 + 0.8 * 6.9

    assert result["projected_points"].iloc[0] == pytest.approx(dampened)
    assert result["projected_points"].iloc[0] < undampened  # confirms the discount actually moved it


def test_outlier_dampening_does_not_trigger_below_points_threshold():
    # a good-but-not-extreme game (e.g. 10 points) making up most of the season must NOT trigger —
    # OUTLIER_POINTS_THRESHOLD (15) is about genuinely rare hauls, not merely "a good week"
    df = pd.DataFrame(
        [_player_row(1, ep_next=5.0, fdr=3.0, minutes=180, total_points=12, event_points=10)]
    )
    priors = {1: 4.0}
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    undampened = (180 / 450) * 5.0 + (1 - 180 / 450) * 4.0
    assert result["projected_points"].iloc[0] == pytest.approx(undampened)


def test_outlier_dampening_does_not_trigger_below_share_threshold():
    # a big single-game haul that's still a MINORITY of a larger season total must not trigger —
    # this is "consistently good with one great week," not "one lucky week carrying the average"
    df = pd.DataFrame(
        [_player_row(1, ep_next=8.0, fdr=3.0, minutes=450, total_points=60, event_points=18)]
    )
    priors = {1: 5.0}
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)
    # 18/60 = 0.30, below OUTLIER_SHARE_THRESHOLD (0.5) -> no dampening -> full minutes -> shrinkage_weight=1.0 -> pure ep_next
    assert result["projected_points"].iloc[0] == pytest.approx(8.0)


def test_outlier_dampening_naturally_fades_as_season_progresses():
    # same 23-point outlier game, but now embedded in a much larger season total (more gameweeks
    # played since) — the mechanism should self-limit without needing an explicit gameweek gate
    df = pd.DataFrame(
        [_player_row(1, ep_next=8.0, fdr=3.0, minutes=1800, total_points=120, event_points=23)]
    )
    priors = {1: 6.9}
    result = compute_projected_points(df, [], current_gw=10, historical_priors=priors)
    # 23/120 = 0.19, below threshold -> no dampening -> full minutes -> shrinkage_weight clamped to 1.0
    assert result["projected_points"].iloc[0] == pytest.approx(8.0)


# --- Set-piece duty bonus (v2.0) ---


def test_set_piece_bonus_single_role():
    df = pd.DataFrame([_player_row(1, ep_next=10.0, fdr=3.0, penalties_order=1)])
    result = compute_projected_points(df, [], current_gw=3)
    assert result["projected_points"].iloc[0] == pytest.approx(10.0 * 1.05)


def test_set_piece_bonus_both_roles_stack():
    df = pd.DataFrame(
        [_player_row(1, ep_next=10.0, fdr=3.0, penalties_order=1, direct_freekicks_order=1)]
    )
    result = compute_projected_points(df, [], current_gw=3)
    assert result["projected_points"].iloc[0] == pytest.approx(10.0 * 1.10)


def test_set_piece_bonus_ignores_backup_taker():
    # order=2 (backup) must NOT trigger the bonus, only order==1 (the actual #1 taker)
    df = pd.DataFrame([_player_row(1, ep_next=10.0, fdr=3.0, penalties_order=2)])
    result = compute_projected_points(df, [], current_gw=3)
    assert result["projected_points"].iloc[0] == pytest.approx(10.0)


def test_set_piece_bonus_none_no_effect():
    df = pd.DataFrame([_player_row(1, ep_next=10.0, fdr=3.0)])  # both order fields default None
    result = compute_projected_points(df, [], current_gw=3)
    assert result["projected_points"].iloc[0] == pytest.approx(10.0)


def test_set_piece_bonus_real_bruno_fernandes_case():
    # Regression test using Bruno Fernandes' actual real data (live-verified 2026-09-01, prompted
    # by Mihir): penalties_order=1 AND direct_freekicks_order=1 at Man Utd — both roles, +10%,
    # stacked on top of the same outlier-dampened shrinkage blend from the earlier real case.
    df = pd.DataFrame(
        [
            _player_row(
                1, ep_next=12.5, fdr=3.0, minutes=180, total_points=25, event_points=23,
                penalties_order=1, direct_freekicks_order=1,
            )
        ]
    )
    priors = {1: 6.05}  # his real 2-season pooled prior at the time
    result = compute_projected_points(df, [], current_gw=3, historical_priors=priors)

    dampened_base = 0.2 * 12.5 + 0.8 * 6.05  # outlier-dampened blend, per v1.9's own test
    expected = dampened_base * 1.10  # +10% for both set-piece roles
    assert result["projected_points"].iloc[0] == pytest.approx(expected)
