"""Integration: data_agent -> research_agent -> projection -> optimizer, wired into one
recommendation for a given gameweek. First point real live data flows through the whole
pipeline end to end."""
from agents.data_agent import build_player_table, fetch_bootstrap, fetch_fixtures
from agents.historical_agent import get_historical_priors_for
from agents.optimizer import select_squad, validate_squad
from agents.projection import compute_projected_points
from agents.research_agent import run_research
from agents.transfer_agent import get_transfer_rumors_for
from agents.watchlist import build_watchlist

BUDGET_TENTHS = 1000
CLUB_CAP = 3


def _deadline_epoch_for_gw(bootstrap, target_gw):
    for event in bootstrap["events"]:
        if event["id"] == target_gw:
            return event["deadline_time_epoch"]
    raise ValueError(f"No event found for gameweek {target_gw}")


def _compute_data_mode(source_status):
    # 'skipped' (the transfer-rumor source outside a transfer window, v1.8) is deliberate, not a
    # failure — it must not count toward "degraded". Excluded entirely from this calculation.
    statuses = [s["status"] for s in source_status.values() if s["status"] != "skipped"]
    if not statuses or all(s == "ok" for s in statuses):
        return "full"
    if all(s in ("failed", "empty") for s in statuses):
        return "official_only"
    return "degraded"


def build_recommendation(current_gw, force_refresh=False):
    """Returns (recommendation, player_table). `recommendation` matches framework.md's pinned
    schema exactly. `player_table` (with a projected_points column added) is returned alongside
    it — Step 7's report needs display fields (name, team, price) the id-only recommendation
    dict deliberately doesn't carry, per framework.md's own schema."""
    bootstrap = fetch_bootstrap(force_refresh=force_refresh)
    fixtures = fetch_fixtures(force_refresh=force_refresh)
    player_table = build_player_table(bootstrap, fixtures)

    research_notes, source_status = run_research(player_table)
    historical_priors = get_historical_priors_for(player_table)

    projected_table = compute_projected_points(
        player_table, research_notes, current_gw, historical_priors=historical_priors
    )
    watchlist = build_watchlist(player_table)

    optimizer_input = projected_table[["player_id", "position", "cost_tenths", "team_id", "projected_points"]]
    squad_result = select_squad(optimizer_input, budget_tenths=BUDGET_TENTHS, club_cap=CLUB_CAP)

    # Transfer rumor check (v1.8) — scoped to the FINAL squad only (not the full ~600-player
    # pool) and gated on an actual transfer window being open. Informational only, never feeds
    # projected_points (already computed above) — surfaces via Risk Flags in the report instead.
    squad_table = projected_table[projected_table["player_id"].isin(squad_result["squad"])]
    transfer_notes, transfer_status = get_transfer_rumors_for(squad_table)

    all_notes = research_notes + transfer_notes
    research_notes_by_player = {}
    for note in all_notes:
        research_notes_by_player.setdefault(note["player_id"], []).append(note)

    source_status = dict(source_status)
    source_status["bbc_gossip"] = transfer_status

    recommendation = {
        "target_gw": current_gw,
        "deadline_epoch": _deadline_epoch_for_gw(bootstrap, current_gw),
        "squad": squad_result["squad"],
        "xi": squad_result["xi"],
        "bench_gk": squad_result["bench_gk"],
        "bench_order": squad_result["bench_order"],
        "captain": squad_result["captain"],
        "vice": squad_result["vice"],
        "cost_tenths": squad_result["cost_tenths"],
        "bank_tenths": squad_result["bank_tenths"],
        "research_notes_by_player": research_notes_by_player,
        "source_status": source_status,
        "data_mode": _compute_data_mode(source_status),
        "watchlist": watchlist,
    }

    validate_squad(recommendation, budget_tenths=BUDGET_TENTHS, club_cap=CLUB_CAP, player_table=player_table)

    return recommendation, projected_table


if __name__ == "__main__":
    # Today's real gameweek (GW2) is before the GW3+ gate — pass current_gw=3 explicitly to
    # exercise the full live pipeline now rather than waiting for the calendar.
    rec, table = build_recommendation(current_gw=3, force_refresh=True)
    print(f"data_mode: {rec['data_mode']}, source_status: {rec['source_status']}")
    print(f"cost: {rec['cost_tenths']}/{BUDGET_TENTHS} tenths, bank: {rec['bank_tenths']}")
    name_map = dict(zip(table["player_id"], table["web_name"]))
    print("Squad:", [name_map[i] for i in rec["squad"]])
    print("Captain:", name_map[rec["captain"]], "Vice:", name_map[rec["vice"]])
    print("Bench GK:", name_map[rec["bench_gk"]], "Bench order:", [name_map[i] for i in rec["bench_order"]])
