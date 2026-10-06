import csv
from unittest.mock import patch

import pytest

from agents.run_weekly import DEADLINE_GUARD_DAYS, detect_missed_gameweeks, run_weekly, select_target_gw

NOW = 1_800_000_000  # arbitrary fixed epoch for reproducible tests
DAY = 86400


def _bootstrap(events):
    return {"events": events}


def _event(gw, deadline_epoch):
    return {"id": gw, "deadline_time_epoch": deadline_epoch}


# --- select_target_gw: frozen-clock cases per finding #8 (6h, 3d, 10d, none) ---


def test_select_target_gw_deadline_in_6_hours():
    bootstrap = _bootstrap([_event(5, NOW + 6 * 3600), _event(6, NOW + 8 * DAY)])
    gw, deadline = select_target_gw(bootstrap, now_epoch=NOW)
    assert gw == 5
    assert deadline == NOW + 6 * 3600


def test_select_target_gw_deadline_in_3_days():
    bootstrap = _bootstrap([_event(5, NOW + 3 * DAY), _event(6, NOW + 10 * DAY)])
    gw, deadline = select_target_gw(bootstrap, now_epoch=NOW)
    assert gw == 5


def test_select_target_gw_deadline_in_10_days():
    bootstrap = _bootstrap([_event(5, NOW + 10 * DAY)])
    gw, deadline = select_target_gw(bootstrap, now_epoch=NOW)
    assert gw == 5
    assert deadline == NOW + 10 * DAY


def test_select_target_gw_none_when_post_season():
    bootstrap = _bootstrap([_event(38, NOW - DAY)])  # only past deadlines remain
    gw, deadline = select_target_gw(bootstrap, now_epoch=NOW)
    assert gw is None
    assert deadline is None


def test_select_target_gw_ignores_already_passed_deadline_even_if_unfinished():
    # this is the exact bug the old 'next unfinished gameweek' selector had: a just-passed
    # deadline must NOT be selected again, regardless of any finished/is_current/is_next flag
    bootstrap = _bootstrap([_event(4, NOW - 3600), _event(5, NOW + 2 * DAY)])
    gw, _ = select_target_gw(bootstrap, now_epoch=NOW)
    assert gw == 5


# --- run_weekly guard behavior ---


def test_run_weekly_no_op_when_outside_guard_window(tmp_path):
    bootstrap = _bootstrap([_event(5, NOW + (DEADLINE_GUARD_DAYS + 1) * DAY)])
    run_log = tmp_path / "run-log.csv"

    with patch("agents.run_weekly.fetch_bootstrap", return_value=bootstrap), \
         patch("agents.run_weekly.build_recommendation") as mock_build:
        result = run_weekly(now_epoch=NOW, run_log_path=str(run_log), reports_dir=str(tmp_path / "reports"))

    assert result is None
    mock_build.assert_not_called()
    rows = list(csv.DictReader(open(run_log)))
    assert rows[-1]["status"] == "no_op"
    assert rows[-1]["target_gw"] == "5"


def test_run_weekly_builds_report_when_inside_guard_window(tmp_path):
    bootstrap = _bootstrap([_event(5, NOW + 1 * DAY)])
    run_log = tmp_path / "run-log.csv"
    reports_dir = tmp_path / "reports"
    fake_rec = {
        "target_gw": 5,
        "data_mode": "full",
        "source_status": {
            "fantasyfootballfix": {"status": "ok"},
            "fantasyfootballscout": {"status": "ok"},
        },
    }

    with patch("agents.run_weekly.fetch_bootstrap", return_value=bootstrap), \
         patch("agents.run_weekly.build_recommendation", return_value=(fake_rec, "fake_table")) as mock_build, \
         patch("agents.run_weekly.write_report") as mock_write:
        result = run_weekly(now_epoch=NOW, run_log_path=str(run_log), reports_dir=str(reports_dir))

    mock_build.assert_called_once_with(5)
    mock_write.assert_called_once()
    assert result == fake_rec
    rows = list(csv.DictReader(open(run_log)))
    assert rows[-1]["status"] == "ok"
    assert rows[-1]["target_gw"] == "5"
    assert rows[-1]["data_mode"] == "full"


def test_run_weekly_skips_gw_before_first_modeled(tmp_path):
    bootstrap = _bootstrap([_event(2, NOW + 1 * DAY)])
    run_log = tmp_path / "run-log.csv"

    with patch("agents.run_weekly.fetch_bootstrap", return_value=bootstrap), \
         patch("agents.run_weekly.build_recommendation") as mock_build:
        result = run_weekly(now_epoch=NOW, run_log_path=str(run_log), reports_dir=str(tmp_path / "reports"))

    assert result is None
    mock_build.assert_not_called()
    rows = list(csv.DictReader(open(run_log)))
    assert rows[-1]["status"] == "no_op"
    assert rows[-1]["sources_failed"] == "too_early"


