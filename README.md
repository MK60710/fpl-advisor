# FPL Advisor

[![tests](https://github.com/MK60710/fpl-advisor/actions/workflows/tests.yml/badge.svg)](https://github.com/MK60710/fpl-advisor/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
[![license: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Hi! I'm Mihir and this is FPL Advisor.

It picks a Fantasy Premier League squad for me every gameweek. It looks at the official FPL data plus what the expert sites are saying, works out how many points each player is likely to score and then picks the best 15 it can under the real rules (the £100m budget, positions and max 3 players per club). It also picks the starting XI, captain and vice-captain.

It only recommends. It never logs into an FPL account or makes changes for you, so you still enter the picks yourself on the FPL site.

## What you get

A markdown report for each gameweek. Here's the top of a real one ([full report](reports/GW6.md)):

```
# FPL Advisor — Gameweek 6 Recommendation

Deadline: 2026-10-10 10:00 UTC
Squad cost: £100.0m / £100.0m — bank: £0.0m

## Starting XI
- Scott (BOU, MID) — £6.1m, 7.04 pp
- Schade (BRE, MID) — £6.2m, 8.88 pp
- Groß (BHA, MID) — £5.8m, 11.95 pp (C)
- Tarkowski (EVE, DEF) — £6.1m, 9.11 pp
...
```

`pp` is projected points for that gameweek, (C) is the captain. The full report also covers the bench, the vice-captain, chip reminders and which data sources were up that run.

## How it works

1. **Data.** It pulls players, teams and fixtures from the official FPL API, no login or API key needed.
2. **Research.** It reads expert signal from sites like Fantasy Football Scout and checks transfer news when a transfer window is open. If a source is down, the report says so and it falls back to the official data.
3. **Projections.** Each player gets a projected points number. Early in the season it blends in last season's numbers so a player with two good games doesn't look like a superstar, and it accounts for fixture difficulty, injuries and set-piece duties.
4. **The pick.** An optimizer (an integer linear program, using [PuLP](https://coin-or.github.io/pulp/)) picks the squad that scores the most projected points within the rules. When two squads tie, it breaks the tie with an exact step-by-step solve instead of tiny weights.
5. **The report.** It writes everything up with the reasoning, into `reports/`.

## Running it

You need Python 3.11 or newer.

```
git clone https://github.com/MK60710/fpl-advisor.git
cd fpl-advisor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 -m agents.run_weekly
```

It's built to run once a day (`run-daily.sh` is what I schedule). Most days it does nothing, and it only builds a new report when the next deadline is a few days out.

## Tests

```
pytest
```

A few tests call the live FPL site and last season's data. They skip themselves if you're offline, and CI skips them on purpose (`SKIP_LIVE_TESTS=1`) so a slow FPL API can't break the build.

## Project layout

```
agents/
  data_agent.py        official FPL API: players, teams, fixtures, rule checks
  historical_agent.py  last season's numbers for the early-season blend
  research_agent.py    expert-site signal
  transfer_agent.py    transfer rumors while a window is open (informational only)
  watchlist.py         breakout watchlist (informational only)
  projection.py        projected points per player
  optimizer.py         one ILP solve that picks the 15, the XI and the captain together
  recommend.py         ties the steps together
  report.py            writes the markdown report
  run_weekly.py        daily entry point with the deadline check
docs/
  SPEC.md              the original spec
  framework.md         every rule and formula, plus the changelog
  construction-plan.md how it was built, step by step
reports/               one report per gameweek
tests/
```

## A heads-up

Football is chaotic and projections are just educated guesses. Use it as a second opinion, not gospel.

## License

MIT. Do whatever you want with it.
