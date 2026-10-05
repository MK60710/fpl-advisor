# Construction Plan: FPL Advisor

Source spec: `~/Projects/FPL-Advisor/SPEC.md` (approach C approved by Mihir 2026-08-28).
Pattern being adapted: `~/Projects/Stock-Research/plans/phase0-phase1-construction-plan.md`
(shared conventions: `framework.md` as pinned single source of truth, direct-mode local git,
deterministic math where correctness matters, graceful-degradation over hard failure).

**Revision note (2026-08-28):** this plan went through an adversarial review pass (opus) that
verified every FPL-domain claim live against the real API rather than from memory, and found 19
MUST-FIX issues — a two-stage optimizer that doesn't actually maximize what FPL scores, several
un-pinned/wrong field types and derivations, a deadline-gameweek selector that would report on
already-locked gameweeks, and a Tue+Fri launchd cadence proven (by simulation against all 38 real
2026-27 deadlines) to silently skip 5 real gameweeks. All 19 are fixed inline below, cited by
number where the fix responds to a specific finding. 11 worth-noting items are folded in too
where cheap; the two flagged as needing Mihir's own confirmation (blank/double-gameweek handling,
scrape-source paywall status) are called out explicitly rather than silently resolved.

## Mode

**Direct mode, no GitHub PR/CI workflow.** Personal, solo project — a local-only `git init`
(no remote) purely for step-by-step checkpoints and rollback safety. Step 1 includes this.

## Dependency graph

```
Step 1 (scaffold + framework.md + pinned interfaces + git init)
  ├─> Step 2 (data_agent.py: official API + player table, live-verifies         ─┐
  │           every UNVERIFIED field + game_settings before Steps 4/5 trust      │
  │           them)                                                              │
  └─> Step 3 (optimizer.py: joint ILP, built against pinned schema + two         ─┤ parallel pair —
              small exact-answer fixtures, no real data needed)                   │ schema pinned in
                                                                                   │ Step 1
         v (Step 2 only)                                                         │
Step 4 (research_agent.py: scraping, name-matched against Step 2's player         │
        table, disambiguated against real duplicate-name data)                   │
         v                                                                        │
Step 5 (projection.py: blends Step 2 + Step 4 into projected_points per           │
        framework.md's pinned formula — MODEL: strongest, this is the           │
        correctness-critical deterministic core)                                 │
         └───────────────────────┬───────────────────────────────────────────────┘
                                  v
                  Step 6 (integration: projection -> optimizer ->
                          squad + XI + bench order + captain/vice,
                          validated by a shared validate_squad())
                                  v
                  Step 7 (report.py: weekly markdown output)
                                  v
                  Step 8 (weekly orchestrator: correct deadline-gameweek
                          selector + daily-cadence scheduling + miss detection)
                                  v
                  Step 9 (fpl-scan skill for conversational ad hoc runs)
```

---

## Step 1 — Scaffold, framework.md, pinned interfaces, local git init
**Model:** strongest
**Depends on:** nothing

Context brief: `framework.md` is the single source of truth other steps are built against without
reading each other's code. Every field below is either a **live-verified fact** (cite: pulled from
`bootstrap-static`/`fixtures` 2026-08-28) or explicitly marked `UNVERIFIED — resolved by Step N`,
per finding #9/#13 — no plain assertions with unstated confidence.

