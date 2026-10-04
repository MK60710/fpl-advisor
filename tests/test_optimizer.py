import pandas as pd
import pytest

from agents.optimizer import FPLInfeasibleError, select_squad, validate_squad

# --- Fixture A: exactly 15 legal players, only one feasible squad exists (all 15 of them) ---
# 2 GKP, 5 DEF, 5 MID, 3 FWD, spread across 5 clubs (3 each — right at the club cap, not over
# it), all cheap enough that budget is never binding. Proves basic feasibility end to end.


def _fixture_a():
    rows = []
    pid = 1
    # club assignment: 3 players per club across 5 clubs (100-104), ascending pp per player id
    positions = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    for i, position in enumerate(positions):
        rows.append(
            {
                "player_id": pid,
                "position": position,
                "team_id": 100 + (i % 5),
                "cost_tenths": 50,
                "projected_points": float(pid),  # 1..15, strictly ascending, no ties
            }
        )
        pid += 1
    return pd.DataFrame(rows)


def test_fixture_a_unique_feasible_squad_is_all_15():
    df = _fixture_a()
    rec = select_squad(df, budget_tenths=1000, club_cap=3)
    assert set(rec["squad"]) == set(df["player_id"])
    validate_squad(rec, budget_tenths=1000, club_cap=3, player_table=df)


def test_fixture_a_budget_infeasible():
    df = _fixture_a()
    df["cost_tenths"] = 100  # 15 * 100 = 1500 > 1000 budget, only feasible squad is over budget
    with pytest.raises(FPLInfeasibleError):
        select_squad(df, budget_tenths=1000, club_cap=3)


def test_club_cap_infeasible():
    df = _fixture_a()
    # Cram all 15 into only 4 clubs — 4*3=12 < 15, no valid assignment can keep every club <= 3
    df["team_id"] = [100, 101, 102, 103] * 3 + [100, 101, 102]
    with pytest.raises(FPLInfeasibleError):
        select_squad(df, budget_tenths=1000, club_cap=3)


# --- Fixture B: 18 players (15 "good" + 3 strictly worse scrubs the optimizer must exclude from
# the squad itself, not just the XI) with pp values designed so the optimal XI/captain/vice/
# bench_order is hand-computable. ---


def _fixture_b():
    # Team assignment for the 15 "good" players cycles 1..5 so each team has exactly 3 of them
    # (right at the club cap, not over it) — a naive per-position round robin instead put 4 of
    # them on the same team and made the fixture spuriously infeasible.
    rows = [
        {"player_id": "G1", "position": "GKP", "team_id": 1, "cost_tenths": 50, "projected_points": 5.0},
        {"player_id": "G2", "position": "GKP", "team_id": 2, "cost_tenths": 50, "projected_points": 3.0},
        {"player_id": "D1", "position": "DEF", "team_id": 3, "cost_tenths": 50, "projected_points": 6.0},
        {"player_id": "D2", "position": "DEF", "team_id": 4, "cost_tenths": 50, "projected_points": 5.5},
        {"player_id": "D3", "position": "DEF", "team_id": 5, "cost_tenths": 50, "projected_points": 5.0},
        {"player_id": "D4", "position": "DEF", "team_id": 1, "cost_tenths": 50, "projected_points": 2.5},
        {"player_id": "D5", "position": "DEF", "team_id": 2, "cost_tenths": 50, "projected_points": 1.0},
        {"player_id": "M1", "position": "MID", "team_id": 3, "cost_tenths": 50, "projected_points": 6.5},
        {"player_id": "M2", "position": "MID", "team_id": 4, "cost_tenths": 50, "projected_points": 6.2},
        {"player_id": "M3", "position": "MID", "team_id": 5, "cost_tenths": 50, "projected_points": 3.5},
        {"player_id": "M4", "position": "MID", "team_id": 1, "cost_tenths": 50, "projected_points": 2.0},
        {"player_id": "M5", "position": "MID", "team_id": 2, "cost_tenths": 50, "projected_points": 1.5},
        {"player_id": "F1", "position": "FWD", "team_id": 3, "cost_tenths": 50, "projected_points": 7.0},
        {"player_id": "F2", "position": "FWD", "team_id": 4, "cost_tenths": 50, "projected_points": 6.1},
        {"player_id": "F3", "position": "FWD", "team_id": 5, "cost_tenths": 50, "projected_points": 2.2},
        # scrubs — strictly worse than every other player at their position, must be excluded
        {"player_id": "G3", "position": "GKP", "team_id": 3, "cost_tenths": 50, "projected_points": 0.1},
        {"player_id": "D6", "position": "DEF", "team_id": 4, "cost_tenths": 50, "projected_points": 0.2},
        {"player_id": "F4", "position": "FWD", "team_id": 4, "cost_tenths": 50, "projected_points": 0.3},
    ]
    return pd.DataFrame(rows)


def test_fixture_b_excludes_scrubs_from_squad():
    df = _fixture_b()
    rec = select_squad(df, budget_tenths=1000, club_cap=3)
    assert set(rec["squad"]) == {
        "G1", "G2", "D1", "D2", "D3", "D4", "D5",
        "M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3",
    }
    assert "G3" not in rec["squad"]
    assert "D6" not in rec["squad"]
    assert "F4" not in rec["squad"]


