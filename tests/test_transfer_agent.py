from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from agents.transfer_agent import (
    get_transfer_rumors_for,
    is_transfer_window_open,
    scrape_transfer_rumors,
)


def test_is_transfer_window_open_during_summer_window():
    assert is_transfer_window_open(today=date(2026, 8, 30)) is True
    assert is_transfer_window_open(today=date(2026, 9, 1)) is True  # inclusive of close date


def test_is_transfer_window_open_false_outside_any_window():
    assert is_transfer_window_open(today=date(2026, 10, 15)) is False
    assert is_transfer_window_open(today=date(2026, 11, 1)) is False


def test_is_transfer_window_open_during_winter_window():
    assert is_transfer_window_open(today=date(2027, 1, 15)) is True


GOSSIP_HTML = """
<html><body>
<a href="/a1">Man City enter race for Gakpo - Tuesday's gossip</a>
<a href="/a2">Arsenal continue Alvarez pursuit - Friday's gossip</a>
<a href="/a3">Napoli interested in Woltemade loan - Sunday's gossip</a>
<a href="/nav1">Football</a>
</body></html>
"""


def _mock_player_table():
    rows = [
        {"player_id": 1, "web_name": "Gakpo", "full_name": "Cody Gakpo"},
        {"player_id": 2, "web_name": "Haaland", "full_name": "Erling Haaland"},
    ]
    return pd.DataFrame(rows)


def test_scrape_transfer_rumors_matches_squad_player():
    with patch("agents.transfer_agent._get", return_value=GOSSIP_HTML):
        notes, status = scrape_transfer_rumors(_mock_player_table())

    assert status == "ok"
    assert len(notes) == 1
    assert notes[0]["player_id"] == 1
    assert notes[0]["note_type"] == "transfer_rumor"
    assert notes[0]["polarity"] == "negative"
    assert "Gakpo" in notes[0]["text"]


def test_scrape_transfer_rumors_no_match_for_uninvolved_player():
    # Haaland isn't mentioned in any headline — must produce zero notes for him
    with patch("agents.transfer_agent._get", return_value=GOSSIP_HTML):
        notes, status = scrape_transfer_rumors(_mock_player_table())
    haaland_notes = [n for n in notes if n["player_id"] == 2]
    assert haaland_notes == []


def test_scrape_transfer_rumors_failed_source():
    with patch("agents.transfer_agent._get", side_effect=ConnectionError("simulated failure")):
        notes, status = scrape_transfer_rumors(_mock_player_table())
    assert notes == []
    assert status == "failed"


def test_scrape_transfer_rumors_empty_on_no_gossip_links():
    with patch("agents.transfer_agent._get", return_value="<html><body>nothing here</body></html>"):
        notes, status = scrape_transfer_rumors(_mock_player_table())
    assert notes == []
    assert status == "empty"


def test_get_transfer_rumors_for_skips_outside_window_without_network_call():
    with patch("agents.transfer_agent.scrape_transfer_rumors") as mock_scrape:
        notes, status_entry = get_transfer_rumors_for(_mock_player_table(), today=date(2026, 10, 15))

    mock_scrape.assert_not_called()  # must not even attempt the network call outside a window
    assert notes == []
    assert status_entry["status"] == "skipped"


def test_get_transfer_rumors_for_runs_inside_window():
    with patch("agents.transfer_agent.scrape_transfer_rumors", return_value=([{"fake": "note"}], "ok")), \
         patch("agents.transfer_agent.time.sleep"):
        notes, status_entry = get_transfer_rumors_for(_mock_player_table(), today=date(2026, 8, 30))

    assert notes == [{"fake": "note"}]
    assert status_entry["status"] == "ok"
    assert status_entry["note_count"] == 1
