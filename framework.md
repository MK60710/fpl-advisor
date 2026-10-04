# FPL Advisor — Framework

Single source of truth. Every field is either **live-verified** (cited with the date it was
pulled) or **UNVERIFIED — resolved by Step N**. No plain assertions with unstated confidence.

## Changelog

- v1.0 (2026-08-28) — initial framework, written after adversarial review of the construction plan.
- v1.1 (2026-08-29) — added historical-data shrinkage blend (Feature 1) and Breakout Watchlist
  (Feature 2), per Mihir's request. Schema extended with `minutes`, `starts`, `birth_date`,
  `expected_goal_involvements_per_90` on player_table. New formula step (shrinkage blend) inserted
  between base and fixture-term. New module `historical_agent.py` (last-season data from
  `vaastav/Fantasy-Premier-League` GitHub repo — verified live 2026-08-29: real, active, 10 seasons
  of history, `players_raw.csv` per season with per-player `minutes`/`total_points`/`birth_date`).
- v1.2 (2026-08-30) — fixed a real gap found live: a promoted-team player (no 2025-26 PL record —
  e.g. Hull City, promoted for 2026-27) got NO historical prior at all, so their raw ~2-game
  `ep_next` passed through completely unshrunk. Verified live: three Hull players sat at ep_next
  8.0-10.0 off ~150 minutes while a returning star (Bruno Fernandes, real prior 6.9 pp/90) was
  correctly stabilized by the exact-name match. `historical_agent.py`'s `compute_historical_priors`
  now also returns position-pooled averages (GKP/DEF/MID/FWD, same min-minutes-filtered rows);
  `match_historical_priors_to_current` falls back to the player's position average when no exact
  name match exists, rather than leaving them with no prior. Exact match still always wins when
  available — this only fills the previously-uncovered case.
- v1.3 (2026-08-30) — fixed a second real gap the v1.2 fix exposed: the historical blend was
  applying to currently-unavailable players. Verified live: Osula (Newcastle, status='i', chance_
  of_playing_next_round=0, correctly-zeroed ep_next=0.0, "Foot injury - Unknown return date") still
  landed at 7.23 projected points and made the recommended XI, because his low current-season
  minutes (55) gave his historical/position prior ~88% blend weight, overriding the correctly-
  zeroed current signal. Fixed in `projection.py`: the blend now only applies when `status in
  {'a','d'}` AND `chance_of_playing_next_round != 0` — an unavailable player's base stays exactly
  `ep_next` (i.e. 0.0), full stop, regardless of how large their historical prior is.
- v1.4 (2026-08-30) — fixed the optimizer's tie-break direction (`optimizer.py`). It was
  `-TIE_BREAK_EPSILON * cost` (prefer cheaper on a tie), which silently left real budget unused
  on every near-tie — a live run forcing minimum squad spend from £0 to the full £100m produced
  the exact same objective value throughout, proving the negative sign was picking the cheaper of
  tied options every time, not that spending more genuinely cost points. Flipped to
  `+TIE_BREAK_EPSILON * cost` — defer to price when the model is indifferent, and default idle
  budget to spent rather than banked for no reason (this build never plans a future transfer to
  save cash for).
