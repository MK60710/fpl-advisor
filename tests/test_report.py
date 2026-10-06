import pandas as pd

from agents.report import build_report_markdown, write_report


def _fixture_player_table():
    positions = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    rows = []
    for i, position in enumerate(positions):
        rows.append(
            {
                "player_id": i + 1,
                "web_name": f"Player{i + 1}",
                "team_short": "ABC",
                "position": position,
                "price_m": 5.0 + i * 0.1,
                "projected_points": 10.0 - i * 0.3,
                "chance_of_playing_next_round": 100 if i != 2 else 75,  # player 3 (id=3) is a doubt
                "disputed_availability": False,
                "birth_date": "2004-01-01",
                "expected_goal_involvements_per_90": 0.5,
                "selected_by_percent": 5.0,
            }
        )
    return pd.DataFrame(rows)


def _fixture_recommendation(data_mode="full"):
    return {
        "target_gw": 3,
        "deadline_epoch": 1735400000,
        "squad": list(range(1, 16)),
        "xi": list(range(1, 12)),
        "bench_gk": 12,
        "bench_order": [13, 14, 15],
        "captain": 1,
        "vice": 2,
        "cost_tenths": 950,
        "bank_tenths": 50,
        "research_notes_by_player": {
            3: [{"source": "fantasyfootballscout", "note_type": "injury", "text": "Doubtful for GW3", "polarity": "negative"}],
        },
        "source_status": {
            "fantasyfootballfix": {"status": "ok", "note_count": 2},
            "fantasyfootballscout": {"status": "failed", "note_count": 0},
        },
        "data_mode": data_mode,
        "watchlist": [4],
    }


def test_report_includes_all_required_sections():
    table = _fixture_player_table()
    rec = _fixture_recommendation(data_mode="degraded")
    markdown = build_report_markdown(rec, table)

    assert "## Starting XI" in markdown
    assert "## Bench" in markdown
    assert "## Captain & Vice-Captain" in markdown
    assert "## Risk Flags" in markdown
    assert "## Full Squad" in markdown


def test_report_shows_degradation_note_when_not_full():
    table = _fixture_player_table()
    rec = _fixture_recommendation(data_mode="degraded")
    markdown = build_report_markdown(rec, table)

    assert "Data mode: degraded" in markdown
    assert "fantasyfootballscout" in markdown  # the failed source is named


def test_report_omits_degradation_note_when_full():
    table = _fixture_player_table()
    rec = _fixture_recommendation(data_mode="full")
    markdown = build_report_markdown(rec, table)

    assert "Data mode:" not in markdown


def test_report_flags_doubtful_starter_and_injury_note():
    table = _fixture_player_table()
    rec = _fixture_recommendation(data_mode="degraded")
    markdown = build_report_markdown(rec, table)

    assert "Player3" in markdown.split("## Risk Flags")[1].split("## Full Squad")[0]
    assert "chance of playing next round: 75%" in markdown
    assert "Doubtful for GW3" in markdown


def test_report_flags_transfer_rumor_for_bench_player():
    # v1.8: risk flags now cover the full squad, not just XI — a bench player's transfer-out
    # rumor still matters for future selection even though it doesn't affect this week's score.
    table = _fixture_player_table()
    rec = _fixture_recommendation()
    rec["research_notes_by_player"][13] = [
        {"source": "bbc_gossip", "note_type": "transfer_rumor", "text": "Club X eye Player13 - Monday's gossip", "polarity": "negative"}
    ]
    markdown = build_report_markdown(rec, table)

    risk_section = markdown.split("## Risk Flags")[1].split("## Full Squad")[0]
    assert "Player13" in risk_section
    assert "Monday's gossip" in risk_section


def test_report_marks_captain_and_vice_tags():
    table = _fixture_player_table()
    rec = _fixture_recommendation()
    markdown = build_report_markdown(rec, table)

    xi_section = markdown.split("## Starting XI")[1].split("## Bench")[0]
    assert "Player1" in xi_section and "(C)" in xi_section
    assert "Player2" in xi_section and "(VC)" in xi_section


def test_report_watchlist_section_lists_players():
    table = _fixture_player_table()
    rec = _fixture_recommendation()  # watchlist=[4]
    markdown = build_report_markdown(rec, table)

    section = markdown.split("## Breakout Watchlist")[1]
    assert "Player4" in section
    assert "xGI/90" in section
    assert "% owned" in section


def test_report_watchlist_section_empty_case():
    table = _fixture_player_table()
    rec = _fixture_recommendation()
    rec["watchlist"] = []
    markdown = build_report_markdown(rec, table)

    section = markdown.split("## Breakout Watchlist")[1]
    assert "No qualifying players this week." in section


def test_report_watchlist_missing_key_defaults_to_empty():
    # backward compatibility: an older recommendation dict without a 'watchlist' key must not crash
    table = _fixture_player_table()
    rec = _fixture_recommendation()
    del rec["watchlist"]
    markdown = build_report_markdown(rec, table)
    assert "## Breakout Watchlist" in markdown


def test_write_report_creates_latest_and_archive_files(tmp_path):
    table = _fixture_player_table()
    rec = _fixture_recommendation()
    reports_dir = tmp_path / "reports"
    archive_dir = reports_dir / "archive"

    latest_path = write_report(rec, table, reports_dir=str(reports_dir), archive_dir=str(archive_dir))

    assert (reports_dir / "GW3.md").exists()
    assert latest_path == str(reports_dir / "GW3.md")
    archived = list(archive_dir.glob("GW3-*.md"))
    assert len(archived) == 1


def test_write_projections_saves_every_player_sorted_and_json_safe(tmp_path):
    import json
    import math

    import numpy as np
    import pandas as pd

    from agents.report import write_projections

    table = pd.DataFrame([
        {"player_id": 1, "web_name": "Low", "team_short": "ARS", "position": "DEF", "price_m": 4.5, "status": "a",
         "chance_of_playing_next_round": float("nan"), "minutes": np.int64(90), "starts": 1, "ep_next": 2.0,
         "fixture_difficulty_next5": 3.0, "projected_points": np.float64(2.1), "news": "ignored"},
        {"player_id": 2, "web_name": "High", "team_short": "MCI", "position": "FWD", "price_m": 15.0, "status": "a",
         "chance_of_playing_next_round": 100.0, "minutes": 450, "starts": 5, "ep_next": 9.0,
         "fixture_difficulty_next5": 2.4, "projected_points": 9.8, "news": ""},
    ])
    path = write_projections(7, table, reports_dir=str(tmp_path))

    data = json.load(open(path))
    assert path.endswith("GW7-projections.json")
    assert data["gameweek"] == 7
    assert [p["web_name"] for p in data["players"]] == ["High", "Low"]
    assert data["players"][1]["chance_of_playing_next_round"] is None
    assert data["players"][1]["minutes"] == 90 and isinstance(data["players"][1]["minutes"], int)
    assert "news" not in data["players"][0]
    assert not any(isinstance(v, float) and math.isnan(v) for p in data["players"] for v in p.values())
