"""Expert-site scraping + name-matching (framework.md). Each source is isolated in its own
try/except and reports an explicit status — a source that returns 200-OK-but-unparseable content
must be surfaced as degraded ('empty'), never silently treated as a quiet zero-note success."""
import re
import time

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process

from agents.data_agent import fetch_bootstrap

USER_AGENT = "fpl-advisor-research/1.0 (personal project, low-volume, contact: mihirkolakaluri@gmail.com)"
REQUEST_TIMEOUT = 15
REQUEST_DELAY_SECONDS = 1.0  # politeness — a weekly personal tool, not a crawler
MAX_ARTICLES_PER_SOURCE = 8

VALID_NOTE_TYPES = {"injury", "rotation", "differential", "captain_pick", "transfer_rumor"}
VALID_POLARITIES = {"positive", "negative", "neutral"}

FFF_INDEX_URL = "https://www.fantasyfootballfix.com/blog-index/"
FFS_TEAM_NEWS_URL = "https://www.fantasyfootballscout.co.uk/category/team-news/"

# Name (TEAM_CODE) — Fantasy Football Fix's consistent in-article mention style, e.g. "Bruno Fernandes (IPS)"
FFF_NAME_TEAM_RE = re.compile(r"\b([A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+)+)\s*\(([A-Z]{2,4})\)")

# "Surname1, Surname2, Surname3: TeamName injury latest ..." — FFS's team-news headline style
FFS_HEADLINE_RE = re.compile(r"^(?P<names>[^:]+):\s*(?P<team>.+?)\s+injury latest", re.IGNORECASE)