- v1.5 (2026-08-30, same debugging session) — replaced the epsilon-weighted single-objective
  approach with a proper **3-stage lexicographic solve** in `optimizer.py`. While adding a third
  epsilon term (bench-pp preference, prompted by a real gap: Emiliano Martinez proj 3.81 lost a
  bench slot to a proj-3.19 player at equal cost since bench pp never entered the objective before
  this), found the epsilon approach itself is fragile — `BENCH_PP_EPSILON=1e-9` was verified
  mathematically correct (forcing each candidate directly gave distinct objective values,
  119.0007500160 vs .0155) but CBC's branch-and-bound floating-point noise swallowed a difference
  that small and silently returned the worse option; `1e-7` failed the same way; only `1e-6`
  (uncomfortably close to `TIE_BREAK_EPSILON`'s own scale, risking the two interfering) resolved
  reliably. Rather than tune a fourth magic constant with no principled floor, `select_squad` now
  solves exactly, in priority order, with each stage's result pinned as a real constraint before
  the next stage runs: Stage 1 maximizes real FPL points (XI + captain bonus); Stage 2, among
  Stage-1-optimal squads, maximizes total spend; Stage 3, among Stage-1-and-2-optimal squads,
  maximizes bench pp too. No floating-point weight ever competes with another. `TIE_BREAK_EPSILON`
  and `BENCH_PP_EPSILON` are removed from the code entirely.
- v1.6 (2026-08-30) — historical prior now pools **2 completed seasons** (2025-26 + 2024-25), not
  just the last one. Prompted by a real live case: Raya's single-season (2025-26) prior, 4.38
  pp/90, was nearly overtaken by Tzolakis (a promoted-team keeper on a hot 2-game start) — a
  single season is one noisy data point for an established player. `compute_historical_priors` now
  pools raw minutes+points across every given season BEFORE applying `MIN_HISTORICAL_MINUTES` and
  computing pp90 — a player below the threshold in every individual season can now qualify on the
  combined total (e.g. 270+270=540 minutes). `get_historical_priors_for` fetches each season
  independently; one season failing to fetch degrades gracefully (logged, skipped) rather than
  sinking the whole prior — same per-source degrade policy as the scrape sources. Both prior
  seasons live-verified fetchable 2026-08-30.
- v1.7 (2026-08-30, same debugging session) — fixed a real bug the v1.6 pooling change exposed:
  Raya's pooled prior came out numerically IDENTICAL to his single-season prior, which shouldn't
  happen. Root cause, live-verified: David Raya's `second_name` is spelled `Raya Martín` (with
  accent) in the 2025-26 CSV but `Raya Martin` (no accent) in 2024-25 — the same real player, two
  different exact-string keys, so his 2024-25 season was silently pooled under a key that never
  matched anything and never actually merged in. `_normalize_name()` (NFKD decompose, drop
  combining marks) added and applied everywhere a name becomes a pooling or matching key —
  `compute_historical_priors`'s pooling keys AND `match_historical_priors_to_current`'s lookup
  against the current bootstrap's spelling (which may itself differ from either historical file).
  This class of bug likely affects every accented name across season boundaries, not just Raya.