**Squad rules (live-verified 2026-08-28):** 15 players — 2 GKP, 5 DEF, 5 MID, 3 FWD; budget is
**1000 tenths of £1m** (`game_settings.squad_total_spend`); max 3 players per real club
(`game_settings.squad_team_limit`); starting XI: 1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD, 11 total
(`element_types[].squad_min_play`/`squad_max_play`); captain = 2x, vice-captain activates if
captain doesn't play. **Only 8 formations are legal** (DEF-MID-FWD): 3-4-3, 3-5-2, 4-3-3, 4-4-2,
4-5-1, 5-2-3, 5-3-2, 5-4-1 — pin this literal list, it's the cheapest exact way to pick a starting
XI (finding #1, #2).

**Budget arithmetic (finding #3 — fixes a real float-tolerance bug):** all optimizer and validation
arithmetic uses **integer `cost_tenths`** against **`budget_tenths = 1000`**. `price_m (float)` is
derived (`cost_tenths / 10`) for display/report purposes ONLY — never fed into the solver or any
`==`/`<=` budget check.

**Pinned player-table schema** (output of Step 2, consumed by Steps 3-7) — every column has a
name, a dtype, and (finding #13) a stated source field, since the raw API returns several numeric
fields as **strings**:

| column | dtype | source | note |
|---|---|---|---|
| `player_id` | int | `elements[].id` | |
| `web_name` | str | `elements[].web_name` | **not unique** — 15 live duplicates incl. `Wilson` x3 (finding #12) |
| `first_name`, `second_name`, `full_name` | str | `elements[].first_name`/`second_name`/concat | added per finding #11, needed for disambiguation |
| `team_id` | int | `elements[].team` | added per finding #11 — required to join fixtures, `team_short` alone can't |
| `team_short` | str | `teams[].short_name` (joined via `team_id`) | |
| `position` | str, one of GKP/DEF/MID/FWD | derived from `element_types[].singular_name_short` via `elements[].element_type` (an **int**, not a string — finding #13) | **raise** on any `element_type` not in the expected set, never silently drop/mis-slot |
| `cost_tenths` | int | `elements[].now_cost` (already tenths, no division) | primary budget field, finding #3 |
| `price_m` | float | `cost_tenths / 10` | display only |
| `form` | float | `elements[].form` (cast from **str**, finding #13) | |
| `total_points` | int | `elements[].total_points` | |
| `ep_next` | float | `elements[].ep_next` (cast from **str**) | |
| `selected_by_percent` | float | `elements[].selected_by_percent` (cast from **str**) | |
| `chance_of_playing_next_round` | `int \| None` | `elements[].chance_of_playing_next_round` (finding #13: it's int-or-null, NOT float 0-100 as originally drafted) | |
| `status` | str, one of `a,d,i,s,u` | `elements[].status` | **UNVERIFIED-RESOLVED**: live-verified 2026-08-28 that only these 5 values occur (no `n`) — Step 2 re-confirms on every run and raises on an unexpected value rather than silently accepting one |
| `news` | str | `elements[].news` | |
| `fixture_difficulty_next5` | float | avg of `fixtures[].team_h_difficulty`/`team_a_difficulty` for this team's next 5 unfinished fixtures (home/away branch on `team_h`/`team_a`, filtered `finished == false`, `event is not null`) | source fields named explicitly per finding #13 |

**Runtime rule-drift guard (finding #13):** Step 2 must assert, on every run, that
`game_settings.squad_squadsize == 15`, `squad_squadplay == 11`, `squad_team_limit == 3`,
`squad_total_spend == 1000`, and each `element_types[].squad_select/squad_min_play/squad_max_play`
matches this table — raise loudly on mismatch (a mid-season FPL rule change must fail loudly, not
silently produce an illegal squad).

**Pinned research-note schema** (output of Step 4, consumed by Step 5): `{player_id (matched),
source, note_type: injury|rotation|differential|captain_pick, text, polarity: positive|negative|
neutral}`. Deduped by `(player_id, source, note_type)` before Step 5 counts anything (finding #7 —
prevents 3 sources naming the same consensus captain from auto-maxing the nudge).

**Name-matching rule (finding #12 — live duplicate names make simple threshold-matching unsafe):**
`match_player` returns the full ranked candidate list, not just a top pick. If ≥2 candidates score
within 5 points of the top match, disambiguate using a team short-name or full-name token found in
the surrounding scrape text; if that still doesn't resolve it, log and drop the note. Never attach
on an ambiguous top-1 alone.

**Pinned projected-points formula** (implemented in Step 5, deterministic, not LLM judgment —
finding #6, #7, #8, #21 all resolved here):
- **Base**: `ep_next`, always. (Finding #8: the originally-planned GW1-2 fallback was degenerate —
  at GW1 every player's `total_points` is 0, collapsing the formula to a price-tier tie-break with
  the ±15% nudge as the only real differentiator, inverting the stated "nudge is an adjustment on
  top of hard data" principle. Cut entirely rather than patched — see SPEC's updated
  out-of-scope section. `compute_projected_points` raises `FPLTooEarlyError` if `current_gw < 3`.)
- **Fixture term** (finding #5 — `fixture_difficulty_next5` was pinned in the schema but consumed
  by nothing; either give it a real, numbered role or delete the column, and it's genuinely useful
  signal so: give it one): `base *= clamp(1 + 0.05 * (3 - fixture_difficulty_next5), 0.85, 1.15)`.
  FDR is 1 (easiest) to 5 (hardest); an average FDR of 3 is neutral.
- **Expert nudge** (finding #7, pinned exactly with a worked example): count deduped positive and
  negative notes per player, `nudge = clamp(0.05 * (n_pos - n_neg), -0.15, +0.15)`,
  `adjusted = base * (1 + nudge)`. Neutral notes contribute 0. Worked example: 2 positive + 1
  negative note -> `nudge = clamp(0.05, -0.15, 0.15) = 0.05` -> `adjusted = base * 1.05`.
- **Disputed-availability override** (finding #6 — this is the scraper's actual highest-value
  signal, and the original design gave it almost no teeth): if `status == 'a'` (API shows no
  official issue) but ≥2 independent sources carry a `negative`-polarity `injury` note on the same
  player, set `disputed_availability = true` and apply a fixed `0.4x` multiplier to `adjusted`,
  **outside** the ±15% nudge cap, before the hard override step below.
- **Hard availability override** (finding #6 — the original design mostly duplicated what `ep_next`
  already encodes; live-verified 2026-08-28 that every `status ∈ {i,s,u}` player and every
  `chance_of_playing_next_round == 0` player already has `ep_next == 0.0`, so re-zeroing here is a
  no-op on the GW3+ path, and multiplying by `chance/100` a second time was double-discounting):
  on the GW3+-only formula, this step is now a **no-op by design and documented as such** — do not
  re-apply a `chance_of_playing_next_round` discount on top of `ep_next`, which already reflects
  it. Keep a defensive `assert status in {'a','d','i','s','u'}` (raise, don't silently pass through
  an unexpected value) but do not multiply again.
- **Cap**: `projected_points = max(0, adjusted)`.

**Scrape source list + verified-access requirement (finding #26 — flagged for Mihir, not silently
resolved):** intended sources are Fantasy Football Scout, an FPL projected-points second opinion
site, and r/FantasyPL. Fantasy Football Scout's team-news/projection content is largely
members-only and Reddit's HTML is aggressively rate-limited — **before Step 4 is built, confirm
each source's actual free/fetchable surface** (Reddit's public `.json` endpoints with a real
descriptive User-Agent are the known-working path for that one) and pin the exact URL pattern per
source here, replacing this placeholder list. If a source turns out paywalled, pin its replacement
here rather than leaving "or equivalent" in code.

**Failure policy** (finding #14 — degradation must be explicit, not just exception-based): each
source's `scrape_source()` returns `(notes, status)` where `status ∈ {ok, failed, empty}` —
`empty` (200 OK, zero notes parsed — e.g. a silent site redesign) is surfaced exactly like
`failed`, not treated as a quiet success. All notes pass through `validate_notes()` against the
pinned schema above before reaching Step 5; a note with a missing/invalid field is dropped and
counted, never silently coerced. If every source reports `failed` or `empty`, the run proceeds on
official-API-only data (`data_mode = "official_only"`) and the report says so explicitly.

**Pinned recommendation-dict schema** (output of Step 6, consumed by Steps 7/8 — finding #10, this
interface was previously un-pinned and Steps 7/8 needed fields Step 6 never returned): `target_gw
(int), deadline_epoch (int), squad (list[player_id]), xi (list[player_id]), bench_gk (player_id),
bench_order (list[player_id], len 3, outfield bench sorted desc by projected_points), captain
(player_id), vice (player_id), cost_tenths (int), bank_tenths (int), research_notes_by_player
(dict[player_id, list[note]]), source_status (dict[source, {"ok"|"failed"|"empty", note_count}]),
data_mode ("full"|"degraded"|"official_only")`.

**Report/log path + idempotency (finding #24):** `reports/GW{n}.md` is overwrite-latest;
`reports/archive/GW{n}-{ISO-timestamp}.md` keeps every run. `logs/run-log.csv` columns:
`timestamp, target_gw, status (ok|no_op|error), data_mode, sources_ok, sources_failed`, plus a
`MISSED_GW` row type (see Step 8, finding #17).

**Deferred, not built in v1** (finding #23): transfer suggestions and chip timing — Mihir has no
existing FPL team yet, so there's nothing to diff transfers against. `select_squad()` (Step 3) has
no `locked_in`/`excluded` params; don't add unused surface.

**Changelog header** starting v1.0. Any formula/weight/schema change gets an entry — including
Step 2's own live-verification of the UNVERIFIED `status` block (finding #25: Step 2 may only
replace the UNVERIFIED blocks it's named as resolving; any other framework.md edit bumps the
version and re-triggers review, since Step 3 reads this file in parallel).

Tasks:
- [ ] Create directory tree: `agents/`, `data/`, `reports/`, `reports/archive/`, `logs/`, `tests/`, `.cache/`
- [ ] `git init` (no remote), `.gitignore` (`.env`, `__pycache__`, `.cache/` — reports and logs
      ARE tracked, they're the actual deliverable, not scratch output)
- [ ] Write `framework.md` per the full spec above, including the literal 8-formation list and the
      worked nudge example
- [ ] Write `requirements.txt` starter: `requests`, `pandas`, `pulp`, `beautifulsoup4`, `rapidfuzz`, `python-dotenv`
- [ ] `git add -A && git commit -m "Step 1: scaffold + framework.md + pinned interfaces"`

Verification (finding #19 — concrete, checkable, not an impression): every schema column above has
a name + dtype + source field; the nudge formula's worked example is present with its expected
numeric output; every UNVERIFIED block explicitly names the step that resolves it; the 8-formation
list is a literal enumeration, not a formula.

Exit criteria: framework.md is complete and internally consistent; no code yet.

---

## Step 2 — data_agent.py (official API + player table, live-verification pass)
**Model:** default
**Depends on:** Step 1
**Parallel with:** Step 3

Context brief: Thin, reliable wrappers, PLUS the live-verification responsibility framework.md's
UNVERIFIED blocks assign to this step. `fetch_bootstrap() -> dict` (raw `bootstrap-static/` JSON,
cached to `.cache/` with a short TTL — prices/injury status change during the week).
`fetch_fixtures() -> list[dict]` (raw `fixtures/` JSON). `build_player_table(bootstrap, fixtures) ->
DataFrame` implementing Step 1's pinned schema exactly, including casting the string-typed API
fields (`form`, `ep_next`, `selected_by_percent`) to float and deriving `position` from
`element_types` (raising on an unrecognized `element_type`).

**First real action in this step:** pull live `bootstrap-static`, run the `game_settings`/
`element_types` rule-drift assertions from framework.md, print the distinct `status` values
actually present, and update framework.md's `status` block from UNVERIFIED to confirmed (per
Step 1's changelog rule — this is the one block Step 2 is authorized to edit).

**Blank/double-gameweek risk (finding #20 — flagged, not silently assumed away):** the live
fixture list right now has exactly one fixture per team per gameweek with zero `event: null`
entries, because it's GW2 and no postponements have happened yet. Historically, blanks (a team
with 0 fixtures) and doubles (2 fixtures) start appearing from around December once cup
competitions force reschedules — `ep_next` is a single-gameweek figure that won't scale correctly
for a double, and a blank team's players should show sharply reduced value. This build does not
yet handle that case correctly, and it WILL occur later this season. Add the test now so the gap
is visible immediately rather than silently mis-modeling in December.

Tasks:
- [ ] Run the `game_settings`/`element_types` rule-drift assertions; raise loudly on any mismatch
- [ ] Confirm live `status` values, update framework.md's UNVERIFIED block to confirmed (changelog entry)
- [ ] Implement `fetch_bootstrap()`, `fetch_fixtures()` with caching + retry/backoff, raise a typed
      `FPLDataUnavailableError` rather than silently returning partial data on failure
- [ ] Implement `build_player_table()` matching Step 1's pinned schema and dtypes exactly
- [ ] `tests/test_data_agent.py`: `assert set(df.columns) == FRAMEWORK_COLUMNS` and `df.dtypes`
      against a pinned dict (not a manual "looks right" check — finding #19); `cost_tenths` sanity
      assertion using the real live max (155, i.e. £15.5m) rather than a vague "known expensive
      player" check
- [ ] **DGW/BGW guard**: add a synthetic fixture-list fixture with a team appearing 0 times and a
      team appearing 2 times in one gameweek; assert `build_player_table` logs a `DGW/BGW detected`
      warning (surfaced later in the report) rather than silently producing a wrong average
- [ ] `git commit -m "Step 2: data_agent.py"` (run the full test suite first)

Verification: `pytest tests/test_data_agent.py -v` including the column/dtype assertion and the
DGW/BGW guard test.

Exit criteria: a real, cached, schema-correct player table can be produced from a live API pull;
framework.md's status block is confirmed, not placeholder.

---

## Step 3 — optimizer.py (joint ILP: squad + XI + bench order + captain/vice)
**Model:** strongest
**Depends on:** Step 1 (pinned schema only — built and tested against small exact-answer
fixtures, not real data, so it doesn't block on Step 2)
**Parallel with:** Step 2

Context brief: Squad selection is a knapsack/ILP problem and must be solved exactly — this is
where correctness matters most, per the approved design. **Finding #1 (the single most important
fix in this plan): a two-stage "pick 15 then pick 11 from those 15" design is provably
suboptimal**, because FPL only scores the starting XI plus the captain's doubled points — spending
budget on bench players optimized for squad-total ignores what's actually scored. This must be
**one joint ILP**, not two stages, and the earlier "second small ILP or greedy" sub-selection
language is gone — greedy is not equivalent to exact and is not an acceptable substitute.

`select_squad(player_table_with_projection, budget_tenths=1000) -> dict` (matching Step 1's
pinned recommendation-dict fields it's responsible for: `squad, xi, bench_gk, bench_order,
captain, vice, cost_tenths`). Formulation: binary `sq_i` (in 15), `xi_i` (in starting 11, requires
`xi_i <= sq_i`), `capt_i` (is captain, requires `capt_i <= xi_i`, `sum(capt) == 1`). Objective:
`maximize sum(pp_i * xi_i) + sum(pp_i * capt_i)` — this is what FPL actually scores. Constraints:
`sum(sq) == 15`; squad position counts 2/5/5/3; `sum(xi) == 11`; XI position counts from the
pinned 8-formation list (GKP==1, DEF in [3,5], MID in [2,5], FWD in [1,3]); budget
`sum(cost_tenths_i * sq_i) <= budget_tenths`; club cap `sum(sq_i for team) <= 3` per team.
Vice-captain = highest-`projected_points` starter excluding the chosen captain. `bench_gk` = the
non-starting GKP; `bench_order` = the 3 non-starting outfielders sorted descending by
`projected_points` (finding #4 — SPEC requires bench order, no earlier step produced it).

**Deterministic tie-break** (finding #22 — a near-flat objective makes CBC ties likely, which
would otherwise make the same inputs produce a different squad on different runs): objective
becomes `maximize sum(pp_i*xi_i) + sum(pp_i*capt_i) - 1e-6 * sum(cost_tenths_i * sq_i)`, and use
`PULP_CBC_CMD(msg=0)` with a fixed argument set.

**Solver-status guard** (finding #2 — without this, an infeasible/undefined solve silently returns
a partial squad rather than failing): after solving, `assert pulp.LpStatus[prob.status] ==
"Optimal"`, else raise a typed `FPLInfeasibleError` — never read `.value()` off a non-optimal
solve.

Tasks:
- [ ] `pip install pulp`, add to `requirements.txt`
- [ ] Implement the joint ILP exactly as formulated above, including the tie-break term
- [ ] Implement the solver-status guard and `FPLInfeasibleError`
- [ ] `tests/test_optimizer.py` (finding #2 — the original "40-60 player brute-force" test is not
      runnable, C(60,15) is ~5x10^13; replaced with two small, hand-computable fixtures):
      (a) exactly 15 legal players spanning the position/budget/club constraints with a unique
      feasible solution — assert the returned squad IS that exact set of 15;
      (b) ~18 players constructed so the optimal XI/captain choice is hand-computable — assert the
      exact objective value, not just feasibility
- [ ] **Two infeasibility fixtures** (finding #2 — the original single "all players over
      budget/15" case doesn't cover the failure mode a real run will actually hit): budget-infeasible
      (as before) AND club-cap-infeasible (only 4 real clubs represented, so 15 players at max 3/club
      is structurally impossible) — both must raise `FPLInfeasibleError`, not return a partial squad
- [ ] Implement a shared `validate_squad(rec) -> None` (finding #19): asserts 15 players, 2/5/5/3,
      ≤3 per club, `cost_tenths <= budget_tenths`, 11 starters forming one of the 8 legal
      formations, captain in xi, vice in xi, vice != captain — raises on any violation. This one
      implementation is reused (not restated) by Steps 6, 8, 9.
- [ ] `git commit -m "Step 3: optimizer.py + tests"` (run the full test suite first)

Verification: `pytest tests/test_optimizer.py -v` — both exact-answer fixtures pass with the exact
expected squad/objective value, both infeasibility fixtures raise, `validate_squad` has its own
unit tests for each individual constraint it checks.

Exit criteria: solver proven exactly correct on two small hand-verifiable fixtures, and proven to
fail loudly (not silently) on two distinct infeasibility shapes — before ever touching real data.

---

## Step 4 — research_agent.py (expert scraping, name-matched)
**Model:** default
**Depends on:** Step 2 (needs real player names/duplicates to match against)

Context brief: Implements Step 1's pinned research-note schema and failure policy. Before writing
scraper code, **confirm each source's real fetchable surface** per framework.md's flagged note
(finding #26) — do not assume Fantasy Football Scout's projections are free or that Reddit's HTML
pages aren't rate-limited; use Reddit's public `.json` endpoints with a descriptive User-Agent.
`scrape_source(source_name, url) -> (list[dict], status)` per source, each wrapped in its own
try/except AND returning an explicit `status ∈ {ok, failed, empty}` (finding #14 — a 200-OK page
that no longer matches expected structure must report `empty`, not silently return zero notes as
if that were a normal quiet day). `match_player(raw_name, player_table) -> player_id | None`
implementing Step 1's ranked-candidate disambiguation rule (finding #12) — tested against the
**real** live duplicate-name set (`Wilson` x3, plus `Martinez`/`Palmer`/`James`/`Henderson`/
`Johnson`/`King`/`Gomez`/`Hughes`/`Kamara`/`Dasilva`/`Patterson` x2), not a synthetic near-miss.
`validate_notes(notes) -> (valid, dropped_count)` drops schema-invalid notes (finding #14) rather
than letting a malformed note silently reach Step 5 as a false zero-nudge. `run_research
(player_table) -> (notes, source_status)` orchestrates all sources.

Tasks:
- [ ] Confirm and pin each source's exact fetchable URL pattern in framework.md before scraping code is written
- [ ] Implement per-source scrapers returning `(notes, status)`, isolated try/except per source
- [ ] Implement `match_player()` with the ranked-candidate disambiguation rule
- [ ] Implement `validate_notes()`
- [ ] Implement `run_research()` orchestration, deduping notes by `(player_id, source, note_type)`
- [ ] `tests/test_research_agent.py`: mock HTML fixtures per source (don't depend on live site
      structure for the test suite); name-matching test using the real duplicate-name list above;
      a **forced-degradation test** (finding #19) — monkeypatch one scraper to raise and another to
      return 200-OK garbage, assert the run completes, both are marked correctly in
      `source_status`, and total note count reflects only the successful source
- [ ] `git commit -m "Step 4: research_agent.py"` (run the full test suite first)

Verification: `pytest tests/test_research_agent.py -v` including the forced-degradation test; one
live smoke-test run against real sites.

Exit criteria: scraping produces schema-correct, correctly-disambiguated notes; a mixed
failed+garbage-source run is proven non-blocking and correctly flagged, not just "unreachable
right now."

---

## Step 5 — projection.py (deterministic blend) — CORRECTNESS-CRITICAL
**Model:** strongest (upgraded from default per finding #28 — this is the deterministic core the
whole design's value proposition rests on, and issues #6/#7/#8 all lived here)
**Depends on:** Step 2, Step 4

Context brief: Implements framework.md's pinned projected-points formula exactly — deterministic
Python, never an LLM call. `compute_projected_points(player_table, research_notes, current_gw:
int) -> DataFrame` adds a `projected_points` column following, in order: raise `FPLTooEarlyError`
if `current_gw < 3`; base = `ep_next`; fixture-term multiply; deduped-nudge multiply (capped
±15%); disputed-availability 0.4x override (≥2 independent negative injury notes on a `status=='a'`
player) applied outside the nudge cap; final `max(0, ...)` floor. No second
`chance_of_playing_next_round` discount — `ep_next` already encodes it (live-verified, see
framework.md).

Tasks:
- [ ] Implement the formula exactly as pinned, in the stated order of operations
- [ ] Implement the deduped nudge aggregation and the disputed-availability override
- [ ] `tests/test_projection.py`: `current_gw < 3` raises; fixture-term direction test (easy
      fixtures raise projected_points, hard fixtures lower it); nudge-cap test with >3 same-type
      notes from one source (must dedupe, not stack); disputed-availability test (a `status=='a'`
      player with 2 negative injury notes must drop to the 0.4x band regardless of a simultaneous
      positive nudge from other notes); an explicit test that `projected_points` for a `status in
      {i,s,u}` fixture equals its `ep_next` unmodified by any override multiply (proving the old
      double-discount is gone)
- [ ] `git commit -m "Step 5: projection.py"` (run the full test suite first)

Verification: `pytest tests/test_projection.py -v`, all listed cases including the
no-double-discount regression test.

Exit criteria: formula matches framework.md exactly, in the pinned order of operations.

---

## Step 6 — Integration: projection -> optimizer -> squad/XI/bench/captain
**Model:** default
**Depends on:** Step 3, Step 5

Context brief: Wires Step 2's real player table through Step 4's research and Step 5's projection
into Step 3's `select_squad()` for the first time against real live data. Produces
`build_recommendation(current_gw: int) -> dict` returning the full pinned recommendation-dict
schema from framework.md (finding #10 — this interface is now fully specified, so Steps 7/8 have
every field they need: `research_notes_by_player`, `source_status`, `data_mode` included, not just
squad/cost).

Tasks:
- [ ] Implement `build_recommendation()` calling Steps 2/4/5/3 in order, assembling the full pinned dict
- [ ] Call Step 3's `validate_squad()` on the result before returning — a real end-to-end run must
      pass the same validator the optimizer's own tests use (finding #19, shared not restated)
- [ ] `tests/test_integration.py`: one real live run, feed the returned dict through
      `validate_squad()` and assert no exception
- [ ] `git commit -m "Step 6: integration"` (run the full test suite first)

Verification: `validate_squad()` passes on a real live-data run's output — a concrete assertion,
not a manual "no obviously wrong picks" read (finding #19).

Exit criteria: a real live squad recommendation, matching the full pinned schema, can be produced
start to finish and passes validation.

---

## Step 7 — report.py
**Model:** default
**Depends on:** Step 6

Context brief: `write_report(recommendation: dict, out_path) -> None` — a markdown file: squad
table (name, team, position, price_m, projected_points), starting XI vs GK-bench vs bench-order
split (finding #4), captain/vice-captain with one-line rationale, risk flags section (any starter
with `chance_of_playing_next_round < 100`, a matched injury/rotation note, or
`disputed_availability`), `bank_tenths` converted to £m for display, and — reading
`recommendation['data_mode']` and `source_status` directly (now available per Step 6's fix of
finding #10) — an explicit degraded-data note when `data_mode != "full"`, naming which sources
failed. Writes to `reports/GW{target_gw}.md` (overwrite-latest) and
`reports/archive/GW{target_gw}-{ISO-timestamp}.md` per framework.md's pinned path convention
(finding #24).

Tasks:
- [ ] Implement `write_report()` per the sections above, including bench order and the data_mode note
- [ ] `tests/test_report.py`: a fixture recommendation dict with `data_mode="degraded"` and one
      injured starter — assert both the degradation note and the risk flag appear in the output
- [ ] `git commit -m "Step 7: report.py"` (run the full test suite first)

Verification: run against Step 6's real output, open and read the resulting markdown file.

Exit criteria: a readable, complete weekly report — including bench order and data-mode
transparency — is produced from a real recommendation.

---

## Step 8 — Weekly orchestrator: correct deadline-gameweek selector + daily-cadence scheduling
**Model:** default
**Depends on:** Step 7

Context brief: Two of the plan's most consequential fixes live here.

**Deadline-gameweek selector (finding #16 — the original "next unfinished gameweek" design is
provably wrong):** live-verified that a gameweek stays `finished: false` from its deadline until
Monday/Tuesday processing, and that `is_current`/`is_next` can point at an already-deadlined
gameweek in that window — so "next unfinished" or "is_current" would produce a report for a
gameweek Mihir can no longer act on. **Correct selector**: `target_gw` = the event with the
smallest `deadline_time_epoch` strictly greater than `time.time()` (all comparisons on epoch
seconds, never a constructed naive datetime — finding #18, since deadlines are UTC and Mihir's
machine moves between US Eastern and IST). If no such event exists (post-GW38), log a `status:
no_op` heartbeat row and exit 0.

**Scheduling (finding #15, #17 — the original "Friday evening" assumption and Tue+Fri cadence are
both wrong and provably lose gameweeks):** live-verified the actual rule holds for all 38
gameweeks this season: **deadline = exactly 90 minutes before the first kickoff of that
gameweek**. The real weekday distribution is Sat 26 / Fri 5 / Wed 5 / Sun 2 — NOT
predominantly-Friday. A Tue+Fri cadence, simulated against all 38 real deadlines, misses 5
Wednesday-deadline gameweeks entirely (GW13, 18, 20, 25, 28) because the preceding Tuesday often
still resolves to the prior (not-yet-finished) gameweek under the old selector, and the following
Friday is already past the Wednesday deadline. **Fix: launchd runs daily at ~07:00 local**, gated
by "does `target_gw`'s deadline fall within the next `N=3` days" — a no-op day costs nothing and
removes every single-point-of-failure gap a twice-weekly schedule had.

**Miss detection (finding #17):** since a heartbeat log only ever records runs that happened, it
can't detect a launchd failure (laptop closed, missed wake) on its own. Each run additionally
compares the highest `GW{n}.md` present in `reports/` against every event whose deadline has now
passed; any gap writes a `MISSED_GW` row to `logs/run-log.csv` and the report notes it prominently
next time a report IS produced.

Tasks:
- [ ] Implement the correct `target_gw` selector (epoch-based, per above) — unit-test with frozen
      clocks: deadline in 6h, in 3 days, in 10 days, and none (post-season)
- [ ] Implement `run_weekly.py`: selector -> `build_recommendation()` -> `write_report()` -> heartbeat log
- [ ] Implement the "within N=3 days" guard and the `MISSED_GW` detection pass
- [ ] Write the launchd plist (**daily**, ~07:00 local), install it
- [ ] `git commit -m "Step 8: weekly orchestrator + scheduling"` (run the full test suite first)

Verification (finding #19 — "job is loaded" alone doesn't prove it's correct): the frozen-clock
selector/guard unit tests all pass; PLUS one real `launchctl kickstart` producing an actual
heartbeat row; PLUS a forced-gap test (delete a report, run again, assert `MISSED_GW` is logged).

Exit criteria: the pipeline runs unattended daily, self-gates on a correctly-selected real
deadline, and detects its own missed runs rather than failing silently.

---

## Step 9 — fpl-scan skill (conversational ad hoc trigger)
**Model:** default
**Depends on:** all prior steps

Context brief: Steps 6 and 8 already performed real end-to-end runs against live data (finding
#30 — a third redundant "run it once more" checkbox adds nothing). This step's actual deliverable
is `~/.claude/skills/fpl-scan/SKILL.md`, a thin executor pointing at `framework.md`, modeled on
`~/.claude/skills/land-scan/SKILL.md` — so Mihir can trigger an ad hoc run conversationally, not
only via the scheduled launchd job.

Tasks:
- [ ] Write `~/.claude/skills/fpl-scan/SKILL.md`
- [ ] One invocation via the skill, confirm it produces/refreshes the current `reports/GW{n}.md`
- [ ] `git commit -m "Step 9: fpl-scan skill"` (run the full test suite first)

Verification: `~/.claude/skills/fpl-scan/SKILL.md` exists; a conversational invocation produces a
report at the pinned path (finding #19 — not the vague "working scheduled system" restatement of
the whole project's goal).

Exit criteria: FPL Advisor is reachable both on its daily schedule and on demand.
