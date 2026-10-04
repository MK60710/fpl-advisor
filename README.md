# FPL Advisor

Hi! I'm Mihir and this is FPL Advisor.

It picks a Fantasy Premier League squad for me every gameweek. It looks at the official FPL data plus what the expert sites are saying, works out how many points each player is likely to score and then picks the best 15 it can under the real rules (the £100m budget, positions and max 3 players per club). It also picks the starting XI, captain and vice-captain.

It only recommends. It never logs into an FPL account or makes changes for you, so you still enter the picks yourself on the FPL site.

## How it works

1. **Data.** It pulls players, teams and fixtures from the official FPL API, no login needed.
2. **Research.** It reads expert signal from sites like Fantasy Football Scout and checks transfer news when a transfer window is open. If a source is down, the report says so and it falls back to the official data.
3. **Projections.** Each player gets a projected points number. Early in the season it blends in last season's numbers so a player with two good games doesn't look like a superstar, and it accounts for fixture difficulty, injuries and set-piece duties.
4. **The pick.** An optimizer (an integer linear program, using PuLP) picks the squad that scores the most projected points within the rules. When two squads tie, it breaks the tie with an exact step-by-step solve instead of tiny weights.
5. **The report.** It writes a markdown report for the gameweek with the squad, XI, captain and the reasoning. There are a few in `reports/`.

`framework.md` has the full details and the changelog, and `SPEC.md` has the original spec.

## Running it

You need Python 3.

```
git clone https://github.com/MK60710/fpl-advisor.git
cd fpl-advisor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 -m agents.run_weekly
```

It's built to run once a day. Most days it does nothing, and it only builds a new report when the next deadline is a few days out. The report lands in `reports/`.

Run the tests with:

```
pytest
```

## A heads-up

Football is chaotic and projections are just educated guesses. Use it as a second opinion, not gospel.