- v1.8 (2026-08-30) — added automatic transfer rumor checking, per Mihir's request (prompted by a
  manual one-off check that found Harvey Elliott reported as a Brighton transfer target while he
  was a recommended starter). New module `transfer_agent.py`: scrapes BBC Sport's gossip column
  (live-verified free, structured, ~24 headlines/pull), scoped to the final squad only, gated on
  an actual transfer window being open (Mihir's own point — no need to check year-round when
  nothing's happening). Informational only — feeds the report's Risk Flags (now scoped to the
  full squad, not just XI), never `projected_points`. `_compute_data_mode` updated so a
  deliberate 'skipped' status (outside a window) never reads as degraded.
- v1.9 (2026-09-01) — added outlier-game dampening to the historical shrinkage blend, per Mihir's
  observation. Verified live: Bruno Fernandes scored 2 points in GW1, then a hat-trick (23 points)
  in GW2 — his season total (25) is 92% one single game, and the un-dampened blend was trusting
  that as if it were a real 2-game sample. Fix uses `event_points` (most recent finished
  gameweek's score — already present in `bootstrap-static`, no extra API calls needed): when a
  single recent gameweek accounts for ≥50% of season `total_points` AND that gameweek itself was
  ≥15 points (a genuine outlier haul, not just "a good week"), effective minutes for the
  shrinkage-weight calculation are halved — trusting the current-season sample less, leaning more
  on the historical prior. Self-limiting by construction: as more gameweeks accumulate,
  `event_points/total_points` naturally falls below the threshold for almost everyone, so this
  only bites when one game genuinely dominates a still-thin sample.
- v2.0 (2026-09-01, same session) — added a set-piece duty bonus, per Mihir's push-back on the
  v1.9 dampening ("Bruno takes all the penalties and free kicks"). Live-verified:
  `penalties_order` and `direct_freekicks_order` are real, structured fields in `bootstrap-static`
  (Bruno Fernandes = 1 for both at Man Utd; spot-checked against known real assignments —
  Haaland=1 penalties at Man City, Palmer=1 penalties at Chelsea, Watkins=2/backup at Villa — all
  correct). This is a durable, official role assignment, not a recency-biased stat or a
  speculative rumor, so unlike the Watchlist/transfer-rumor "informational only" pieces it DOES
  feed `projected_points`: +5% per confirmed #1 role (penalties, direct free kicks;
  corners/indirect free kicks excluded — too weak a direct-scoring signal to warrant a term), only
  for `order == 1` (the actual primary taker, not a backup), max +10% stacking both roles.
  Deliberately flat/modest rather than modeling expected penalty volume per team (no clean data
  source for that) — reflects the real structural edge without overfitting a speculative
  magnitude. Applied after the historical shrinkage blend, before the fixture term.
- v2.1 (2026-09-19) — gated the historical shrinkage blend on `starts >= 1`, per Mihir's push-back
  ("big players normally play a lot of matches and perform consistently") which led to ground-
  truthing the live GW6 squad. Verified live: Fábio Carvalho (BRE) had 3 minutes and 0 starts
  across 5 gameweeks, status='a', chance_of_playing_next_round=100, live `ep_next` correctly low
  (1.2) — but `shrinkage_weight = 3/450 = 0.007` gave his historical prior (7.96 pp/90, from an
  earlier strong spell) 99.3% of the blend, landing him at ~7.75 projected points and a near-
  captain pick in the recommended squad despite not being in his club's matchday plans all season.
  Same pattern found on Nmecha (50 min, 0 starts). v1.3 only gates the blend on official status/
  chance-of-playing — it does not catch a player who is officially fit but simply unselected.
  Fixed in `projection.py`: the blend now additionally requires `starts >= 1`; a player with zero
  starts this season, however available on paper, keeps base as pure `ep_next` (which already
  reflects the live exclusion) rather than being lifted by a stale prior. Does not affect the v1.2
  promoted-team/position-fallback path for a player who simply hasn't had a fixture yet this
  season — only bites once a player has actually gone winless for a start.
- v2.2 (2026-09-24) — added a Bench Boost/Triple Captain window reminder to the report, per
  Mihir's explicit request: informational only, purely date-derived from the live-verified GW1-19/
  GW20-38 chip windows (see Squad rules section), never feeds the optimizer or squad/XI selection.
  Deliberate scope limit — Mihir rejected building real chip-timing logic (a "bench_boost_prep"
  optimizer mode raising the bench cost/quality floor) into the squad-building path, since he only
  uses each chip ~twice a season and doesn't want it factored into every run. This does not track
  whether a chip has actually been used (the tool still has no visibility into Mihir's live team,
  per "Deferred, not built in v1" below) — it just surfaces the window and gameweeks remaining so
  he can decide manually. `report._chip_window_note`.

## Squad rules (live-verified 2026-08-28 against `bootstrap-static`)

- 15 players total: 2 GKP, 5 DEF, 5 MID, 3 FWD.
- Budget: **1000 tenths of £1m** (`game_settings.squad_total_spend`). All optimizer/validation
  arithmetic uses integer `cost_tenths` against `budget_tenths = 1000` — never float `price_m`.
- Max 3 players per real club (`game_settings.squad_team_limit`).
- Starting XI: 1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD, 11 total (`element_types[].squad_min_play`/
  `squad_max_play`).
- Captain = 2x points; vice-captain activates if captain doesn't play.
- **Only 8 legal starting formations** (DEF-MID-FWD): 3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-2-3,
  5-3-2, 5-4-1.
- Current gameweek at plan time: GW2 of 38, deadline 2026-08-28T17:30:00Z. Deadline rule
  (live-verified across all 38 events): exactly 90 minutes before the first kickoff of that
  gameweek. Weekday distribution: Sat 26, Fri 5, Wed 5, Sun 2 — NOT predominantly Friday.
- Chips (unmodeled in v1): Bench Boost and Triple Captain, GW1-19 and GW20-38; Wildcard and Free
  Hit, GW2-19 and GW20-38 (`start_event` verified: wildcard/freehit = 2, bboost/3xc = 1).

## Runtime rule-drift guard

Step 2 asserts on every run: `game_settings.squad_squadsize == 15`, `squad_squadplay == 11`,
`squad_team_limit == 3`, `squad_total_spend == 1000`, and each `element_types[].squad_select/
squad_min_play/squad_max_play` matches the table above. Raise loudly on mismatch — a mid-season
FPL rule change must fail loudly, never silently produce an illegal squad.

## Pinned player-table schema (output of `data_agent.build_player_table`)

| column | dtype | source | note |
|---|---|---|---|
| `player_id` | int | `elements[].id` | |
| `web_name` | str | `elements[].web_name` | **not unique** — 15 live duplicates incl. `Wilson` x3 |
| `first_name`, `second_name`, `full_name` | str | `elements[].first_name`/`second_name`/concat | needed for disambiguation |
| `team_id` | int | `elements[].team` | required to join fixtures — `team_short` alone can't |
| `team_short` | str | `teams[].short_name` (joined via `team_id`) | |
| `position` | str, one of GKP/DEF/MID/FWD | derived from `element_types[].singular_name_short` via `elements[].element_type` (an **int**) | raise on any `element_type` not in the expected set |
| `cost_tenths` | int | `elements[].now_cost` (already tenths, no division) | primary budget field |
| `price_m` | float | `cost_tenths / 10` | display only, never fed to the solver |
| `form` | float | `elements[].form` (cast from **str**) | |
| `total_points` | int | `elements[].total_points` | |
| `ep_next` | float | `elements[].ep_next` (cast from **str**) | |
| `selected_by_percent` | float | `elements[].selected_by_percent` (cast from **str**) | |
| `chance_of_playing_next_round` | `int \| None` | `elements[].chance_of_playing_next_round` | int-or-null, not 0-100 float |
| `status` | str, one of `a,d,i,s,u` | `elements[].status` | **UNVERIFIED — resolved by Step 2** (live-checked once 2026-08-28: only these 5 values occurred, no `n`; Step 2 re-confirms every run) |
| `news` | str | `elements[].news` | |
| `fixture_difficulty_next5` | float | avg of `fixtures[].team_h_difficulty`/`team_a_difficulty` for this team's next 5 unfinished fixtures (`finished == false`, `event is not null`, home/away branch on `team_h`/`team_a`) | **UNVERIFIED — resolved by Step 2** (field names, not yet coded) |
| `minutes` | int | `elements[].minutes` | current-season cumulative minutes, added v1.1 for the shrinkage blend |
| `starts` | int | `elements[].starts` | added v1.1, watchlist use |
| `birth_date` | str (ISO date) | `elements[].birth_date` | added v1.1, watchlist age calc — live-verified already a plain string, no cast needed |
| `expected_goal_involvements_per_90` | float | `elements[].expected_goal_involvements_per_90` | added v1.1, watchlist — live-verified already float, no cast needed (unlike form/ep_next/selected_by_percent, which ARE strings) |
| `event_points` | int | `elements[].event_points` | added v1.9, outlier-dampening — most recent finished gameweek's score, live-verified already int |
| `penalties_order` | `int \| None` | `elements[].penalties_order` | added v2.0, set-piece bonus — 1 = primary taker |
| `direct_freekicks_order` | `int \| None` | `elements[].direct_freekicks_order` | added v2.0, set-piece bonus — 1 = primary taker |

## Name-matching rule (`research_agent.match_player`)

Returns the full ranked candidate list, not just a top pick. If ≥2 candidates score within 5
points of the top match, disambiguate using a team short-name or full-name token found in the
surrounding scrape text; if that still doesn't resolve it, log and drop the note. Never attach on
an ambiguous top-1 alone. Test against the real live duplicate set: `Wilson` x3, plus `Martinez`,
`Palmer`, `James`, `Henderson`, `Johnson`, `King`, `Gomez`, `Hughes`, `Kamara`, `Dasilva`,
`Patterson` x2.

## Pinned research-note schema (output of `research_agent`/`transfer_agent`, consumed by `projection` and `report`)

`{player_id (matched), source, note_type: injury|rotation|differential|captain_pick|
transfer_rumor, text, polarity: positive|negative|neutral}`. Deduped by `(player_id, source,
note_type)` before `projection` counts anything. `transfer_rumor` notes (v1.8) are the one
exception to "consumed by projection" — they're informational only (see Transfer Rumor Checking
below) and never enter the projected-points formula, only the report's Risk Flags section.