def test_fixture_b_hand_computable_xi_captain_vice_bench():
    df = _fixture_b()
    rec = select_squad(df, budget_tenths=1000, club_cap=3)

    # Hand-computed optimum: top-10 outfield by pp is F1,M1,M2,F2,D1,D2,D3,M3,D4,F3
    # (positions: DEF=4, MID=3, FWD=3 — a legal formation), leaving M4,M5,D5 on the bench.
    assert set(rec["xi"]) == {"G1", "F1", "M1", "M2", "F2", "D1", "D2", "D3", "M3", "D4", "F3"}
    assert rec["captain"] == "F1"  # highest pp in the xi
    assert rec["vice"] == "M1"  # second-highest pp in the xi
    assert rec["bench_gk"] == "G2"
    assert rec["bench_order"] == ["M4", "M5", "D5"]  # descending pp: 2.0, 1.5, 1.0

    # Known-answer objective check computed independently from the fixture's own pp values —
    # not read off the solver's internal LP objective (which also carries the tiny tie-break term).
    pp = dict(zip(df["player_id"], df["projected_points"]))
    xi_sum = sum(pp[i] for i in rec["xi"])
    captain_bonus = pp[rec["captain"]]
    assert xi_sum == pytest.approx(55.5)
    assert xi_sum + captain_bonus == pytest.approx(62.5)

    validate_squad(rec, budget_tenths=1000, club_cap=3, player_table=df)


def test_tie_break_prefers_higher_cost_on_equal_projected_points():
    # Regression test for a real live bug (2026-08-30): the tie-break was originally negative
    # (prefer cheaper on a tie), which silently left real budget unused on every near-tie in a
    # live run — forcing minimum spend from 0 up to the full budget produced the EXACT SAME
    # objective value throughout. Fixed to positive: when the model can't tell two players
    # apart on points, prefer spending more, not banking it for no reason.
    df = _fixture_a()  # 2 GKP, 5 DEF, 5 MID, 3 FWD, ascending pp 1..15, cheap (50 tenths each)
    # fixture_a's GKPs: id=1 (pp=1.0), id=2 (pp=2.0). id=2 is a guaranteed pick (strictly best
    # GKP). Add a THIRD GKP candidate that ties id=1's pp exactly but costs more — with 3
    # candidates for 2 slots, id=2 always wins one slot outright; id=1 vs the new candidate tie
    # for the other, and the pricier one must now win.
    tied_pricier_gk = {
        "player_id": 100, "position": "GKP", "team_id": 999, "cost_tenths": 90,  # pricier than id=1's 50
        "projected_points": 1.0,  # exact tie with player_id=1
    }
    df = pd.concat([df, pd.DataFrame([tied_pricier_gk])], ignore_index=True)

    rec = select_squad(df, budget_tenths=1000, club_cap=3)

    assert 2 in rec["squad"]  # the strictly-better GKP, unaffected by the tie
    assert 100 in rec["squad"]  # pricier of the tied pair — must win now
    assert 1 not in rec["squad"]  # cheaper of the tied pair — must lose now


def test_bench_pp_tiebreak_prefers_higher_scoring_bench_player():
    # Regression test for a real live bug (2026-08-30): the objective only ever scores xi[i]/
    # capt[i], never squad-only (bench) membership, so two same-cost bench candidates with
    # different pp were a true tie with no preference — Emiliano Martinez (proj 3.81) lost a
    # bench GK slot to Arrizabalaga (proj 3.19) at the identical price, purely because pp value
    # didn't matter to the objective at all for a bench slot.
    df = _fixture_a()  # id=2 (pp=2.0) is the guaranteed-best GKP; id=1 (pp=1.0) fills bench GKP
    weaker_but_pricier_bench_gk = {
        # cheaper AND lower-pp than id=1, so it can only lose on both counts if the fix works;
        # a real regression (tie-break ignoring pp) would still pick this over id=1 only if cost
        # tie-broke it, which it can't since it's strictly cheaper too — so instead make it the
        # SAME cost as id=1 but lower pp, isolating the bench-pp preference specifically.
        "player_id": 101, "position": "GKP", "team_id": 998, "cost_tenths": 50,  # same cost as id=1
        "projected_points": 0.5,  # strictly worse than id=1's pp=1.0
    }
    df = pd.concat([df, pd.DataFrame([weaker_but_pricier_bench_gk])], ignore_index=True)

    rec = select_squad(df, budget_tenths=1000, club_cap=3)

    assert 2 in rec["squad"]  # unaffected, strictly best GKP
    assert 1 in rec["squad"]  # higher pp (1.0) at equal cost — must win the bench slot now
    assert 101 not in rec["squad"]  # lower pp (0.5) at equal cost — must lose now


def test_validate_squad_catches_vice_equals_captain():
    rec = {
        "squad": list(range(1, 16)), "xi": list(range(1, 12)),
        "bench_gk": 12, "bench_order": [13, 14, 15],
        "captain": 1, "vice": 1, "cost_tenths": 900, "bank_tenths": 100,
    }
    with pytest.raises(AssertionError):
        validate_squad(rec, budget_tenths=1000)


def test_validate_squad_catches_over_budget():
    rec = {
        "squad": list(range(1, 16)), "xi": list(range(1, 12)),
        "bench_gk": 12, "bench_order": [13, 14, 15],
        "captain": 1, "vice": 2, "cost_tenths": 1100, "bank_tenths": -100,
    }
    with pytest.raises(AssertionError):
        validate_squad(rec, budget_tenths=1000)
