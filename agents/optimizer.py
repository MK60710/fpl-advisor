"""Joint ILP squad optimizer (framework.md). One solve over squad+XI+captain — a two-stage
'pick 15 then pick 11' design would optimize the wrong quantity, since FPL only scores the
starting XI plus the captain's doubled points, not the full squad."""
from collections import Counter

import pulp

SQUAD_POSITION_COUNTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}

# (DEF, MID, FWD) — the 8 legal starting formations, GKP is always 1. framework.md.
LEGAL_FORMATIONS = [
    (3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2),
    (4, 5, 1), (5, 2, 3), (5, 3, 2), (5, 4, 1),
]

class FPLInfeasibleError(Exception):
    """Raised when no valid squad exists under the given constraints. Never read solver .value()
    on a non-optimal solve — that silently returns a partial/nonsensical squad."""


def _build_base_problem(ids, pp, cost, pos, team, budget_tenths, club_cap):
    """Shared structural constraints (no objective) — used by all three lexicographic stages
    below, so they can never silently drift apart from each other."""
    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    sq = pulp.LpVariable.dicts("sq", ids, cat="Binary")
    xi = pulp.LpVariable.dicts("xi", ids, cat="Binary")
    capt = pulp.LpVariable.dicts("capt", ids, cat="Binary")

    for i in ids:
        prob += xi[i] <= sq[i]
        prob += capt[i] <= xi[i]

    prob += pulp.lpSum(sq[i] for i in ids) == 15
    prob += pulp.lpSum(xi[i] for i in ids) == 11
    prob += pulp.lpSum(capt[i] for i in ids) == 1

    for position, count in SQUAD_POSITION_COUNTS.items():
        prob += pulp.lpSum(sq[i] for i in ids if pos[i] == position) == count

    prob += pulp.lpSum(xi[i] for i in ids if pos[i] == "GKP") == 1
    prob += pulp.lpSum(xi[i] for i in ids if pos[i] == "DEF") >= 3
    prob += pulp.lpSum(xi[i] for i in ids if pos[i] == "DEF") <= 5
    prob += pulp.lpSum(xi[i] for i in ids if pos[i] == "MID") >= 2
    prob += pulp.lpSum(xi[i] for i in ids if pos[i] == "MID") <= 5
    prob += pulp.lpSum(xi[i] for i in ids if pos[i] == "FWD") >= 1
    prob += pulp.lpSum(xi[i] for i in ids if pos[i] == "FWD") <= 3

    prob += pulp.lpSum(cost[i] * sq[i] for i in ids) <= budget_tenths

    for t in set(team.values()):
        prob += pulp.lpSum(sq[i] for i in ids if team[i] == t) <= club_cap

    return prob, sq, xi, capt


def _solve(prob):
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise FPLInfeasibleError(f"No valid squad exists (solver status: {status})")
    return status


