import socket

import pandas as pd
import pytest

from agents.data_agent import (
    EXPECTED_POSITION_RULES,
    EXPECTED_SQUAD_RULES,
    FRAMEWORK_COLUMNS,
    FPLRuleDriftError,
    assert_rules_unchanged,
    build_player_table,
    check_status_codes,
    detect_dgw_bgw,
    fetch_bootstrap,
    fetch_fixtures,
)

# Numeric columns get an exact dtype check. String columns are checked via
# pd.api.types.is_string_dtype rather than a literal dtype name — pandas 3.x's default string
# storage ("str" extension dtype) differs from pandas <3's "object", and pinning the literal name
# would make this test environment-version-specific for no real benefit.
FRAMEWORK_NUMERIC_DTYPES = {
    "player_id": "int64",
    "team_id": "int64",
    "cost_tenths": "int64",
    "price_m": "float64",
    "form": "float64",
    "total_points": "int64",
    "ep_next": "float64",
    "selected_by_percent": "float64",
    "fixture_difficulty_next5": "float64",
    "minutes": "int64",
    "starts": "int64",
    "expected_goal_involvements_per_90": "float64",
    "event_points": "int64",
}
FRAMEWORK_STRING_COLUMNS = [
    "web_name", "first_name", "second_name", "full_name", "team_short", "position", "status",
    "news", "birth_date",
]


