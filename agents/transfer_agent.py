"""Transfer rumor checking (framework.md v1.8) — informational only, never affects
projected_points (transfer gossip is far less reliable than official injury status; treated with
the same "surface, don't decide" philosophy as the Breakout Watchlist). Only runs when a real
transfer window is open — Mihir's own point: rumor-checking every week year-round is pointless
when transfers only happen in two short windows."""
import time
from datetime import date

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

USER_AGENT = "fpl-advisor-research/1.0 (personal project, low-volume, contact: mihirkolakaluri@gmail.com)"
REQUEST_TIMEOUT = 15
GOSSIP_URL = "https://www.bbc.com/sport/football/gossip"

# Live-verified 2026-08-30 (WebSearch): summer 2026 window closes Tue 2026-09-01, 23:00 BST.
# Winter window dates are NOT live-verified — historical-pattern approximation only, flagged here
# so a future session re-checks before it actually matters (months away from today).
SUMMER_WINDOW = (date(2026, 6, 1), date(2026, 9, 1))
WINTER_WINDOW_APPROXIMATE = (date(2027, 1, 1), date(2027, 2, 3))  # UNVERIFIED — re-check live in Dec/Jan

NAME_MATCH_THRESHOLD = 90  # headlines are short/dense — require a tight fuzzy match to avoid false positives


def is_transfer_window_open(today=None):
    today = today or date.today()
    return SUMMER_WINDOW[0] <= today <= SUMMER_WINDOW[1] or WINTER_WINDOW_APPROXIMATE[0] <= today <= WINTER_WINDOW_APPROXIMATE[1]


def _get(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape_transfer_rumors(player_table):
    """Returns (notes, status). Only checks headlines against the given player_table's names
    (intended: pass only the CURRENT SQUAD, not the full ~600-player pool — keeps this cheap and
    focused on players Mihir actually has picked, not every player in the league). A name match
    means the player appeared in today's gossip column at all; direction/certainty isn't parsed
    from the headline grammar — deliberately conservative, surfaced for Mihir to judge himself."""
    try:
        html = _get(GOSSIP_URL)
    except Exception:
        return [], "failed"

    soup = BeautifulSoup(html, "html.parser")
    headlines = [a.get_text(strip=True) for a in soup.find_all("a", href=True) if "'s gossip" in a.get_text()]
    if not headlines:
        return [], "empty"

    web_names = dict(zip(player_table["player_id"], player_table["web_name"]))
    full_names = dict(zip(player_table["player_id"], player_table["full_name"]))

    notes = []
    for headline in headlines:
        for player_id in player_table["player_id"]:
            web_name = web_names[player_id]
            full_name = full_names[player_id]
            # Substring check first (cheap, exact) for short/common surnames; fuzzy as a backstop
            # for headlines using a fuller name than the FPL web_name.
            matched = (
                web_name.lower() in headline.lower()
                or fuzz.partial_ratio(full_name.lower(), headline.lower()) >= NAME_MATCH_THRESHOLD
            )
            if matched:
                notes.append(
                    {
                        "player_id": player_id,
                        "source": "bbc_gossip",
                        "note_type": "transfer_rumor",
                        "text": headline,
                        "polarity": "negative",  # any current transfer chatter is a continuity risk
                    }
                )

    if not notes:
        return [], "empty"
    return notes, "ok"


def get_transfer_rumors_for(player_table, today=None):
    """Convenience wrapper matching the other sources' contract. Returns
    (notes, source_status_entry). Skips the scrape entirely (status='skipped') outside a real
    transfer window — Mihir's point: checking weekly year-round when nothing's happening is
    wasted effort."""
    if not is_transfer_window_open(today):
        return [], {"status": "skipped", "note_count": 0}

    time.sleep(1.0)  # politeness, matching research_agent.py's convention
    notes, status = scrape_transfer_rumors(player_table)
    return notes, {"status": status, "note_count": len(notes)}


if __name__ == "__main__":
    from agents.data_agent import build_player_table, fetch_bootstrap, fetch_fixtures
    from agents.recommend import build_recommendation

    rec, table = build_recommendation(current_gw=3, force_refresh=False)
    squad_table = table[table["player_id"].isin(rec["squad"])]
    notes, status = get_transfer_rumors_for(squad_table)
    print(f"status: {status}")
    for n in notes:
        name = squad_table[squad_table["player_id"] == n["player_id"]]["web_name"].iloc[0]
        print(f"{name}: {n['text']}")
