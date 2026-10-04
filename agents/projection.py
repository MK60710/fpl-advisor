"""Deterministic projected-points formula (framework.md). Never an LLM call — every branch here
is pinned exactly, in the stated order of operations: GW3+ gate -> ep_next base -> historical
shrinkage blend (outlier-game-dampened) -> set-piece duty bonus -> fixture term -> capped expert
nudge -> disputed-availability override -> floor at 0."""
from collections import defaultdict

FIXTURE_TERM_SLOPE = 0.05
FIXTURE_TERM_MIN = 0.85
FIXTURE_TERM_MAX = 1.15
NUDGE_PER_NOTE = 0.05
NUDGE_CAP = 0.15
DISPUTED_AVAILABILITY_MULTIPLIER = 0.4
DISPUTED_AVAILABILITY_MIN_SOURCES = 2
MINUTES_FULL_CONFIDENCE = 450  # five full matches — shrinkage weight reaches 1.0 (pure ep_next)
# Outlier-game dampening (v1.9): a single explosive gameweek shouldn't be trusted as "this is who
# this player is now" — a hat-trick is a rare, high-variance event, not a form change. Live case
# that prompted this (2026-08-30): Bruno Fernandes scored 2 points in GW1, then a hat-trick (23
# points) in GW2 — his season total (25) is 92% one single game. Pinned thresholds: a single
# gameweek haul this large (>=15, roughly a hat-trick or a massive defensive/bonus day) making up
# more than half the season total triggers the discount.
OUTLIER_POINTS_THRESHOLD = 15
OUTLIER_SHARE_THRESHOLD = 0.5
OUTLIER_MINUTES_DISCOUNT = 0.5  # halve effective minutes for shrinkage-confidence purposes only

# Set-piece duty (v2.0): +5% per confirmed #1 role (penalties, direct free kicks — corners/
# indirect free kicks excluded, too weak a direct-scoring signal to warrant a term). Deliberately
# modest and flat rather than modeling expected penalty volume per team (no clean data source for
# that) — a real, durable edge, not overfit to a speculative magnitude. Max +10% (both roles).
SET_PIECE_BONUS_PER_ROLE = 0.05

VALID_STATUS_CODES = {"a", "d", "i", "s", "u"}


