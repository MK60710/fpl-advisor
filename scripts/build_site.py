"""Builds the data files for the GitHub Pages site from reports/GW*.md.

Writes docs/data/gwN.json for every report, docs/data/index.json (one row per
gameweek, with the projected-vs-actual track record) and docs/latest.json (the
newest gameweek). For gameweeks that have finished, real points come from the
official FPL API. If the API is unreachable, points fetched on an earlier run
are kept, and the build still succeeds.

Run after a new report lands:  python3 scripts/build_site.py
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")
API = "https://fantasy.premierleague.com/api"
PLAYER = re.compile(r"^- (?:(GK|\d): )?(.+?) \(([A-Z]{3}), (GKP|DEF|MID|FWD)\) — £([\d.]+)m, ([\d.]+) pp(?: \((C|VC)\))?")
WATCH = re.compile(r"^- (.+?) \(([A-Z]{3}), (GKP|DEF|MID|FWD)\) — age (\d+), £([\d.]+)m, ([\d.]+) xGI/90, ([\d.]+)% owned")


def section(text, title):
    m = re.search(r"^## " + re.escape(title) + r"\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def players(block):
    out = []
    for line in block.splitlines():
        m = PLAYER.match(line.strip())
        if m:
            slot, name, club, pos, price, pp, tag = m.groups()
            out.append({"name": name, "club": club, "pos": pos, "price": float(price), "pp": float(pp), "tag": tag, "slot": slot})
    return out


def parse_report(path):
    text = open(path, encoding="utf-8").read()
    gw = int(re.search(r"Gameweek (\d+)", text).group(1))
    cost = re.search(r"^Squad cost: £([\d.]+)m / £([\d.]+)m — bank: £([\d.]+)m", text, re.M)
    mode = re.search(r"^\*\*Data mode: ([a-z_]+)\.\*\* (.+)$", text, re.M)
    watch = []
    for line in section(text, "Breakout Watchlist").splitlines():
        m = WATCH.match(line.strip())
        if m:
            name, club, pos, age, price, xgi, owned = m.groups()
            watch.append({"name": name, "club": club, "pos": pos, "age": int(age), "price": float(price), "xgi90": float(xgi), "owned": float(owned)})
    data = {
        "gameweek": gw,
        "deadline": re.search(r"^Deadline: (.+)$", text, re.M).group(1).strip(),
        "cost": float(cost.group(1)), "budget": float(cost.group(2)), "bank": float(cost.group(3)),
        "data_mode": mode.group(1) if mode else None,
        "xi": players(section(text, "Starting XI")),
        "bench": players(section(text, "Bench")),
        "risk_flags": section(text, "Risk Flags"),
        "watchlist": watch,
        "report": os.path.relpath(path, ROOT),
    }
    if len(data["xi"]) != 11 or len(data["bench"]) != 4:
        raise SystemExit(f"{data['report']}: parsed {len(data['xi'])} starters and {len(data['bench'])} bench players, expected 11 and 4")
    return data


def captain_multiplier(xi, actual_minutes):
    """FPL rule: the captain scores double; if the captain played no minutes, the vice-captain does instead."""
    cap = next((p for p in xi if p["tag"] == "C"), None)
    vc = next((p for p in xi if p["tag"] == "VC"), None)
    if cap and actual_minutes.get(cap["name"], 0) > 0:
        return cap["name"]
    if vc and actual_minutes.get(vc["name"], 0) > 0:
        return vc["name"]
    return cap["name"] if cap else None


def projected_total(xi):
    return round(sum(p["pp"] * (2 if p["tag"] == "C" else 1) for p in xi), 2)


def add_actuals(data, live, ids):
    """Fills in real points and minutes for every player, and the XI total."""
    stats = {e["id"]: e["stats"] for e in live["elements"]}
    for p in data["xi"] + data["bench"]:
        pid = ids.get((p["name"], p["club"]))
        if pid is None or pid not in stats:
            raise SystemExit(f"GW{data['gameweek']}: couldn't match {p['name']} ({p['club']}) to an FPL player")
        p["actual"] = stats[pid]["total_points"]
        p["minutes"] = stats[pid]["minutes"]
    doubled = captain_multiplier(data["xi"], {p["name"]: p["minutes"] for p in data["xi"]})
    data["actual_total"] = sum(p["actual"] * (2 if p["name"] == doubled else 1) for p in data["xi"])
    data["captain_scored_for"] = doubled


def fetch(url):
    r = requests.get(url, timeout=20, headers={"User-Agent": "fpl-advisor-site/1.0 (github.com/MK60710/fpl-advisor)"})
    r.raise_for_status()
    return r.json()


def build():
    reports = sorted(glob.glob(os.path.join(ROOT, "reports", "GW*.md")), key=lambda p: int(re.search(r"GW(\d+)\.md$", p).group(1)))
    if not reports:
        raise SystemExit("no reports/GW*.md found")
    os.makedirs(DATA_DIR, exist_ok=True)

    finished, ids, api_ok, bench = set(), {}, True, {}
    try:
        boot = fetch(f"{API}/bootstrap-static/")
        finished = {e["id"] for e in boot["events"] if e["finished"]}
        bench = {e["id"]: {"average": e["average_entry_score"], "highest": e["highest_score"]} for e in boot["events"] if e["finished"]}
        teams = {t["id"]: t["short_name"] for t in boot["teams"]}
        ids = {(p["web_name"], teams[p["team"]]): p["id"] for p in boot["elements"]}
    except requests.RequestException as e:
        api_ok = False
        print(f"FPL API unreachable ({e.__class__.__name__}), keeping points from earlier runs", file=sys.stderr)

    index = []
    for path in reports:
        data = parse_report(path)
        gw = data["gameweek"]
        out = os.path.join(DATA_DIR, f"gw{gw}.json")
        data["projected_total"] = projected_total(data["xi"])
        proj_path = os.path.join(ROOT, "reports", f"GW{gw}-projections.json")
        if os.path.exists(proj_path):  # written by the pipeline from GW6/7 on; older weeks don't have one
            proj = json.load(open(proj_path, encoding="utf-8"))
            top = {}
            for p in proj["players"]:
                if p.get("projected_points") is None:
                    continue
                top.setdefault(p["position"], [])
                if len(top[p["position"]]) < 10:
                    top[p["position"]].append({"name": p["web_name"], "club": p["team_short"], "price": p["price_m"],
                                               "pp": round(p["projected_points"], 2), "ep_next": p["ep_next"]})
            data["projections"] = top
        data["finished"] = gw in finished
        if api_ok and data["finished"]:
            add_actuals(data, fetch(f"{API}/event/{gw}/live/"), ids)
            data["average_manager"], data["highest"] = bench[gw]["average"], bench[gw]["highest"]
        elif os.path.exists(out):
            old = json.load(open(out, encoding="utf-8"))
            if "actual_total" in old:  # keep earlier real points when the API is down
                data.update({k: old[k] for k in ("actual_total", "captain_scored_for", "finished", "average_manager", "highest") if k in old})
                for new_p, old_p in zip(data["xi"] + data["bench"], old["xi"] + old["bench"]):
                    if new_p["name"] == old_p["name"] and "actual" in old_p:
                        new_p["actual"], new_p["minutes"] = old_p["actual"], old_p["minutes"]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        index.append({"gameweek": gw, "finished": data["finished"], "projected_total": data["projected_total"], "actual_total": data.get("actual_total"),
                      "average_manager": data.get("average_manager"), "highest": data.get("highest")})

    latest = json.load(open(os.path.join(DATA_DIR, f"gw{index[-1]['gameweek']}.json"), encoding="utf-8"))
    with open(os.path.join(ROOT, "docs", "latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "gameweeks": index}, f, indent=2)
    for row in index:
        actual = row["actual_total"] if row["actual_total"] is not None else "not played yet"
        avg = f", average manager {row['average_manager']}" if row["average_manager"] is not None else ""
        print(f"GW{row['gameweek']}: projected {row['projected_total']}, actual {actual}{avg}")


if __name__ == "__main__":
    build()