def test_run_weekly_post_season_no_op(tmp_path):
    bootstrap = _bootstrap([_event(38, NOW - DAY)])
    run_log = tmp_path / "run-log.csv"

    with patch("agents.run_weekly.fetch_bootstrap", return_value=bootstrap):
        result = run_weekly(now_epoch=NOW, run_log_path=str(run_log), reports_dir=str(tmp_path / "reports"))

    assert result is None
    rows = list(csv.DictReader(open(run_log)))
    assert rows[-1]["status"] == "no_op"
    assert rows[-1]["target_gw"] == ""


# --- miss detection (finding #17) ---


def test_detect_missed_gameweeks_flags_a_real_gap(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "GW3.md").write_text("report")
    # GW4's deadline passed but no report exists for it — a real missed gameweek
    bootstrap = _bootstrap([_event(3, NOW - 10 * DAY), _event(4, NOW - 3 * DAY), _event(5, NOW + 2 * DAY)])

    missed = detect_missed_gameweeks(bootstrap, now_epoch=NOW, reports_dir=str(reports_dir))
    assert missed == [4]


def test_detect_missed_gameweeks_excludes_gw1_and_gw2():
    # GW1-2 deadlines passed but are never modeled by design — not a miss
    bootstrap = _bootstrap([_event(1, NOW - 20 * DAY), _event(2, NOW - 13 * DAY)])
    missed = detect_missed_gameweeks(bootstrap, now_epoch=NOW, reports_dir="/nonexistent")
    assert missed == []


def test_run_weekly_logs_missed_gw_row(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    run_log = tmp_path / "run-log.csv"
    bootstrap = _bootstrap([_event(4, NOW - 3 * DAY), _event(5, NOW + 10 * DAY)])  # GW4 missed, GW5 outside guard

    with patch("agents.run_weekly.fetch_bootstrap", return_value=bootstrap):
        run_weekly(now_epoch=NOW, run_log_path=str(run_log), reports_dir=str(reports_dir))

    rows = list(csv.DictReader(open(run_log)))
    statuses = [r["status"] for r in rows]
    assert "MISSED_GW" in statuses
    missed_row = [r for r in rows if r["status"] == "MISSED_GW"][0]
    assert missed_row["target_gw"] == "4"


def test_run_weekly_propagates_error_and_logs_it(tmp_path):
    bootstrap = _bootstrap([_event(5, NOW + 1 * DAY)])
    run_log = tmp_path / "run-log.csv"

    with patch("agents.run_weekly.fetch_bootstrap", return_value=bootstrap), \
         patch("agents.run_weekly.build_recommendation", side_effect=RuntimeError("simulated failure")):
        with pytest.raises(RuntimeError):
            run_weekly(now_epoch=NOW, run_log_path=str(run_log), reports_dir=str(tmp_path / "reports"))

    rows = list(csv.DictReader(open(run_log)))
    assert rows[-1]["status"] == "error"
    assert "simulated failure" in rows[-1]["sources_failed"]


def test_run_weekly_logs_error_on_bootstrap_fetch_failure(tmp_path):
    # Regression test: a real scheduled launchd run hit a transient DNS failure on the very
    # first network call (fetch_bootstrap, before target_gw selection even happens) and produced
    # NO heartbeat row at all under the old code — this failure path wasn't wrapped like the
    # later build_recommendation/write_report one was.
    run_log = tmp_path / "run-log.csv"

    with patch("agents.run_weekly.fetch_bootstrap", side_effect=ConnectionError("simulated DNS failure")):
        with pytest.raises(ConnectionError):
            run_weekly(now_epoch=NOW, run_log_path=str(run_log), reports_dir=str(tmp_path / "reports"))

    rows = list(csv.DictReader(open(run_log)))
    assert len(rows) == 1
    assert rows[-1]["status"] == "error"
    assert "simulated DNS failure" in rows[-1]["sources_failed"]


def test_projections_failure_never_breaks_the_run(tmp_path):
    bootstrap = _bootstrap([_event(5, NOW + 1 * DAY)])
    run_log = tmp_path / "run-log.csv"
    fake_rec = {"target_gw": 5, "data_mode": "full", "source_status": {"fantasyfootballscout": {"status": "ok"}}}

    with patch("agents.run_weekly.fetch_bootstrap", return_value=bootstrap), \
         patch("agents.run_weekly.build_recommendation", return_value=(fake_rec, "fake_table")), \
         patch("agents.run_weekly.write_report") as mock_write, \
         patch("agents.run_weekly.write_projections", side_effect=RuntimeError("disk full")) as mock_proj:
        result = run_weekly(now_epoch=NOW, run_log_path=str(run_log), reports_dir=str(tmp_path / "reports"))

    mock_write.assert_called_once()
    mock_proj.assert_called_once()
    assert result == fake_rec
    assert list(csv.DictReader(open(run_log)))[-1]["status"] == "ok"