class FPLTooEarlyError(Exception):
    """current_gw < 3: the GW1-2 fallback formula was found degenerate in adversarial review
    (collapses to a price-tier tie-break with no real hard-data differentiation) and was cut
    rather than patched. First usable gameweek is 3."""


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def compute_projected_points(player_table, research_notes, current_gw, historical_priors=None):
    """player_table: DataFrame with framework.md's pinned schema (status, ep_next, minutes,
    fixture_difficulty_next5 required). research_notes: list of dicts (player_id, source,
    note_type, text, polarity) — already deduped by (player_id, source, note_type) upstream.
    historical_priors: optional {player_id: last_season_pp90} from historical_agent.py — a player
    with no entry (or when historical_priors is None/empty) simply skips the shrinkage step and
    uses pure ep_next, never a guess. Returns a copy of player_table with 'projected_points' and
    'disputed_availability' columns."""
    historical_priors = historical_priors or {}
    if current_gw < 3:
        raise FPLTooEarlyError(
            f"current_gw={current_gw}: ep_next isn't reliable before GW3, and the GW1-2 fallback "
            "was cut in review as degenerate. No projection is produced before GW3."
        )

    notes_by_player = defaultdict(list)
    for note in research_notes:
        notes_by_player[note["player_id"]].append(note)

    df = player_table.copy()
    projected = []
    disputed_flags = []

    for _, row in df.iterrows():
        status = row["status"]
        assert status in VALID_STATUS_CODES, f"unexpected status {status!r} for player {row['player_id']}"

        # Base: ep_next, unmodified. No chance_of_playing_next_round discount here — ep_next
        # already reflects it (framework.md, live-verified: every status in {i,s,u} and every
        # chance_of_playing_next_round==0 player already has ep_next==0.0). Re-applying it here
        # was the original design's double-discount bug.
        base = float(row["ep_next"])

        # Historical shrinkage blend (v1.1): a player with real but thin current-season minutes
        # (a new signing who just debuted, a return from injury) gets blended toward last
        # season's per-90 rate, weighted by how much they've actually played THIS season. A
        # player with no historical prior (genuine PL debutant) is left on pure ep_next — no
        # guess. This does not touch the GW1-2 gate above, a different problem (ep_next itself
        # isn't populated league-wide that early, not just for individually low-minutes players).
        #
        # v1.3 fix (live bug found 2026-08-30): the blend must NEVER apply to a player who is
        # currently unavailable — "how good were you historically" is irrelevant if you literally
        # cannot play the upcoming match. Verified live: Osula (status='i', chance_of_playing_
        # next_round=0, ep_next correctly 0.0, "Foot injury - Unknown return date") still landed
        # at 7.23 projected points and made the recommended XI, because his low current-season
        # minutes (55) gave the blend ~88% weight on his historical/position prior, completely
        # overriding a correctly-zeroed current unavailability signal. Gate the blend on
        # `is_available` — an unavailable player's base stays exactly ep_next (0.0), full stop.
        is_available = status in {"a", "d"} and row["chance_of_playing_next_round"] != 0

        # v2.1 fix (real bug found live 2026-09-19): an officially-available player with real
        # PRIOR minutes on the books can still be completely out of his manager's plans this
        # season — Fábio Carvalho (BRE): 3 minutes, 0 starts across 5 gameweeks, status='a',
        # chance=100, live ep_next correctly low (1.2), yet shrinkage_weight=3/450=0.007 gave his
        # historical prior (7.96 pp/90, from a prior strong spell) 99.3% of the blend weight,
        # landing him at ~7.75 projected points and a near-captain pick. Same pattern on Nmecha
        # (50 min, 0 starts). A player who has not started a single match this season despite
        # being fit is real, current evidence he is not part of the XI plan — the live ep_next
        # already reflects that (correctly low); blending toward an old prior papers over it. Gate
        # the blend on starts >= 1 as well as availability. Deliberately still allows the blend at
        # zero MINUTES-but-nonzero-starts (impossible in practice) and doesn't touch the v1.2
        # promoted-team-player fallback path for a player who simply hasn't had a fixture yet.
        historical_pp90 = historical_priors.get(row["player_id"])
        if historical_pp90 is not None and is_available and row["starts"] >= 1:
            effective_minutes = float(row["minutes"])
            # v1.9: discount effective minutes (not the base/ep_next itself — this only affects
            # how much we TRUST the current-season sample, not what it says) when the season
            # total is dominated by one big recent game. Self-limiting: as more gameweeks
            # accumulate, event_points/total_points naturally falls below the threshold for
            # almost everyone, so this only bites early season when one game IS most of the sample.
            total_points = row["total_points"]
            event_points = row["event_points"]
            if (
                total_points > 0
                and event_points >= OUTLIER_POINTS_THRESHOLD
                and event_points / total_points >= OUTLIER_SHARE_THRESHOLD
            ):
                effective_minutes *= OUTLIER_MINUTES_DISCOUNT
            shrinkage_weight = _clamp(effective_minutes / MINUTES_FULL_CONFIDENCE, 0.0, 1.0)
            base = shrinkage_weight * base + (1 - shrinkage_weight) * historical_pp90

        # Set-piece duty (v2.0): official, structured, durable — not recency-biased like ep_next,
        # not speculative like a scraped note. The #1 penalty or direct-free-kick taker has a real
        # structural scoring edge (prompted by Mihir: Bruno Fernandes is Man Utd's #1 for both,
        # live-verified via penalties_order/direct_freekicks_order == 1). Applied regardless of
        # availability status intentionally — this reflects a role assignment, not current form,
        # though an unavailable player's base is already 0 at this point so it's a no-op for them.
        set_piece_roles = sum(1 for field in ("penalties_order", "direct_freekicks_order") if row[field] == 1)
        base *= 1 + SET_PIECE_BONUS_PER_ROLE * set_piece_roles

        fdr = row["fixture_difficulty_next5"]
        fixture_multiplier = _clamp(1 + FIXTURE_TERM_SLOPE * (3 - fdr), FIXTURE_TERM_MIN, FIXTURE_TERM_MAX)
        base *= fixture_multiplier

        player_notes = notes_by_player.get(row["player_id"], [])
        n_pos = sum(1 for n in player_notes if n["polarity"] == "positive")
        n_neg = sum(1 for n in player_notes if n["polarity"] == "negative")
        nudge = _clamp(NUDGE_PER_NOTE * (n_pos - n_neg), -NUDGE_CAP, NUDGE_CAP)
        adjusted = base * (1 + nudge)

        # Disputed availability: the scraper's actual highest-value signal — official status
        # says fine, but >=2 independent sources disagree. Fixed multiplier, outside the nudge
        # cap, so it can't be cancelled out by unrelated positive notes on the same player.
        negative_injury_sources = {
            n["source"] for n in player_notes
            if n["polarity"] == "negative" and n["note_type"] == "injury"
        }
        disputed = status == "a" and len(negative_injury_sources) >= DISPUTED_AVAILABILITY_MIN_SOURCES
        if disputed:
            adjusted *= DISPUTED_AVAILABILITY_MULTIPLIER

        projected.append(max(0.0, adjusted))
        disputed_flags.append(disputed)

    df["projected_points"] = projected
    df["disputed_availability"] = disputed_flags
    return df