def _get(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def match_player(raw_name, player_table, team_hint=None, threshold=80, margin=5):
    """Ranked-candidate fuzzy match against web_name and full_name. If the top candidates are
    within `margin` of each other, require team_hint to disambiguate; otherwise drop (return
    None) rather than guess. framework.md's name-matching rule."""
    web_name_choices = dict(zip(player_table["player_id"], player_table["web_name"]))
    full_name_choices = dict(zip(player_table["player_id"], player_table["full_name"]))

    web_matches = process.extract(raw_name, web_name_choices, scorer=fuzz.WRatio, limit=5)
    full_matches = process.extract(raw_name, full_name_choices, scorer=fuzz.WRatio, limit=5)

    best_by_pid = {}
    for _, score, pid in web_matches + full_matches:
        if pid not in best_by_pid or score > best_by_pid[pid]:
            best_by_pid[pid] = score

    if not best_by_pid:
        return None

    ranked = sorted(best_by_pid.items(), key=lambda kv: kv[1], reverse=True)
    top_score = ranked[0][1]
    if top_score < threshold:
        return None

    close = [pid for pid, score in ranked if top_score - score <= margin]
    if len(close) == 1:
        return close[0]

    if team_hint:
        team_map = dict(zip(player_table["player_id"], player_table["team_short"]))
        for pid in close:
            if team_map.get(pid, "").lower() == team_hint.lower():
                return pid

    return None  # ambiguous and unresolved — caller logs and drops


def _team_full_name_to_short():
    bootstrap = fetch_bootstrap()
    return {t["name"].lower(): t["short_name"] for t in bootstrap["teams"]}


def scrape_fantasy_football_fix(player_table):
    """Returns (notes, status). Full article text is freely readable (live-verified) — player
    mentions come with an inline team code, so no external disambiguation source is needed."""
    try:
        index_html = _get(FFF_INDEX_URL)
    except Exception:
        return [], "failed"

    soup = BeautifulSoup(index_html, "html.parser")
    links = set()
    for a in soup.select("a[href*='/blog-index/']"):
        href = a.get("href", "")
        if "?category=" in href or href.rstrip("/").endswith("blog-index"):
            continue
        links.add(href)

    if not links:
        return [], "empty"

    note_type_by_keyword = [
        (("captain",), "captain_pick"),
        (("differential",), "differential"),
        (("transfer", "target", "replacement"), "rotation"),
    ]
    valid_team_codes = set(player_table["team_short"])

    notes = []
    for href in sorted(links)[:MAX_ARTICLES_PER_SOURCE]:
        url = href if href.startswith("http") else f"https://www.fantasyfootballfix.com{href}"
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            article_html = _get(url)
        except Exception:
            continue  # one article failing doesn't fail the whole source

        article_soup = BeautifulSoup(article_html, "html.parser")
        container = article_soup.find(class_="blog-article")
        if container is None:
            continue
        text = container.get_text(separator=" ", strip=True)
        title = article_soup.title.string.strip() if article_soup.title and article_soup.title.string else ""

        lowered_title = title.lower()
        note_type = "differential"
        for keywords, nt in note_type_by_keyword:
            if any(k in lowered_title for k in keywords):
                note_type = nt
                break

        lowered_text = text.lower()
        polarity = "positive"
        if any(w in lowered_text[:400] for w in ("avoid", "doubt", "injury concern", "miss out", "sell")):
            polarity = "negative"

        for match in FFF_NAME_TEAM_RE.finditer(text):
            raw_name, team_code = match.group(1), match.group(2)
            if team_code not in valid_team_codes:
                continue  # not a real team code match, likely a false positive
            notes.append(
                {
                    "raw_name": raw_name,
                    "team_hint": team_code,
                    "source": "fantasyfootballfix",
                    "note_type": note_type,
                    "text": title,
                    "polarity": polarity,
                }
            )

    if not notes:
        return [], "empty"
    return notes, "ok"


def scrape_fantasy_football_scout(player_table):
    """Returns (notes, status). Headlines only are guaranteed by design (no full-article
    dependency) but live-verified 2026-08-28 that full article text is also fetchable — used here
    for the richer injury detail, not just the headline."""
    try:
        index_html = _get(FFS_TEAM_NEWS_URL)
    except Exception:
        return [], "failed"

    soup = BeautifulSoup(index_html, "html.parser")
    headline_links = []
    seen_hrefs = set()
    # The headline text lives in an h2/h3 inside each <article> card; the card's own <a> wraps
    # the whole card (image + date badge + summary + "Read more"), so pulling text from the <a>
    # directly picks up all of that surrounding junk — use the h2/h3 specifically instead.
    for article in soup.find_all("article"):
        heading = article.find(["h2", "h3"])
        link = article.find("a", href=True)
        if heading is None or link is None:
            continue
        title = heading.get_text(strip=True)
        href = link.get("href", "")
        if title and href and href not in seen_hrefs:
            seen_hrefs.add(href)
            headline_links.append((title, href))

    if not headline_links:
        return [], "empty"

    try:
        team_full_to_short = _team_full_name_to_short()
    except Exception:
        team_full_to_short = {}

    notes = []
    seen_urls = set()
    for title, href in headline_links:
        m = FFS_HEADLINE_RE.match(title)
        if not m:
            continue  # not an injury-latest headline (e.g. "Team v Team predicted line-ups") — skip
        if href in seen_urls or len(seen_urls) >= MAX_ARTICLES_PER_SOURCE:
            continue
        seen_urls.add(href)

        raw_names = [n.strip() for n in m.group("names").split(",") if n.strip()]
        team_text = m.group("team").strip().lower()
        team_hint = None
        for full_name, short in team_full_to_short.items():
            if full_name in team_text or team_text in full_name:
                team_hint = short
                break

        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            article_html = _get(href)
            article_soup = BeautifulSoup(article_html, "html.parser")
            for tag in article_soup(["script", "style"]):
                tag.decompose()
            body = article_soup.find("article") or article_soup.find(class_="entry-content")
            body_text = body.get_text(separator=" ", strip=True) if body else title
        except Exception:
            body_text = title  # fall back to the headline alone if the article fetch fails

        lowered = body_text.lower()
        polarity = "negative"  # this source's URL pattern is injury-latest by construction
        if any(w in lowered[:600] for w in ("return", "available", "boost", "back in training")):
            polarity = "neutral"

        for raw_name in raw_names:
            notes.append(
                {
                    "raw_name": raw_name,
                    "team_hint": team_hint,
                    "source": "fantasyfootballscout",
                    "note_type": "injury",
                    "text": title,
                    "polarity": polarity,
                }
            )

    if not notes:
        return [], "empty"
    return notes, "ok"


def validate_notes(notes):
    """Drop schema-invalid notes (missing/invalid field) rather than letting a malformed note
    silently reach projection.py as a false zero-nudge. Returns (valid_notes, dropped_count)."""
    required = {"player_id", "source", "note_type", "text", "polarity"}
    valid = []
    dropped = 0
    for n in notes:
        if not isinstance(n, dict) or not required.issubset(n.keys()):
            dropped += 1
            continue
        if n["note_type"] not in VALID_NOTE_TYPES or n["polarity"] not in VALID_POLARITIES:
            dropped += 1
            continue
        valid.append({k: n[k] for k in required})
    return valid, dropped


def run_research(player_table):
    """Orchestrates all sources. Always returns (notes, source_status) — total failure of every
    source degrades to an empty note list, never an exception, so the weekly run still proceeds
    on official-API-only data (framework.md's failure policy)."""
    source_status = {}
    all_raw_notes = []

    for source_name, scraper in (
        ("fantasyfootballfix", scrape_fantasy_football_fix),
        ("fantasyfootballscout", scrape_fantasy_football_scout),
    ):
        try:
            raw_notes, status = scraper(player_table)
        except Exception:
            raw_notes, status = [], "failed"
        source_status[source_name] = {"status": status, "note_count": len(raw_notes)}
        all_raw_notes.extend(raw_notes)

    matched_notes = []
    dropped_unmatched = 0
    for raw in all_raw_notes:
        player_id = match_player(raw["raw_name"], player_table, team_hint=raw.get("team_hint"))
        if player_id is None:
            dropped_unmatched += 1
            continue
        matched_notes.append(
            {
                "player_id": player_id,
                "source": raw["source"],
                "note_type": raw["note_type"],
                "text": raw["text"],
                "polarity": raw["polarity"],
            }
        )

    deduped = {}
    for n in matched_notes:
        key = (n["player_id"], n["source"], n["note_type"])
        deduped[key] = n  # last write wins — dedupe by (player_id, source, note_type) per framework.md
    valid_notes, dropped_invalid = validate_notes(list(deduped.values()))

    for status in source_status.values():
        status["dropped_unmatched_or_invalid"] = dropped_unmatched + dropped_invalid

    return valid_notes, source_status


if __name__ == "__main__":
    from agents.data_agent import build_player_table, fetch_fixtures

    bs = fetch_bootstrap()
    fx = fetch_fixtures()
    table = build_player_table(bs, fx)
    notes, status = run_research(table)
    print(f"source_status: {status}")
    print(f"{len(notes)} matched notes")
    for n in notes[:10]:
        print(n)
