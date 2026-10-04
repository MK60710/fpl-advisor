from unittest.mock import patch

import pandas as pd
import pytest

from agents.research_agent import (
    match_player,
    run_research,
    scrape_fantasy_football_fix,
    scrape_fantasy_football_scout,
    validate_notes,
)

# Real live duplicate web_names (2026-08-28) that make naive top-1 fuzzy matching unsafe —
# framework.md's name-matching rule exists specifically for cases like "Wilson" x3.
def _duplicate_name_player_table():
    rows = [
        {"player_id": 1, "web_name": "Wilson", "full_name": "Callum Wilson", "team_short": "NEW"},
        {"player_id": 2, "web_name": "Wilson", "full_name": "Ola Wilson", "team_short": "BOU"},
        {"player_id": 3, "web_name": "Wilson", "full_name": "James Wilson", "team_short": "LEE"},
        {"player_id": 4, "web_name": "Salah", "full_name": "Mohamed Salah", "team_short": "LIV"},
    ]
    return pd.DataFrame(rows)


def test_match_player_disambiguates_duplicate_names_via_team_hint():
    df = _duplicate_name_player_table()
    assert match_player("Wilson", df, team_hint="NEW") == 1
    assert match_player("Wilson", df, team_hint="BOU") == 2
    assert match_player("Wilson", df, team_hint="LEE") == 3


def test_match_player_drops_ambiguous_name_without_team_hint():
    df = _duplicate_name_player_table()
    # three equally-good "Wilson" candidates, no team hint to disambiguate — must drop, not guess
    assert match_player("Wilson", df, team_hint=None) is None


def test_match_player_unambiguous_name_resolves_without_hint():
    df = _duplicate_name_player_table()
    assert match_player("Salah", df, team_hint=None) == 4


def test_match_player_below_threshold_returns_none():
    df = _duplicate_name_player_table()
    assert match_player("CompletelyUnrelatedXYZ", df) is None


def test_validate_notes_drops_invalid_polarity_and_missing_fields():
    notes = [
        {"player_id": 1, "source": "s", "note_type": "injury", "text": "t", "polarity": "positive"},
        {"player_id": 1, "source": "s", "note_type": "injury", "text": "t", "polarity": "Positive"},  # wrong case
        {"player_id": 1, "source": "s", "note_type": "made_up_type", "text": "t", "polarity": "positive"},
        {"player_id": 1, "source": "s", "text": "t", "polarity": "positive"},  # missing note_type
    ]
    valid, dropped = validate_notes(notes)
    assert len(valid) == 1
    assert dropped == 3


FFF_INDEX_HTML = """
<html><body>
<a href="/blog-index/?category=Captaincy">Captaincy</a>
<a href="/blog-index/fpl-gameweek-2-captaincy-2026/">Captain picks</a>
</body></html>
"""

FFF_ARTICLE_HTML = """
<html><head><title>Best FPL Captain for Gameweek 2: Top 3 Picks</title></head>
<body><div class="blog-article">
Bruno Fernandes (IPS) has been in excellent form and is our top captain pick this week.
Mohamed Salah (LIV) is the safe, high-ownership choice for Gameweek 2.
</div></body></html>
"""


def _mock_player_table_with_teams():
    rows = [
        {"player_id": 10, "web_name": "Fernandes", "full_name": "Bruno Fernandes", "team_short": "IPS"},
        {"player_id": 11, "web_name": "Salah", "full_name": "Mohamed Salah", "team_short": "LIV"},
    ]
    return pd.DataFrame(rows)


def test_scrape_fantasy_football_fix_parses_name_team_pattern():
    def fake_get(url):
        if url == "https://www.fantasyfootballfix.com/blog-index/":
            return FFF_INDEX_HTML
        return FFF_ARTICLE_HTML

    with patch("agents.research_agent._get", side_effect=fake_get), \
         patch("agents.research_agent.time.sleep"):
        notes, status = scrape_fantasy_football_fix(_mock_player_table_with_teams())

    assert status == "ok"
    raw_names = {n["raw_name"] for n in notes}
    assert "Bruno Fernandes" in raw_names
    assert "Mohamed Salah" in raw_names
    for n in notes:
        assert n["note_type"] == "captain_pick"
        assert n["team_hint"] in {"IPS", "LIV"}


