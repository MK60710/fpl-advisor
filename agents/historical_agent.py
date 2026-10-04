"""Last-season historical data (framework.md v1.1) — a shrinkage prior for the projection
formula, not a replacement for live current-season data. Source: vaastav/Fantasy-Premier-League
on GitHub (live-verified 2026-08-29: real, actively maintained, 10 seasons of history)."""
import csv
import io
import os
import time
import unicodedata

import requests

from agents.data_agent import FPLDataUnavailableError, PROJECT_ROOT

BASE_URL = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
LAST_COMPLETED_SEASON = "2025-26"
# v1.6: look back 2 completed seasons, not just the last one — a single season is a noisy sample
# for an established player (found live 2026-08-30: Raya's 2025-26-only prior, 4.38 pp/90, was
# nearly overtaken by a promoted-team keeper on a hot 2-game streak; a season is one real data
# point, and pooling more of them gives a steadier baseline for exactly this kind of case).
# Live-verified 2026-08-30: both 2024-25 and 2023-24 seasons are fetchable from the same repo.
LOOKBACK_SEASONS = ["2025-26", "2024-25"]
CACHE_DIR = os.path.join(PROJECT_ROOT, ".cache")
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # a completed season's data never changes — cache a full week
MIN_HISTORICAL_MINUTES = 450  # five full matches, pooled across all fetched seasons combined

# Live-verified 2026-08-30: historical players_raw.csv element_type uses the same 1/2/3/4
# encoding as the current bootstrap-static (GKP/DEF/MID/FWD) — no element_types lookup table
# ships with the historical CSV itself, so this mapping is hardcoded rather than derived.
ELEMENT_TYPE_TO_POSITION = {"1": "GKP", "2": "DEF", "3": "MID", "4": "FWD"}


