"""Official FPL API fetch + normalization into the pinned player-table schema (framework.md)."""
import json
import os
import time

import pandas as pd
import requests

BASE_URL = "https://fantasy.premierleague.com/api"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PROJECT_ROOT, ".cache")
BOOTSTRAP_CACHE = os.path.join(CACHE_DIR, "bootstrap.json")
FIXTURES_CACHE = os.path.join(CACHE_DIR, "fixtures.json")
CACHE_TTL_SECONDS = 30 * 60

EXPECTED_SQUAD_RULES = {
    "squad_squadsize": 15,
    "squad_squadplay": 11,
    "squad_team_limit": 3,
    "squad_total_spend": 1000,
}

# position short name -> (squad_select, squad_min_play, squad_max_play)
EXPECTED_POSITION_RULES = {
    "GKP": (2, 1, 1),
    "DEF": (5, 3, 5),
    "MID": (5, 2, 5),
    "FWD": (3, 1, 3),
}

VALID_STATUS_CODES = {"a", "d", "i", "s", "u"}

FRAMEWORK_COLUMNS = [
    "player_id", "web_name", "first_name", "second_name", "full_name",
    "team_id", "team_short", "position", "cost_tenths", "price_m",
    "form", "total_points", "ep_next", "selected_by_percent",
    "chance_of_playing_next_round", "status", "news", "fixture_difficulty_next5",
    "minutes", "starts", "birth_date", "expected_goal_involvements_per_90", "event_points",
    "penalties_order", "direct_freekicks_order",
]


class FPLDataUnavailableError(Exception):
    """Raised when the official API can't be reached after retries. Never swallow this."""


class FPLRuleDriftError(Exception):
    """Raised when live game_settings/element_types no longer match framework.md's pinned rules."""


def _fetch_with_retry(url, retries=3, backoff=2):
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "fpl-advisor/1.0"})
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, retried then re-raised typed
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff**attempt)
    raise FPLDataUnavailableError(f"Failed to fetch {url} after {retries} attempts: {last_exc}")


def _read_cache(path, ttl):
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > ttl:
        return None
    with open(path) as f:
        return json.load(f)