def test_scrape_fantasy_football_fix_empty_on_unparseable_index():
    with patch("agents.research_agent._get", return_value="<html><body>nothing here</body></html>"), \
         patch("agents.research_agent.time.sleep"):
        notes, status = scrape_fantasy_football_fix(_mock_player_table_with_teams())
    assert notes == []
    assert status == "empty"


FFS_INDEX_HTML = """
<html><body>
<article><h2><a href="https://example.com/injury-article">Nico, Osula, Toure: Newcastle injury latest for FPL Gameweek 2</a></h2></article>
<article><h2><a href="https://example.com/lineup-article">Aston Villa v Arsenal predicted line-ups + FPL team news</a></h2></article>
</body></html>
"""

FFS_ARTICLE_HTML = """
<html><body><article>
Dan Burn (ankle) remains out for Newcastle. Will Osula (foot) is also unavailable.
</article></body></html>
"""


def _mock_player_table_for_ffs():
    rows = [
        {"player_id": 20, "web_name": "Burn", "full_name": "Dan Burn", "team_short": "NEW"},
        {"player_id": 21, "web_name": "Osula", "full_name": "Will Osula", "team_short": "NEW"},
    ]
    return pd.DataFrame(rows)


def test_scrape_fantasy_football_scout_parses_injury_headline():
    def fake_get(url):
        if url == "https://www.fantasyfootballscout.co.uk/category/team-news/":
            return FFS_INDEX_HTML
        return FFS_ARTICLE_HTML

    with patch("agents.research_agent._get", side_effect=fake_get), \
         patch("agents.research_agent.time.sleep"), \
         patch("agents.research_agent._team_full_name_to_short", return_value={"newcastle": "NEW"}):
        notes, status = scrape_fantasy_football_scout(_mock_player_table_for_ffs())

    assert status == "ok"
    raw_names = {n["raw_name"] for n in notes}
    assert raw_names == {"Nico", "Osula", "Toure"}
    for n in notes:
        assert n["note_type"] == "injury"
        assert n["team_hint"] == "NEW"


def test_scrape_fantasy_football_scout_skips_non_injury_headlines():
    only_lineup_html = """
    <html><body>
    <article><h2><a href="https://example.com/lineup-article">Aston Villa v Arsenal predicted line-ups + FPL team news</a></h2></article>
    </body></html>
    """
    with patch("agents.research_agent._get", return_value=only_lineup_html), \
         patch("agents.research_agent.time.sleep"), \
         patch("agents.research_agent._team_full_name_to_short", return_value={}):
        notes, status = scrape_fantasy_football_scout(_mock_player_table_for_ffs())
    assert notes == []
    assert status == "empty"


def test_run_research_forced_degradation_one_source_raises_one_returns_garbage():
    """One source raises outright, the other returns 200-OK-but-unparseable content — the run
    must still complete, both must be marked correctly, and note count must reflect only what
    (if anything) a healthy source produced."""
    player_table = _mock_player_table_with_teams()

    def broken_fff(_player_table):
        raise ConnectionError("simulated network failure")

    def garbage_ffs(_player_table):
        return [], "empty"  # 200 OK, but nothing parseable — matches scrape_fantasy_football_scout's own empty-return contract

    with patch("agents.research_agent.scrape_fantasy_football_fix", side_effect=broken_fff), \
         patch("agents.research_agent.scrape_fantasy_football_scout", side_effect=garbage_ffs):
        notes, source_status = run_research(player_table)

    assert notes == []
    assert source_status["fantasyfootballfix"]["status"] == "failed"
    assert source_status["fantasyfootballscout"]["status"] == "empty"


def test_run_research_dedupes_by_player_source_note_type():
    player_table = _mock_player_table_with_teams()

    def three_identical_notes(_player_table):
        note = {
            "raw_name": "Bruno Fernandes", "team_hint": "IPS", "source": "fantasyfootballfix",
            "note_type": "captain_pick", "text": "pick 1", "polarity": "positive",
        }
        return [note, dict(note, text="pick 2"), dict(note, text="pick 3")], "ok"

    with patch("agents.research_agent.scrape_fantasy_football_fix", side_effect=three_identical_notes), \
         patch("agents.research_agent.scrape_fantasy_football_scout", return_value=([], "empty")):
        notes, _ = run_research(player_table)

    fernandes_notes = [n for n in notes if n["player_id"] == 10]
    assert len(fernandes_notes) == 1  # deduped by (player_id, source, note_type)
