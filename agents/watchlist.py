"""Breakout Watchlist (framework.md v1.1) — informational only, never influences squad selection
or projected_points. Pure current-season live data, no historical dependency."""
from datetime import date

MAX_AGE = 23
MIN_MINUTES = 90
MAX_MINUTES = 900
MIN_XGI_PER_90 = 0.4
MAX_OWNERSHIP_PERCENT = 10.0
WATCHLIST_SIZE = 5


def _age(birth_date_str, as_of):
    year, month, day = (int(x) for x in birth_date_str.split("-"))
    years = as_of.year - year
    if (as_of.month, as_of.day) < (month, day):
        years -= 1
    return years


def build_watchlist(player_table, as_of=None):
    """Returns a list of player_id, top WATCHLIST_SIZE by expected_goal_involvements_per_90
    descending, filtered to young (< MAX_AGE), emerging (MIN_MINUTES <= minutes < MAX_MINUTES),
    productive (xGI/90 >= MIN_XGI_PER_90), and low-owned (selected_by_percent < MAX_OWNERSHIP_PERCENT)."""
    as_of = as_of or date.today()

    candidates = []
    for _, row in player_table.iterrows():
        birth_date = row.get("birth_date")
        # Live data has real gaps here (some players' birth_date is missing/null from the API) —
        # pandas represents that as float NaN even in an otherwise-string column, and `not NaN`
        # is False in Python, so a plain truthiness check silently lets NaN through. Check the
        # type explicitly instead.
        if not isinstance(birth_date, str):
            continue
        age = _age(birth_date, as_of)
        minutes = row["minutes"]
        xgi90 = row["expected_goal_involvements_per_90"]
        ownership = row["selected_by_percent"]

        if (
            age < MAX_AGE
            and MIN_MINUTES <= minutes < MAX_MINUTES
            and xgi90 >= MIN_XGI_PER_90
            and ownership < MAX_OWNERSHIP_PERCENT
        ):
            candidates.append((row["player_id"], xgi90))

    candidates.sort(key=lambda c: c[1], reverse=True)
    return [player_id for player_id, _ in candidates[:WATCHLIST_SIZE]]


if __name__ == "__main__":
    from agents.data_agent import build_player_table, fetch_bootstrap, fetch_fixtures

    bs = fetch_bootstrap()
    fx = fetch_fixtures()
    table = build_player_table(bs, fx)
    watchlist = build_watchlist(table)
    name_map = dict(zip(table["player_id"], table["web_name"]))
    print(f"{len(watchlist)} players on the watchlist:")
    for pid in watchlist:
        row = table[table["player_id"] == pid].iloc[0]
        print(f"  {row['web_name']} ({row['team_short']}) — age {_age(row['birth_date'], date.today())}, "
              f"{row['expected_goal_involvements_per_90']:.2f} xGI/90, {row['selected_by_percent']:.1f}% owned")
