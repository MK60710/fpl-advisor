from datetime import date

import pandas as pd
import pytest

from agents.watchlist import _age, build_watchlist

AS_OF = date(2026, 8, 29)


def _row(player_id, birth_date, minutes, xgi90, ownership):
    return {
        "player_id": player_id, "birth_date": birth_date, "minutes": minutes,
        "expected_goal_involvements_per_90": xgi90, "selected_by_percent": ownership,
    }


def test_age_calculation_before_birthday_this_year():
    # born 2005-12-01, as of 2026-08-29 — birthday hasn't happened yet this year
    assert _age("2005-12-01", AS_OF) == 20


def test_age_calculation_after_birthday_this_year():
    # born 2005-01-01, as of 2026-08-29 — birthday already passed this year
    assert _age("2005-01-01", AS_OF) == 21


def test_age_calculation_on_birthday():
    assert _age("2005-08-29", AS_OF) == 21


def test_watchlist_includes_qualifying_young_differential():
    df = pd.DataFrame(
        [_row(1, "2005-01-01", minutes=300, xgi90=0.6, ownership=3.0)]  # age 21, qualifies on everything
    )
    result = build_watchlist(df, as_of=AS_OF)
    assert result == [1]


def test_watchlist_excludes_too_old():
    df = pd.DataFrame([_row(1, "1990-01-01", minutes=300, xgi90=0.6, ownership=3.0)])  # age 36
    assert build_watchlist(df, as_of=AS_OF) == []


def test_watchlist_excludes_too_few_minutes():
    df = pd.DataFrame([_row(1, "2005-01-01", minutes=45, xgi90=0.6, ownership=3.0)])  # below MIN_MINUTES
    assert build_watchlist(df, as_of=AS_OF) == []


def test_watchlist_excludes_established_starter_by_minutes():
    df = pd.DataFrame([_row(1, "2005-01-01", minutes=950, xgi90=0.6, ownership=3.0)])  # above MAX_MINUTES
    assert build_watchlist(df, as_of=AS_OF) == []


def test_watchlist_excludes_low_output():
    df = pd.DataFrame([_row(1, "2005-01-01", minutes=300, xgi90=0.1, ownership=3.0)])  # below MIN_XGI_PER_90
    assert build_watchlist(df, as_of=AS_OF) == []


def test_watchlist_excludes_high_ownership():
    df = pd.DataFrame([_row(1, "2005-01-01", minutes=300, xgi90=0.6, ownership=25.0)])  # already widely owned
    assert build_watchlist(df, as_of=AS_OF) == []


def test_watchlist_excludes_missing_birth_date():
    df = pd.DataFrame([_row(1, None, minutes=300, xgi90=0.6, ownership=3.0)])
    assert build_watchlist(df, as_of=AS_OF) == []


def test_watchlist_excludes_nan_birth_date():
    # Regression test: a real live bug — pandas represents a missing birth_date as float NaN
    # (even in an otherwise-string column) rather than None, and `not NaN` is False in Python,
    # so a plain truthiness check let it through and crashed in _age(). Mixing a real qualifying
    # row in the same DataFrame reproduces the exact dtype conditions that caused it live.
    import math

    df = pd.DataFrame(
        [
            _row(1, float("nan"), minutes=300, xgi90=0.6, ownership=3.0),
            _row(2, "2005-01-01", minutes=300, xgi90=0.6, ownership=3.0),
        ]
    )
    assert math.isnan(df.loc[0, "birth_date"])  # confirms pandas actually stored it as NaN, not None
    assert build_watchlist(df, as_of=AS_OF) == [2]


def test_watchlist_sorted_descending_and_capped_at_5():
    rows = [
        _row(i, "2005-01-01", minutes=300, xgi90=0.4 + i * 0.1, ownership=3.0)
        for i in range(1, 8)  # 7 qualifying candidates, xgi90 from 0.5 up to 1.1
    ]
    df = pd.DataFrame(rows)
    result = build_watchlist(df, as_of=AS_OF)
    assert len(result) == 5
    assert result == [7, 6, 5, 4, 3]  # highest xGI/90 first
