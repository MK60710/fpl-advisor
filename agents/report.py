"""Weekly markdown report writer (framework.md). Reads recommend.build_recommendation's output
directly — no re-derivation of squad/cost/data_mode logic here, this module only renders."""
import os
from datetime import date, datetime, timezone

from agents.watchlist import _age

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
ARCHIVE_DIR = os.path.join(REPORTS_DIR, "archive")


def _player_row_text(player_id, player_table, extra=""):
    row = player_table[player_table["player_id"] == player_id].iloc[0]
    return (
        f"{row['web_name']} ({row['team_short']}, {row['position']}) — "
        f"£{row['price_m']:.1f}m, {row['projected_points']:.2f} pp{extra}"
    )


def _risk_flags(recommendation, player_table):
    # Full squad, not just XI (v1.8) — a transfer-out rumor matters for a bench player too (they'd
    # be gone from future selection entirely, unlike an injury which is a this-week-only concern
    # for a non-scoring bench slot).
    flags = []
    for player_id in recommendation["squad"]:
        row = player_table[player_table["player_id"] == player_id].iloc[0]
        reasons = []
        if row["chance_of_playing_next_round"] is not None and row["chance_of_playing_next_round"] < 100:
            reasons.append(f"chance of playing next round: {row['chance_of_playing_next_round']}%")
        if row.get("disputed_availability", False):
            reasons.append("disputed availability — official status clear but multiple sources flag doubt")
        notes = recommendation["research_notes_by_player"].get(player_id, [])
        flagged_types = ("injury", "rotation", "transfer_rumor")
        injury_or_rotation = [n for n in notes if n["note_type"] in flagged_types and n["polarity"] == "negative"]
        for note in injury_or_rotation:
            reasons.append(f"{note['source']}: {note['text']}")
        if reasons:
            flags.append(f"- **{row['web_name']}** ({row['team_short']}): {'; '.join(reasons)}")
    return flags


def _chip_window_note(target_gw):
    """Informational-only Bench Boost/Triple Captain window reminder (framework.md v2.2). Purely
    date-derived from the live-verified GW1-19/GW20-38 chip windows — does not track whether the
    chip has actually been used, and never influences squad/XI selection."""
    if 1 <= target_gw <= 19:
        window_end = 19
    elif 20 <= target_gw <= 38:
        window_end = 38
    else:
        return None
    remaining = window_end - target_gw + 1
    return (
        f"**Chip reminder:** Bench Boost and Triple Captain reset after GW{window_end} "
        f"({remaining} gameweek{'s' if remaining != 1 else ''} left in this window, including "
        "this one). This tool doesn't track whether you've used them yet this half — worth "
        "checking manually, and Bench Boost only pays off in a week your bench is actually "
        "playing."
    )