def _has_network():
    try:
        socket.create_connection(("fantasy.premierleague.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


def _make_bootstrap(elements=None, element_types_overrides=None, statuses_ok=True):
    element_types = [
        {"id": 1, "singular_name_short": "GKP", "squad_select": 2, "squad_min_play": 1, "squad_max_play": 1},
        {"id": 2, "singular_name_short": "DEF", "squad_select": 5, "squad_min_play": 3, "squad_max_play": 5},
        {"id": 3, "singular_name_short": "MID", "squad_select": 5, "squad_min_play": 2, "squad_max_play": 5},
        {"id": 4, "singular_name_short": "FWD", "squad_select": 3, "squad_min_play": 1, "squad_max_play": 3},
    ]
    if element_types_overrides:
        for et in element_types:
            if et["singular_name_short"] in element_types_overrides:
                et.update(element_types_overrides[et["singular_name_short"]])

    if elements is None:
        elements = [
            {
                "id": 1, "web_name": "Salah", "first_name": "Mohamed", "second_name": "Salah",
                "team": 1, "element_type": 3, "now_cost": 130, "form": "8.0", "total_points": 20,
                "ep_next": "7.5", "selected_by_percent": "45.3",
                "chance_of_playing_next_round": 100, "status": "a", "news": "",
                "minutes": 180, "starts": 2, "birth_date": "1992-06-15",
                "expected_goal_involvements_per_90": 0.9, "event_points": 10,
            },
            {
                "id": 2, "web_name": "Haaland", "first_name": "Erling", "second_name": "Haaland",
                "team": 2, "element_type": 4, "now_cost": 155, "form": "9.0", "total_points": 25,
                "ep_next": "8.0", "selected_by_percent": "60.1",
                "chance_of_playing_next_round": None, "status": "a", "news": "",
                "minutes": 180, "starts": 2, "birth_date": "2000-07-21",
                "expected_goal_involvements_per_90": 1.1, "event_points": 12,
            },
            {
                "id": 3, "web_name": "InjuredGuy", "first_name": "Some", "second_name": "InjuredGuy",
                "team": 1, "element_type": 2, "now_cost": 45, "form": "0.0", "total_points": 2,
                "ep_next": "0.0", "selected_by_percent": "1.0",
                "chance_of_playing_next_round": 0, "status": "i" if statuses_ok else "n", "news": "Injured",
                "minutes": 0, "starts": 0, "birth_date": "1998-01-01",
                "expected_goal_involvements_per_90": 0.0, "event_points": 0,
            },
        ]

    return {
        "game_settings": dict(EXPECTED_SQUAD_RULES),
        "element_types": element_types,
        "teams": [{"id": 1, "short_name": "LIV"}, {"id": 2, "short_name": "MCI"}],
        "elements": elements,
    }


def _make_fixtures(pairs):
    """pairs: list of (event, team_h, team_a, team_h_difficulty, team_a_difficulty, finished)."""
    fixtures = []
    for i, (event, team_h, team_a, fdr_h, fdr_a, finished) in enumerate(pairs):
        fixtures.append(
            {
                "event": event, "team_h": team_h, "team_a": team_a,
                "team_h_difficulty": fdr_h, "team_a_difficulty": fdr_a, "finished": finished,
            }
        )
    return fixtures


def test_assert_rules_unchanged_passes_on_matching_rules():
    bootstrap = _make_bootstrap()
    assert_rules_unchanged(bootstrap)  # should not raise


def test_assert_rules_unchanged_raises_on_budget_drift():
    bootstrap = _make_bootstrap()
    bootstrap["game_settings"]["squad_total_spend"] = 999
    with pytest.raises(FPLRuleDriftError):
        assert_rules_unchanged(bootstrap)


def test_assert_rules_unchanged_raises_on_position_drift():
    bootstrap = _make_bootstrap(element_types_overrides={"DEF": {"squad_select": 4}})
    with pytest.raises(FPLRuleDriftError):
        assert_rules_unchanged(bootstrap)


def test_check_status_codes_raises_on_unexpected_code():
    bootstrap = _make_bootstrap(statuses_ok=False)
    with pytest.raises(FPLRuleDriftError):
        check_status_codes(bootstrap)


def test_build_player_table_schema_and_dtypes():
    bootstrap = _make_bootstrap()
    fixtures = _make_fixtures([(3, 1, 2, 2, 4, False)])
    df = build_player_table(bootstrap, fixtures)

    assert set(df.columns) == set(FRAMEWORK_COLUMNS)
    assert list(df.columns) == FRAMEWORK_COLUMNS
    for col, expected_dtype in FRAMEWORK_NUMERIC_DTYPES.items():
        assert str(df[col].dtype) == expected_dtype, f"{col}: got {df[col].dtype}, expected {expected_dtype}"
    for col in FRAMEWORK_STRING_COLUMNS:
        assert pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object, f"{col}: got {df[col].dtype}, expected string-like"
    # chance_of_playing_next_round: int | None per framework.md — not asserted to a single pandas
    # dtype since pandas represents an int column with nulls as float64 or a nullable Int64
    # depending on version; check the actual values instead.
    non_null = df["chance_of_playing_next_round"].dropna()
    assert non_null.apply(lambda v: float(v).is_integer()).all()


def test_build_player_table_cost_conversion():
    bootstrap = _make_bootstrap()
    fixtures = _make_fixtures([(3, 1, 2, 2, 4, False)])
    df = build_player_table(bootstrap, fixtures)

    haaland = df[df["web_name"] == "Haaland"].iloc[0]
    assert haaland["cost_tenths"] == 155
    assert haaland["price_m"] == 15.5


def test_build_player_table_raises_on_unexpected_element_type():
    bootstrap = _make_bootstrap()
    bootstrap["elements"][0]["element_type"] = 99
    fixtures = _make_fixtures([(3, 1, 2, 2, 4, False)])
    with pytest.raises(FPLRuleDriftError):
        build_player_table(bootstrap, fixtures)


def test_fixture_difficulty_next5_averages_correctly():
    bootstrap = _make_bootstrap()
    # team 1 (LIV) has 2 upcoming fixtures: home FDR 2, away FDR 5 -> avg 3.5
    fixtures = _make_fixtures(
        [
            (3, 1, 2, 2, 4, False),  # LIV home, FDR 2
            (4, 2, 1, 3, 5, False),  # LIV away, FDR 5
            (2, 1, 2, 1, 1, True),  # finished, must be excluded
        ]
    )
    df = build_player_table(bootstrap, fixtures)
    liv_players = df[df["team_id"] == 1]
    assert (liv_players["fixture_difficulty_next5"] == 3.5).all()


def test_dgw_bgw_guard_detects_blank_and_double():
    bootstrap = _make_bootstrap()
    # GW5: team 1 plays twice (double, vs an unlisted opponent id 3), team 2 plays zero times (blank)
    fixtures = [
        {"event": 5, "team_h": 1, "team_a": 3, "team_h_difficulty": 2, "team_a_difficulty": 2, "finished": False},
        {"event": 5, "team_h": 3, "team_a": 1, "team_h_difficulty": 2, "team_a_difficulty": 2, "finished": False},
    ]
    warnings = detect_dgw_bgw(bootstrap, fixtures)
    assert any("team_id=1" in w and "double" in w for w in warnings)
    assert any("team_id=2" in w and "blank" in w for w in warnings)


@pytest.mark.skipif(not _has_network(), reason="no network access")
def test_live_pull_smoke():
    bootstrap = fetch_bootstrap(force_refresh=True)
    fixtures = fetch_fixtures(force_refresh=True)
    df = build_player_table(bootstrap, fixtures)

    assert len(df) > 500
    assert set(df.columns) == set(FRAMEWORK_COLUMNS)
    # Sanity band, not an exact pin — prices move over the season. Live-verified 2026-08-28: max=155.
    assert 100 <= df["cost_tenths"].max() <= 250
    assert df["status"].isin({"a", "d", "i", "s", "u"}).all()
