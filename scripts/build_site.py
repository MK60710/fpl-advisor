"""Builds docs/latest.json for the GitHub Pages site from the newest reports/GW*.md.

Run after a new report lands:  python3 scripts/build_site.py
"""
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYER = re.compile(r"^- (?:(GK|\d): )?(.+?) \(([A-Z]{3}), (GKP|DEF|MID|FWD)\) — £([\d.]+)m, ([\d.]+) pp(?: \((C|VC)\))?")
WATCH = re.compile(r"^- (.+?) \(([A-Z]{3}), (GKP|DEF|MID|FWD)\) — age (\d+), £([\d.]+)m, ([\d.]+) xGI/90, ([\d.]+)% owned")


def latest_report():
    reports = glob.glob(os.path.join(ROOT, "reports", "GW*.md"))
    if not reports:
        raise SystemExit("no reports/GW*.md found")
    return max(reports, key=lambda p: int(re.search(r"GW(\d+)\.md$", p).group(1)))


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


def build():
    path = latest_report()
    text = open(path, encoding="utf-8").read()
    gw = int(re.search(r"Gameweek (\d+)", text).group(1))
    deadline = re.search(r"^Deadline: (.+)$", text, re.M).group(1).strip()
    cost = re.search(r"^Squad cost: £([\d.]+)m / £([\d.]+)m — bank: £([\d.]+)m", text, re.M)
    mode = re.search(r"^\*\*Data mode: ([a-z_]+)\.\*\* (.+)$", text, re.M)
    risk = section(text, "Risk Flags")
    watch = []
    for line in section(text, "Breakout Watchlist").splitlines():
        m = WATCH.match(line.strip())
        if m:
            name, club, pos, age, price, xgi, owned = m.groups()
            watch.append({"name": name, "club": club, "pos": pos, "age": int(age), "price": float(price), "xgi90": float(xgi), "owned": float(owned)})
    data = {
        "gameweek": gw,
        "deadline": deadline,
        "cost": float(cost.group(1)), "budget": float(cost.group(2)), "bank": float(cost.group(3)),
        "data_mode": mode.group(1) if mode else None,
        "data_note": mode.group(2).strip() if mode else None,
        "xi": players(section(text, "Starting XI")),
        "bench": players(section(text, "Bench")),
        "risk_flags": risk,
        "watchlist": watch,
        "report": os.path.relpath(path, ROOT),
    }
    if len(data["xi"]) != 11 or len(data["bench"]) != 4:
        raise SystemExit(f"parsed {len(data['xi'])} starters and {len(data['bench'])} bench players, expected 11 and 4")
    out = os.path.join(ROOT, "docs", "latest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"wrote {os.path.relpath(out, ROOT)} from {data['report']} (GW{gw})")


if __name__ == "__main__":
    build()