def select_squad(player_table, budget_tenths=1000, club_cap=3):
    """player_table: DataFrame/records with player_id, position, cost_tenths, team_id,
    projected_points. Returns the pinned recommendation-dict fields this function owns:
    squad, xi, bench_gk, bench_order, captain, vice, cost_tenths, bank_tenths.

    Three-stage LEXICOGRAPHIC solve, not a single scalarized objective with tie-break weights.
    v1.5 fix (real bug found live 2026-08-30, in the same debugging session that produced v1.4):
    an epsilon-weighted single objective is numerically fragile — verified directly that
    BENCH_PP_EPSILON=1e-9 was mathematically correct (forcing each candidate gave genuinely
    different objective values, 119.0007500160 vs .0155) but CBC's branch-and-bound floating-
    point noise swallowed a difference that small and silently picked the worse option anyway;
    1e-7 failed the same way, only 1e-6 (uncomfortably close to TIE_BREAK_EPSILON's own scale)
    resolved reliably. Rather than tune yet another magic constant with no principled floor,
    solve exactly, in priority order, with real constraints pinning each stage's result before
    the next stage runs — no floating-point weight ever competes with another.

    Stage 1 — maximize what FPL actually scores: XI points + the captain's doubled points.
    Stage 2 — among all Stage-1-optimal squads, maximize total spend (don't bank budget for no
      reason — found live: forcing spend from £0 to £100m tied on real points, an epsilon sign
      bug was silently picking cheap every time).
    Stage 3 — among all Stage-1-and-2-optimal squads, maximize bench (non-XI) projected_points
      too, since the raw objective is otherwise indifferent among bench fillers at equal cost
      (found live: Emiliano Martinez, proj 3.81, lost a bench slot to a proj-3.19 player at the
      identical price, purely because bench pp never entered the objective at all before this).
    """
    players = player_table.to_dict("records") if hasattr(player_table, "to_dict") else list(player_table)
    ids = [p["player_id"] for p in players]
    pp = {p["player_id"]: float(p["projected_points"]) for p in players}
    cost = {p["player_id"]: int(p["cost_tenths"]) for p in players}
    pos = {p["player_id"]: p["position"] for p in players}
    team = {p["player_id"]: p["team_id"] for p in players}

    # Stage 1: maximize real FPL points (XI + captain bonus).
    prob1, sq1, xi1, capt1 = _build_base_problem(ids, pp, cost, pos, team, budget_tenths, club_cap)
    prob1 += pulp.lpSum(pp[i] * xi1[i] for i in ids) + pulp.lpSum(pp[i] * capt1[i] for i in ids)
    _solve(prob1)
    stage1_points = pulp.value(prob1.objective)

    # Stage 2: among Stage-1-optimal squads, maximize total spend.
    prob2, sq2, xi2, capt2 = _build_base_problem(ids, pp, cost, pos, team, budget_tenths, club_cap)
    prob2 += pulp.lpSum(pp[i] * xi2[i] for i in ids) + pulp.lpSum(pp[i] * capt2[i] for i in ids) >= stage1_points
    prob2 += pulp.lpSum(cost[i] * sq2[i] for i in ids)
    _solve(prob2)
    stage2_cost = pulp.value(prob2.objective)

    # Stage 3: among Stage-1-and-2-optimal squads, maximize bench (squad-but-not-XI) pp too.
    prob3, sq3, xi3, capt3 = _build_base_problem(ids, pp, cost, pos, team, budget_tenths, club_cap)
    prob3 += pulp.lpSum(pp[i] * xi3[i] for i in ids) + pulp.lpSum(pp[i] * capt3[i] for i in ids) >= stage1_points
    prob3 += pulp.lpSum(cost[i] * sq3[i] for i in ids) >= stage2_cost
    prob3 += pulp.lpSum(pp[i] * (sq3[i] - xi3[i]) for i in ids)
    _solve(prob3)

    sq, xi, capt = sq3, xi3, capt3
    squad = [i for i in ids if round(sq[i].value()) == 1]
    starting = [i for i in ids if round(xi[i].value()) == 1]
    captain = next(i for i in ids if round(capt[i].value()) == 1)

    bench = [i for i in squad if i not in starting]
    bench_gk = next(i for i in bench if pos[i] == "GKP")
    bench_order = sorted((i for i in bench if i != bench_gk), key=lambda i: pp[i], reverse=True)

    starters_excl_captain = sorted((i for i in starting if i != captain), key=lambda i: pp[i], reverse=True)
    vice = starters_excl_captain[0]

    cost_tenths_total = sum(cost[i] for i in squad)

    return {
        "squad": squad,
        "xi": starting,
        "bench_gk": bench_gk,
        "bench_order": bench_order,
        "captain": captain,
        "vice": vice,
        "cost_tenths": cost_tenths_total,
        "bank_tenths": budget_tenths - cost_tenths_total,
    }


def validate_squad(rec, budget_tenths=1000, club_cap=3, player_table=None):
    """Raise AssertionError on any constraint violation. Shared by Steps 3/6/8/9 — one
    implementation, not restated per step. Position/club checks run only if player_table given."""
    assert len(rec["squad"]) == 15, f"squad size {len(rec['squad'])}, expected 15"
    assert len(set(rec["squad"])) == 15, "squad has duplicate player_ids"
    assert len(rec["xi"]) == 11, f"xi size {len(rec['xi'])}, expected 11"
    assert set(rec["xi"]).issubset(set(rec["squad"])), "xi contains a player not in squad"
    assert rec["bench_gk"] in rec["squad"], "bench_gk not in squad"
    assert rec["bench_gk"] not in rec["xi"], "bench_gk is also a starter"
    assert len(rec["bench_order"]) == 3, f"bench_order length {len(rec['bench_order'])}, expected 3"
    non_gk_bench = set(rec["squad"]) - set(rec["xi"]) - {rec["bench_gk"]}
    assert set(rec["bench_order"]) == non_gk_bench, "bench_order doesn't match the actual non-GK bench"
    assert rec["captain"] in rec["xi"], "captain not in xi"
    assert rec["vice"] in rec["xi"], "vice not in xi"
    assert rec["vice"] != rec["captain"], "vice equals captain"
    assert rec["cost_tenths"] <= budget_tenths, f"cost_tenths {rec['cost_tenths']} exceeds budget {budget_tenths}"
    assert rec["bank_tenths"] == budget_tenths - rec["cost_tenths"], "bank_tenths doesn't reconcile"

    if player_table is not None:
        pos_map = dict(zip(player_table["player_id"], player_table["position"]))
        team_map = dict(zip(player_table["player_id"], player_table["team_id"]))

        squad_positions = Counter(pos_map[i] for i in rec["squad"])
        assert dict(squad_positions) == SQUAD_POSITION_COUNTS, f"squad position counts {dict(squad_positions)}"

        xi_positions = Counter(pos_map[i] for i in rec["xi"])
        gkp, defn, mid, fwd = (
            xi_positions.get("GKP", 0), xi_positions.get("DEF", 0),
            xi_positions.get("MID", 0), xi_positions.get("FWD", 0),
        )
        assert gkp == 1, f"xi GKP count {gkp}, expected 1"
        assert (defn, mid, fwd) in LEGAL_FORMATIONS, f"xi formation {(defn, mid, fwd)} is not one of the 8 legal formations"

        club_counts = Counter(team_map[i] for i in rec["squad"])
        assert max(club_counts.values()) <= club_cap, f"club cap exceeded: {dict(club_counts)}"