def _normalize_name(s):
    """Strip diacritics for cross-season/cross-source name matching. v1.7 fix (real bug found
    live 2026-08-30): the SAME real player's second_name can be spelled with or without accents
    across different files — David Raya is 'Raya Martín' in the 2025-26 CSV but 'Raya Martin'
    (no accent) in 2024-25. Exact-string pooling treated these as two different players, so his
    2024-25 season silently never merged into his prior at all — not an error, just quietly
    missing data. Normalizing (NFKD decompose, drop combining marks) makes both spellings collide
    on the same key. Applied everywhere a name is used as a pooling or matching key: within
    compute_historical_priors (pooling across season files) and in
    match_historical_priors_to_current (matching against the current bootstrap's spelling, which
    may itself differ from either historical file)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _cache_path(season):
    return os.path.join(CACHE_DIR, f"historical_players_{season}.csv")


def fetch_historical_players_csv(season=LAST_COMPLETED_SEASON, force_refresh=False):
    """Returns the raw CSV text for data/{season}/players_raw.csv, cached locally."""
    cache_path = _cache_path(season)
    if not force_refresh and os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) <= CACHE_TTL_SECONDS:
            with open(cache_path) as f:
                return f.read()

    url = f"{BASE_URL}/{season}/players_raw.csv"
    try:
        resp = requests.get(url, headers={"User-Agent": "fpl-advisor/1.0"}, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        raise FPLDataUnavailableError(f"Failed to fetch historical data for {season}: {exc}") from exc

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path, "w") as f:
        f.write(resp.text)
    return resp.text


def compute_historical_priors(csv_texts, min_minutes=MIN_HISTORICAL_MINUTES):
    """csv_texts: a single CSV string (one season) OR a list of CSV strings (pooled across
    multiple seasons, v1.6) — raw minutes/points are summed across every season given BEFORE the
    min_minutes threshold and pp90 are computed, so a player with e.g. 300 minutes in each of two
    seasons (600 total) now qualifies and gets a steadier two-season rate, not two separate,
    individually-thin single-season numbers.

    Returns (name_priors, position_priors).

    name_priors: {(first_name, second_name): points_per_90} for every player with at least
    min_minutes played (pooled across all given seasons) — the high-fidelity, player-specific
    prior.

    position_priors: {position: average_points_per_90} — the per-player pooled pp90 values above,
    averaged across all qualifying players at that position — a fallback for a current player with
    no name match at all (v1.2 fix: a promoted-team player, e.g. from Hull City in 2026-27, has no
    prior-season PL record to match against, so previously got NO shrinkage whatsoever — exactly
    backwards, since a brand-new PL player's 2-game sample needs regression-to-mean more than
    anyone's). Both dicts are built from the same pooled, min-minutes-filtered player set, so
    they're always consistent with each other."""
    if isinstance(csv_texts, str):
        csv_texts = [csv_texts]

    pooled = {}  # (normalized_first_name, normalized_second_name) -> [sum_minutes, sum_points, element_type]
    for csv_text in csv_texts:
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            key = (_normalize_name(row["first_name"]), _normalize_name(row["second_name"]))
            entry = pooled.setdefault(key, [0, 0, row["element_type"]])
            entry[0] += int(row["minutes"])
            entry[1] += int(row["total_points"])
            # element_type: keep whichever season's value we saw first — a real position change
            # mid-lookback-window is rare enough not to warrant reconciliation logic here.

    name_priors = {}
    position_totals = {}  # position -> [sum_pp90, count]
    for key, (minutes, total_points, element_type) in pooled.items():
        if minutes < min_minutes:
            continue
        pp90 = total_points / minutes * 90
        name_priors[key] = pp90

        position = ELEMENT_TYPE_TO_POSITION.get(element_type)
        if position:
            totals = position_totals.setdefault(position, [0.0, 0])
            totals[0] += pp90
            totals[1] += 1

    position_priors = {pos: total / count for pos, (total, count) in position_totals.items()}
    return name_priors, position_priors


def match_historical_priors_to_current(player_table, name_priors, position_priors):
    """Returns {player_id: historical_pp90}. Exact (normalized first_name, normalized
    second_name) match first — normalized (v1.7) because the same real player's name can be
    spelled with or without accents across different files (live-verified: David Raya is 'Raya
    Martín' in one season's CSV, 'Raya Martin' in another). A current player with no name match
    falls back to their position's league-wide average (v1.2) rather than getting no prior at all
    — a promoted-team player is exactly who most needs regression toward a sane baseline off a
    tiny current-season sample. Only a position with zero historical rows at all (shouldn't happen
    with real PL data) leaves a player with no prior."""
    matched = {}
    for _, row in player_table.iterrows():
        key = (_normalize_name(row["first_name"]), _normalize_name(row["second_name"]))
        if key in name_priors:
            matched[row["player_id"]] = name_priors[key]
        elif row["position"] in position_priors:
            matched[row["player_id"]] = position_priors[row["position"]]
    return matched


def get_historical_priors_for(player_table, seasons=LOOKBACK_SEASONS, force_refresh=False):
    """Convenience wrapper: fetch + compute + match in one call. Fetches each season in `seasons`
    independently — one season failing to fetch (network blip, a season not yet archived) is
    logged and skipped, not fatal, same degrade-gracefully policy as every other source in this
    project. Returns {} only if EVERY season fails; this is a projection-quality enhancement, not
    a required dependency."""
    csv_texts = []
    for season in seasons:
        try:
            csv_texts.append(fetch_historical_players_csv(season=season, force_refresh=force_refresh))
        except FPLDataUnavailableError as exc:
            print(f"WARNING: historical data for {season} unavailable, continuing without it — {exc}")

    if not csv_texts:
        return {}

    name_priors, position_priors = compute_historical_priors(csv_texts)
    return match_historical_priors_to_current(player_table, name_priors, position_priors)


if __name__ == "__main__":
    from agents.data_agent import build_player_table, fetch_bootstrap, fetch_fixtures

    bs = fetch_bootstrap()
    fx = fetch_fixtures()
    table = build_player_table(bs, fx)
    priors = get_historical_priors_for(table, force_refresh=True)
    print(f"{len(priors)}/{len(table)} current players have a prior (name match or position fallback)")
    name_map = dict(zip(table["player_id"], table["web_name"]))
    for pid, pp90 in sorted(priors.items(), key=lambda kv: kv[1], reverse=True)[:10]:
        print(f"{name_map[pid]}: {pp90:.2f} pp/90 last season")
