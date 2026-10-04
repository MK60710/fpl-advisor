"""Weekly orchestrator (framework.md). Selects the target gameweek by deadline epoch — NOT by
finished/is_current/is_next, which can point at an already-locked gameweek in the window between
a deadline and Monday/Tuesday processing. Runs daily; a 'within N days of deadline' guard makes
most days a cheap no-op rather than needing a fragile fixed weekday schedule."""
import csv
import glob
import os
import re
import time
from datetime import datetime, timezone

from agents.data_agent import fetch_bootstrap
from agents.recommend import build_recommendation
from agents.report import REPORTS_DIR, write_report

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
RUN_LOG_PATH = os.path.join(LOGS_DIR, "run-log.csv")
DEADLINE_GUARD_DAYS = 3
FIRST_MODELED_GW = 3  # GW1-2 have no reliable projection — framework.md
RUN_LOG_COLUMNS = ["timestamp", "target_gw", "status", "data_mode", "sources_ok", "sources_failed"]


def select_target_gw(bootstrap, now_epoch=None):
    """Returns (target_gw, deadline_epoch) for the event with the smallest deadline_time_epoch
    strictly greater than now. (None, None) if none exists (post-season)."""
    now_epoch = now_epoch if now_epoch is not None else time.time()
    upcoming = [e for e in bootstrap["events"] if e["deadline_time_epoch"] > now_epoch]
    if not upcoming:
        return None, None
    nearest = min(upcoming, key=lambda e: e["deadline_time_epoch"])
    return nearest["id"], nearest["deadline_time_epoch"]


def _existing_report_gameweeks(reports_dir):
    gws = set()
    for path in glob.glob(os.path.join(reports_dir, "GW*.md")):
        m = re.match(r"GW(\d+)\.md$", os.path.basename(path))
        if m:
            gws.add(int(m.group(1)))
    return gws


def detect_missed_gameweeks(bootstrap, now_epoch=None, reports_dir=REPORTS_DIR):
    """Every event (GW >= FIRST_MODELED_GW) whose deadline has passed with no report ever
    produced for it. GW1-2 are excluded — never modeled by design, not a miss."""
    now_epoch = now_epoch if now_epoch is not None else time.time()
    existing = _existing_report_gameweeks(reports_dir)
    passed = [
        e["id"] for e in bootstrap["events"]
        if e["deadline_time_epoch"] <= now_epoch and e["id"] >= FIRST_MODELED_GW
    ]
    return sorted(gw for gw in passed if gw not in existing)


def _log_heartbeat(row, run_log_path=RUN_LOG_PATH):
    os.makedirs(os.path.dirname(run_log_path), exist_ok=True)
    file_exists = os.path.exists(run_log_path)
    with open(run_log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RUN_LOG_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _heartbeat_row(target_gw, status, data_mode="", sources_ok="", sources_failed=""):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_gw": target_gw if target_gw is not None else "",
        "status": status,
        "data_mode": data_mode,
        "sources_ok": sources_ok,
        "sources_failed": sources_failed,
    }


def run_weekly(now_epoch=None, run_log_path=RUN_LOG_PATH, reports_dir=REPORTS_DIR):
    """Main entry point, called daily by launchd. Always writes at least one heartbeat row —
    including on a failure at the very first network call, which a real launchd run hit
    (transient DNS failure, 2026-08-29) with no heartbeat logged before this fix."""
    now_epoch = now_epoch if now_epoch is not None else time.time()

    try:
        bootstrap = fetch_bootstrap()
    except Exception as exc:
        _log_heartbeat(_heartbeat_row(None, "error", sources_failed=str(exc)[:200]), run_log_path=run_log_path)
        raise

    for gw in detect_missed_gameweeks(bootstrap, now_epoch=now_epoch, reports_dir=reports_dir):
        _log_heartbeat(_heartbeat_row(gw, "MISSED_GW"), run_log_path=run_log_path)

    target_gw, deadline_epoch = select_target_gw(bootstrap, now_epoch=now_epoch)
    if target_gw is None:
        _log_heartbeat(_heartbeat_row(None, "no_op", sources_failed="post_season"), run_log_path=run_log_path)
        return None

    days_until_deadline = (deadline_epoch - now_epoch) / 86400
    if days_until_deadline > DEADLINE_GUARD_DAYS:
        _log_heartbeat(_heartbeat_row(target_gw, "no_op"), run_log_path=run_log_path)
        return None

    if target_gw < FIRST_MODELED_GW:
        _log_heartbeat(_heartbeat_row(target_gw, "no_op", sources_failed="too_early"), run_log_path=run_log_path)
        return None

    try:
        recommendation, player_table = build_recommendation(target_gw)
        write_report(recommendation, player_table, reports_dir=reports_dir)
    except Exception as exc:
        _log_heartbeat(_heartbeat_row(target_gw, "error", sources_failed=str(exc)[:200]), run_log_path=run_log_path)
        raise

    sources_ok = [s for s, st in recommendation["source_status"].items() if st["status"] == "ok"]
    sources_failed = [s for s, st in recommendation["source_status"].items() if st["status"] != "ok"]
    _log_heartbeat(
        _heartbeat_row(
            target_gw, "ok", data_mode=recommendation["data_mode"],
            sources_ok=",".join(sources_ok), sources_failed=",".join(sources_failed),
        ),
        run_log_path=run_log_path,
    )
    return recommendation


if __name__ == "__main__":
    run_weekly()