## Scrape sources — live-verified 2026-08-28, resolved by Step 4

Two working sources (Reddit was dropped — see below):

1. **Fantasy Football Fix** (`https://www.fantasyfootballfix.com/blog-index/`) — a real,
   weekly-updating, gameweek-specific blog. Full article text is freely readable, no login wall
   (live-verified: fetched the GW2 captaincy article's full body via a plain HTTP GET). Article
   URLs are discoverable from the blog index page (`<a href="/blog-index/{slug}/">`). Category
   filters exist (`?category=Captaincy`, `?category=Differentials`, `?category=Transfer%20Replacements`,
   etc.) but plain per-article scraping of the index's current article list is simpler and
   sufficient. Player mentions follow a consistent `Name (TEAM_CODE)` pattern in article body text
   (e.g. "Bruno Fernandes (IPS)"), which gives name+team together — no separate disambiguation
   step needed for this source.
2. **Fantasy Football Scout team-news** (`https://www.fantasyfootballscout.co.uk/category/team-news/`)
   — headline links only were expected to be free with full articles paywalled; **live-verified
   finding, more useful than expected**: the paywall is a client-side JS modal overlay, not a
   server-side content gate — the full article text (specific injury details, e.g. "Joelinton
   (unspecified), Dan Burn (ankle) and Will Osula (foot) remain out") is present in the
   plain-HTTP-fetched HTML. Headlines follow a `Surname1, Surname2, Surname3: TeamName injury
   latest for FPL Gameweek N` pattern for injury-focused posts (other posts are `Team v Team
   predicted line-ups...`, not player-specific — skip those). Team name appears after the colon,
   giving a team hint for disambiguating the comma-separated surnames before it.

**Reddit r/FantasyPL — dropped for v1, live-verified blocked (not merely assumed working from a
stale pattern)**: both `www.reddit.com/r/FantasyPL/*.json` and `old.reddit.com/r/FantasyPL/*.json`
returned 403 / a 302 redirect to a login page on 2026-08-28, even with a descriptive User-Agent —
Reddit's anti-scraping posture has tightened since the construction plan assumed the `.json` trick
worked. A real fix would need a registered (free) Reddit script-app + OAuth, which is a
Mihir-blocking credential step, not scraper code — deferred, not silently worked around.

**Politeness**: both sources are fetched with a descriptive `User-Agent` identifying this as a
low-volume personal project, a `time.sleep(1)` between requests, and a per-run cap on how many
articles are fetched (8) — this is a weekly personal tool, not a crawler.

## Failure policy

Each source's `scrape_source()` returns `(notes, status)`, `status ∈ {ok, failed, empty}` —
`empty` (200 OK, zero notes parsed — e.g. a silent site redesign) is surfaced exactly like
`failed`, never treated as quiet success. All notes pass through `validate_notes()` against the
schema above before reaching `projection`; an invalid note is dropped and counted, never coerced.
If every source reports `failed`/`empty`, the run proceeds on official-API-only data
(`data_mode = "official_only"`) and the report states this explicitly.

## Pinned projected-points formula (`projection.compute_projected_points`)

Deterministic Python, never an LLM call. In this exact order:

1. Raise `FPLTooEarlyError` if `current_gw < 3`. (GW1-2 was found degenerate in review — at GW1
   every player's `total_points` is 0, collapsing the formula to a price-tier tie-break with the
   expert nudge as the only real differentiator. Cut, not patched. First usable output is GW3.)
2. Base: `ep_next`, unmodified.
3. **Historical shrinkage blend** (v1.1, position-fallback added v1.2, 2-season pooling added
   v1.6): if the player has a historical prior — points-per-90 pooled across the last 2 completed
   seasons from `historical_agent.py` (min 450 minutes played, summed across both seasons before
   the threshold applies), matched by exact `(first_name, second_name)` first, falling back
   to the player's position-average pp90 (v1.2) if no exact name match exists (e.g. a promoted-
   team player with no PL record in either lookback season). Only a position with zero qualifying historical
   rows at all leaves a player with no prior (base stays unmodified `ep_next`) — blend it in
   weighted by how much the player has actually played THIS season: `shrinkage_weight =
   clamp(minutes / MINUTES_FULL_CONFIDENCE, 0, 1)`, `MINUTES_FULL_CONFIDENCE = 450` (five full
   matches). **v1.3: the blend only applies when the player is currently available** —
   `status in {'a','d'}` AND `chance_of_playing_next_round != 0` — otherwise base stays exactly
   `ep_next` regardless of the historical prior's size (a real bug found live: an injured player
   with thin current minutes got ~88% of his score from a historical prior that's irrelevant since
   he literally can't play the next match). **v2.1: the blend also requires `starts >= 1`** —
   otherwise base stays exactly `ep_next` regardless of the historical prior's size (a real bug
   found live: an officially-available player with 0 starts this season got ~99% of his score
   from a historical prior even though he's plainly not in his club's matchday plans).
   `base = shrinkage_weight * ep_next + (1 - shrinkage_weight) * historical_pp90`. This converges
   to pure `ep_next` once a player has real current-season minutes — it does NOT lift or revive
   the GW1-2 gate above (that gate exists because `ep_next` itself isn't populated league-wide
   that early, a different problem from one player's individually-thin sample). **v1.9 outlier
   dampening**: before computing `shrinkage_weight`, if a single recent gameweek
   (`event_points`) is both ≥15 points AND ≥50% of season `total_points`, halve the effective
   minutes fed into the weight calc — a hat-trick-sized single game shouldn't be trusted as a
   real 2-game sample. Self-limiting as more gameweeks accumulate.
4. **Set-piece duty bonus** (v2.0): `+5%` per confirmed `order == 1` role (`penalties_order`,
   `direct_freekicks_order` — corners/indirect excluded), max `+10%` stacking both. A real,
   durable, official role assignment (unlike the recency-biased current-season signal above), so
   it applies here as a genuine input to `projected_points`, not as informational-only content
   like the Watchlist/transfer rumors.
5. Fixture term: `base *= clamp(1 + 0.05 * (3 - fixture_difficulty_next5), 0.85, 1.15)`. FDR 1
   (easiest) to 5 (hardest); average FDR 3 is neutral.
6. Expert nudge: count deduped positive/negative notes per player, `nudge = clamp(0.05 *
   (n_pos - n_neg), -0.15, +0.15)`, `adjusted = base * (1 + nudge)`. Neutral notes contribute 0.
   Worked example: 2 positive + 1 negative -> `nudge = clamp(0.05, -0.15, 0.15) = 0.05` ->
   `adjusted = base * 1.05`.
7. Disputed-availability override: if `status == 'a'` but ≥2 independent sources carry a
   `negative`-polarity `injury` note on the same player, set `disputed_availability = true` and
   apply a fixed `0.4x` multiplier to `adjusted`, **outside** the ±15% nudge cap.
8. Hard availability check: **no-op by design** on this GW3+-only formula — `ep_next` already
   reflects `status`/`chance_of_playing_next_round` (live-verified 2026-08-28: every
   `status in {i,s,u}` and every `chance_of_playing_next_round == 0` player already has
   `ep_next == 0.0`). Do NOT multiply by `chance_of_playing_next_round / 100` again — that would
   double-discount. Keep a defensive `assert status in {'a','d','i','s','u'}`, raise on anything
   else.
9. Cap: `projected_points = max(0, adjusted)`.

## Breakout Watchlist (v1.1, informational only — never influences squad selection)

A new report section, computed straight from live current-season fields already in player_table
(no historical dependency): age `< 23` (from `birth_date`), `90 <= minutes < 900` (some real
signal, but still emerging — not yet an established regular), `expected_goal_involvements_per_90
>= 0.4`, `selected_by_percent < 10.0` (genuine differential). Top 5 by
`expected_goal_involvements_per_90` descending. Pure information for Mihir to act on manually —
does not feed the optimizer, does not change `projected_points`.

## Transfer Rumor Checking (v1.8, informational only — never influences squad selection)

`transfer_agent.py`, gated on `is_transfer_window_open()` — only scrapes when a real transfer
window is open (Mihir's point: checking weekly year-round when nothing's happening is wasted
effort). Summer window: **live-verified 2026-08-30**, closes 2026-09-01 23:00 BST. Winter window:
**2027-01-01 to 2027-02-03, UNVERIFIED** — a historical-pattern approximation, not live-checked;
re-verify before it matters (a future session, closer to January). Outside any window,
`source_status["bbc_gossip"] = "skipped"` and `_compute_data_mode` excludes it from the
full/degraded calculation entirely — a deliberate skip must never read as a data failure.

Source: BBC Sport's gossip column (`bbc.com/sport/football/gossip`) — live-verified free, no
login, structured `Team + verb + Player - Day's gossip` headlines, ~24 recent headlines per pull.
Scoped to the **final squad only** (15 players, not the full ~600-player pool) — cheap, focused on
players Mihir actually has picked. A name match (web_name substring or a tight fuzzy match on
full_name, threshold 90) produces a `transfer_rumor` note, `polarity: negative` always (any
current transfer chatter is treated as a continuity risk, direction/certainty not parsed from
headline grammar — deliberately conservative). Surfaces through the report's Risk Flags section
(now scoped to the full squad, not just XI, since a bench player's transfer-out risk still matters
for future selection even though it doesn't affect this week's score). Single-source coverage is a
known, real limitation — will catch some real rumors, not all (live-verified: caught a real
Gakpo-to-Man-City rumor 2026-08-30, missed a separately-reported Elliott-to-Brighton rumor that
came from a different outlet in a one-off manual check).

## Legal starting formations (literal list, used by the optimizer)

3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-2-3, 5-3-2, 5-4-1 (DEF-MID-FWD).

## Pinned recommendation-dict schema (output of `build_recommendation`, consumed by report/orchestrator)

`target_gw (int), deadline_epoch (int), squad (list[player_id]), xi (list[player_id]), bench_gk
(player_id), bench_order (list[player_id], len 3, outfield bench sorted desc by
projected_points), captain (player_id), vice (player_id), cost_tenths (int), bank_tenths (int),
research_notes_by_player (dict[player_id, list[note]]), source_status (dict[source, {"ok"|
"failed"|"empty", note_count}]), data_mode ("full"|"degraded"|"official_only"), watchlist
(list[player_id], added v1.1 — see Breakout Watchlist section below; informational only, not part
of squad/xi)`.

## Report/log paths

`reports/GW{n}.md` — overwrite-latest. `reports/archive/GW{n}-{ISO-timestamp}.md` — every run
kept. `logs/run-log.csv` columns: `timestamp, target_gw, status (ok|no_op|error|MISSED_GW),
data_mode, sources_ok, sources_failed`.

## Deferred, not built in v1

Transfer suggestions and chip timing — no existing FPL team to diff transfers against yet.
`select_squad()` has no `locked_in`/`excluded` params.

## Known gap, deferred (flagged, not silently assumed away)

Blank/double-gameweeks (a team with 0 or 2 fixtures in one gameweek): not modeled in v1. Current
fixture list (2026-08-28) has exactly one fixture per team per gameweek — this WILL change around
December once cup postponements start. `data_agent` logs a `DGW/BGW detected` warning when it sees
one so the gap is visible, but does not yet correct for it. Revisit before December.
