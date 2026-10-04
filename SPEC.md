# FPL Advisor — Spec

Approved approach: **C — hybrid** (agents for research/judgment, deterministic solver for the pick). Approved by Mihir 2026-08-28.

## Goal

A weekly-run system that recommends the strongest possible Fantasy Premier League squad/lineup/captain/transfers, given real constraints (budget, positions, club caps). Recommendations only — nothing touches Mihir's actual FPL account. No existing FPL team; this builds the first squad from scratch, targeting GW2 (deadline 2026-08-28 17:30 UTC, likely missed) or GW3 onward.

## Verified game rules (fetched live from `fantasy.premierleague.com/api/bootstrap-static/`, 2026-08-28)

- Squad: 15 players total — 2 GKP, 5 DEF, 5 MID, 3 FWD.
- Budget: £100.0m (`now_cost` fields are in tenths of £1m).
- Max 3 players from any one real club.
- Starting XI: exactly 1 GKP; 3-5 DEF; 2-5 MID; 1-3 FWD; 11 total, 4 on bench.
- Captain: 2x points; vice-captain auto-activates if captain doesn't play.
- Chips, 2x each per season: Bench Boost and Triple Captain available GW1-19 and GW20-38; Wildcard and Free Hit available GW2-19 and GW20-38 (`start_event` verified live: `wildcard`/`freehit` = 2, `bboost`/`3xc` = 1). Chips are unmodeled in this build — see "explicitly out of scope."
- Current gameweek: GW2 (of 38), deadline 2026-08-28T17:30:00Z.

## Data sources

**Official API (no auth, no scraping):**
- `bootstrap-static/` — all players (`elements`), teams, positions (`element_types`), gameweeks (`events`), game settings.
- `fixtures/` — full fixture list with difficulty ratings.
- `entry/{id}/` — a specific manager's team (not needed yet, no existing team).

Key per-player fields: `now_cost`, `form`, `total_points`, `ep_next`, `selected_by_percent`, `chance_of_playing_this_round`, `chance_of_playing_next_round`, `news`.

**Scraped (expert signal, official API can't see):**
- Fantasy Football Scout — https://www.fantasyfootballscout.co.uk
- FPL Review / a projected-points source — for a second opinion on `ep_next`
- r/FantasyPL — sentiment, differential picks, injury chatter the official API lags on

## Architecture

1. **Data agent** — pulls `bootstrap-static` + `fixtures`, normalizes into a clean player table (price, form, fixture difficulty next 5 GWs, injury flag).
2. **Research agent** — scrapes the 2-3 expert sources above for the current gameweek, extracts: injury/rotation notes, consensus captain picks, differential picks, any player-specific red flags.
3. **Projection step** (deterministic, not LLM judgment) — blends official `ep_next` with expert signal into one `projected_points` per player. Expert signal adjusts (nudges) the official number; it does not override hard injury/suspension flags.
4. **Optimizer** (integer programming — PuLP + CBC solver) — selects the 15-man squad, starting XI, captain and vice-captain that maximizes total `projected_points` subject to: budget ≤ £100.0m, exact position counts, max 3 per club, valid starting-XI formation.
5. **Report agent** — writes the weekly output as a markdown artifact: squad list with prices, starting XI + bench order, captain/vice rationale, key risk flags (rotation, injury, tough fixture run), suggested transfers if a team already exists.
6. **Orchestrator** — runs the above weekly, timed ahead of each gameweek's deadline (pulled from `events` in the API, not hardcoded).

## Output

A markdown report (and optionally an Artifact) per gameweek: recommended squad + XI + captain + reasoning. Mihir enters the picks manually on the FPL site.

## Explicitly out of scope (this build)

- No login/automation against Mihir's actual FPL account.
- No mobile app / persistent server — a scheduled local run (launchd, same pattern as `land-scan`) is enough for now.
- No mini-league analysis or rival-team scouting (possible future extension, not now).
- No transfer suggestions or chip-timing logic in v1 — Mihir has no existing FPL team, so "suggest transfers from a current squad" has nothing to operate on yet. The optimizer always builds a fresh 15 from scratch each run. Revisit once a real team/`entry/{id}/` exists to diff against.
- No GW1-2 recommendation — the projection formula requires `ep_next` (reliable from GW3 on). Adversarial review (2026-08-28) found the originally-planned GW1-2 fallback degenerate (collapses to a price-tier tie-break with no real differentiation) and cut it. First real usable output is GW3.

## Open implementation questions for the construction plan

- Exact scrape targets and how brittle they are (site structure can change — needs graceful degradation if a scrape fails, not a hard block on the whole run).
- Solver library choice: PuLP+CBC (free, no license) vs a simpler hand-rolled greedy/backtracking approach — PuLP is the correct call for guaranteed-valid results.
- Where projected-points blending logic lives (deterministic Python, not an LLM call) and how much weight expert signal gets vs official `ep_next`.