def build_report_markdown(recommendation, player_table):
    rec = recommendation
    lines = [f"# FPL Advisor — Gameweek {rec['target_gw']} Recommendation", ""]

    deadline_str = datetime.fromtimestamp(rec["deadline_epoch"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"Deadline: {deadline_str}")
    lines.append(f"Squad cost: £{rec['cost_tenths'] / 10:.1f}m / £100.0m — bank: £{rec['bank_tenths'] / 10:.1f}m")
    lines.append("")

    chip_note = _chip_window_note(rec["target_gw"])
    if chip_note:
        lines.append(chip_note)
        lines.append("")

    if rec["data_mode"] != "full":
        failed_sources = [s for s, status in rec["source_status"].items() if status["status"] != "ok"]
        lines.append(
            f"**Data mode: {rec['data_mode']}.** "
            + (f"Source(s) unavailable this run: {', '.join(failed_sources)}. " if failed_sources else "")
            + "This report is based on official FPL data only where research signal was missing."
        )
        lines.append("")

    lines.append("## Starting XI")
    for player_id in rec["xi"]:
        tag = ""
        if player_id == rec["captain"]:
            tag = " (C)"
        elif player_id == rec["vice"]:
            tag = " (VC)"
        lines.append(f"- {_player_row_text(player_id, player_table, extra=tag)}")
    lines.append("")

    lines.append("## Bench")
    lines.append(f"- GK: {_player_row_text(rec['bench_gk'], player_table)}")
    for i, player_id in enumerate(rec["bench_order"], start=1):
        lines.append(f"- {i}: {_player_row_text(player_id, player_table)}")
    lines.append("")

    captain_row = player_table[player_table["player_id"] == rec["captain"]].iloc[0]
    vice_row = player_table[player_table["player_id"] == rec["vice"]].iloc[0]
    lines.append("## Captain & Vice-Captain")
    lines.append(
        f"- **Captain: {captain_row['web_name']}** — highest projected points in the XI "
        f"({captain_row['projected_points']:.2f} pp)."
    )
    lines.append(
        f"- **Vice-Captain: {vice_row['web_name']}** — second-highest projected points in the XI "
        f"({vice_row['projected_points']:.2f} pp), activates only if the captain doesn't play."
    )
    lines.append("")

    risk_flags = _risk_flags(rec, player_table)
    lines.append("## Risk Flags")
    if risk_flags:
        lines.extend(risk_flags)
    else:
        lines.append("None — every starter shows 100% chance of playing with no negative research signal.")
    lines.append("")

    lines.append("## Full Squad")
    for player_id in rec["squad"]:
        marker = " (XI)" if player_id in rec["xi"] else " (bench)"
        lines.append(f"- {_player_row_text(player_id, player_table, extra=marker)}")
    lines.append("")

    watchlist = rec.get("watchlist", [])
    lines.append("## Breakout Watchlist")
    lines.append(
        "Informational only — young, low-owned players with strong underlying output. "
        "Never affects the squad above; a call for you to make manually."
    )
    if watchlist:
        for player_id in watchlist:
            row = player_table[player_table["player_id"] == player_id].iloc[0]
            age = _age(row["birth_date"], date.today()) if isinstance(row["birth_date"], str) else "?"
            lines.append(
                f"- {row['web_name']} ({row['team_short']}, {row['position']}) — age {age}, "
                f"£{row['price_m']:.1f}m, {row['expected_goal_involvements_per_90']:.2f} xGI/90, "
                f"{row['selected_by_percent']:.1f}% owned"
            )
    else:
        lines.append("No qualifying players this week.")
    lines.append("")

    return "\n".join(lines)


def write_report(recommendation, player_table, reports_dir=REPORTS_DIR, archive_dir=ARCHIVE_DIR):
    """Writes reports/GW{n}.md (overwrite-latest) and reports/archive/GW{n}-{ISO-timestamp}.md
    (every run kept) per framework.md's pinned path convention. Returns the latest-report path."""
    os.makedirs(reports_dir, exist_ok=True)
    os.makedirs(archive_dir, exist_ok=True)

    markdown = build_report_markdown(recommendation, player_table)
    target_gw = recommendation["target_gw"]

    latest_path = os.path.join(reports_dir, f"GW{target_gw}.md")
    with open(latest_path, "w") as f:
        f.write(markdown)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    archive_path = os.path.join(archive_dir, f"GW{target_gw}-{timestamp}.md")
    with open(archive_path, "w") as f:
        f.write(markdown)

    return latest_path


if __name__ == "__main__":
    from agents.recommend import build_recommendation

    rec, table = build_recommendation(current_gw=3, force_refresh=True)
    path = write_report(rec, table)
    print(f"Report written to {path}")


PROJECTION_FIELDS = [
    "player_id", "web_name", "team_short", "position", "price_m", "status",
    "chance_of_playing_next_round", "minutes", "starts", "ep_next",
    "fixture_difficulty_next5", "projected_points",
]


def write_projections(target_gw, player_table, reports_dir=REPORTS_DIR):
    """Saves every player's pre-deadline projection to reports/GW{n}-projections.json.

    Additive only: nothing reads this file back into the pick. It exists so the model can be
    backtested honestly later (the public historical datasets don't carry real pre-match
    projections) and so the website can show a projections table. Returns the path written."""
    import json
    from datetime import datetime, timezone

    os.makedirs(reports_dir, exist_ok=True)
    rows = []
    for rec in player_table.to_dict("records"):
        row = {}
        for field in PROJECTION_FIELDS:
            value = rec.get(field)
            if hasattr(value, "item"):  # numpy scalar -> plain Python for json
                value = value.item()
            if isinstance(value, float) and value != value:  # NaN -> null
                value = None
            row[field] = value
        rows.append(row)
    rows.sort(key=lambda r: (r["projected_points"] is None, -(r["projected_points"] or 0)))
    path = os.path.join(reports_dir, f"GW{target_gw}-projections.json")
    with open(path, "w") as f:
        json.dump({"gameweek": target_gw, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "players": rows}, f, ensure_ascii=False, indent=1)
    return path