def _write_cache(path, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def fetch_bootstrap(force_refresh=False):
    if not force_refresh:
        cached = _read_cache(BOOTSTRAP_CACHE, CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
    data = _fetch_with_retry(f"{BASE_URL}/bootstrap-static/")
    _write_cache(BOOTSTRAP_CACHE, data)
    return data


def fetch_fixtures(force_refresh=False):
    if not force_refresh:
        cached = _read_cache(FIXTURES_CACHE, CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
    data = _fetch_with_retry(f"{BASE_URL}/fixtures/")
    _write_cache(FIXTURES_CACHE, data)
    return data


def assert_rules_unchanged(bootstrap):
    """Raise FPLRuleDriftError if live rules no longer match framework.md's pinned table."""
    gs = bootstrap["game_settings"]
    for key, expected in EXPECTED_SQUAD_RULES.items():
        actual = gs.get(key)
        if actual != expected:
            raise FPLRuleDriftError(f"game_settings.{key} = {actual!r}, expected {expected!r}")

    element_types = {et["singular_name_short"]: et for et in bootstrap["element_types"]}
    for pos, (sel, mn, mx) in EXPECTED_POSITION_RULES.items():
        et = element_types.get(pos)
        if et is None:
            raise FPLRuleDriftError(f"position {pos} missing from element_types")
        actual = (et["squad_select"], et["squad_min_play"], et["squad_max_play"])
        if actual != (sel, mn, mx):
            raise FPLRuleDriftError(f"position {pos} rules changed: got {actual}, expected {(sel, mn, mx)}")


def check_status_codes(bootstrap):
    """Raise FPLRuleDriftError on any status code outside framework.md's confirmed set."""
    statuses = {el["status"] for el in bootstrap["elements"]}
    unexpected = statuses - VALID_STATUS_CODES
    if unexpected:
        raise FPLRuleDriftError(f"Unexpected player status codes found: {unexpected}")
    return statuses


def _avg_fdr_next5(team_id, fixtures, n=5):
    upcoming = [
        f
        for f in fixtures
        if not f.get("finished", False)
        and f.get("event") is not None
        and (f.get("team_h") == team_id or f.get("team_a") == team_id)
    ]
    upcoming.sort(key=lambda f: f["event"])
    difficulties = []
    for f in upcoming[:n]:
        if f["team_h"] == team_id:
            difficulties.append(f["team_h_difficulty"])
        else:
            difficulties.append(f["team_a_difficulty"])
    if not difficulties:
        return 3.0  # neutral default — no upcoming fixtures found (e.g. season end)
    return sum(difficulties) / len(difficulties)


def detect_dgw_bgw(bootstrap, fixtures):
    """Return a list of human-readable warnings for any team with 0 or 2+ fixtures in one event."""
    warnings = []
    team_ids = {t["id"] for t in bootstrap["teams"]}
    events_with_fixtures = {}
    for f in fixtures:
        event = f.get("event")
        if event is None:
            continue
        events_with_fixtures.setdefault(event, []).append(f)

    for event, fx in sorted(events_with_fixtures.items()):
        counts = dict.fromkeys(team_ids, 0)
        for f in fx:
            counts[f["team_h"]] = counts.get(f["team_h"], 0) + 1
            counts[f["team_a"]] = counts.get(f["team_a"], 0) + 1
        for tid, count in counts.items():
            if count == 0:
                warnings.append(f"GW{event}: team_id={tid} has a blank gameweek (0 fixtures)")
            elif count >= 2:
                warnings.append(f"GW{event}: team_id={tid} has a double gameweek ({count} fixtures)")
    return warnings


def _as_float(value, default=0.0):
    if value in (None, ""):
        return default
    return float(value)


def build_player_table(bootstrap, fixtures):
    """Build the pinned player-table DataFrame (framework.md schema) from raw API JSON."""
    assert_rules_unchanged(bootstrap)
    check_status_codes(bootstrap)

    element_type_map = {et["id"]: et["singular_name_short"] for et in bootstrap["element_types"]}
    team_map = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    rows = []
    for el in bootstrap["elements"]:
        position = element_type_map.get(el["element_type"])
        if position not in EXPECTED_POSITION_RULES:
            raise FPLRuleDriftError(f"Unexpected element_type {el['element_type']} for player {el['id']}")

        status = el["status"]
        if status not in VALID_STATUS_CODES:
            raise FPLRuleDriftError(f"Unexpected status {status!r} for player {el['id']}")

        cost_tenths = int(el["now_cost"])
        rows.append(
            {
                "player_id": int(el["id"]),
                "web_name": el["web_name"],
                "first_name": el["first_name"],
                "second_name": el["second_name"],
                "full_name": f"{el['first_name']} {el['second_name']}",
                "team_id": int(el["team"]),
                "team_short": team_map.get(el["team"], ""),
                "position": position,
                "cost_tenths": cost_tenths,
                "price_m": cost_tenths / 10.0,
                "form": _as_float(el.get("form")),
                "total_points": int(el["total_points"]),
                "ep_next": _as_float(el.get("ep_next")),
                "selected_by_percent": _as_float(el.get("selected_by_percent")),
                "chance_of_playing_next_round": el.get("chance_of_playing_next_round"),
                "status": status,
                "news": el.get("news", ""),
                "minutes": int(el["minutes"]),
                "starts": int(el["starts"]),
                "birth_date": el.get("birth_date"),
                "expected_goal_involvements_per_90": float(el["expected_goal_involvements_per_90"]),
                "event_points": int(el["event_points"]),
                "penalties_order": el.get("penalties_order"),
                "direct_freekicks_order": el.get("direct_freekicks_order"),
            }
        )

    df = pd.DataFrame(rows, columns=[c for c in FRAMEWORK_COLUMNS if c != "fixture_difficulty_next5"])
    df["fixture_difficulty_next5"] = df["team_id"].apply(lambda tid: _avg_fdr_next5(tid, fixtures))
    df = df[FRAMEWORK_COLUMNS]

    for warning in detect_dgw_bgw(bootstrap, fixtures):
        print(f"WARNING: DGW/BGW detected — {warning}")

    return df


if __name__ == "__main__":
    bs = fetch_bootstrap(force_refresh=True)
    fx = fetch_fixtures(force_refresh=True)
    table = build_player_table(bs, fx)
    print(table.head())
    print(f"\n{len(table)} players. Max cost_tenths: {table['cost_tenths'].max()}")
    print(f"ep_next distribution: min={table['ep_next'].min()}, max={table['ep_next'].max()}, "
          f"mean={table['ep_next'].mean():.2f}")
