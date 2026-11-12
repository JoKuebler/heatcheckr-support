#!/usr/bin/env python3
"""Fill the "Last night's NBA games" section of index.html from the public API.

Run daily by .github/workflows/update-games.yml (standard library only).
Rewrites only what sits between the GAMES:START / GAMES:END markers in
index.html, plus the home page's <lastmod> in sitemap.xml.

Spoiler-free by construction: only team names, the 0-10 excitement score and
the labels are written. Final scores, winners and labels of the app's
"Potential Spoilers" group never reach the page.

Fails safe: if the API is down or has no games, the files are left untouched
and the script exits 0, so the page keeps the last game night.
"""
import html
import json
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API = "https://app-production-2fb0.up.railway.app/api/games"
ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- GAMES:START -->", "<!-- GAMES:END -->"
LOOKBACK_DAYS = 7  # no games last night: show the most recent night within a week

# Same patterns and order as LABEL_PATTERNS in the app (02_frontend/src/constants.ts).
# First match wins. "spoilers" labels are dropped.
CATEGORIES = [
    ("spoilers", r"buzzer beater|double ot|triple ot|overtime|upset alert|madness moment|walk-off"),
    ("significance", r"instant classic|matchup|bout|playoff race|tank bowl|game 7|elimination game|pivotal game|divisional battle|primetime"),
    ("competitiveness", r"back & forth|nail biter ending|photo finish|tight finish|q4 comeback|halftime comeback|comeback|long climb|massive run|hot start|late r(un|ally)|defensive|roller coaster|one-score"),
    ("highlights", r"shootout|high octane|glass cleaner|assist symphony|triple double|scoring explosion|sniper|pickpocket|block party|clutch stop|clutch shot|free flowing|hot hand|bench mob|scoring duel|big-play|pick six|explosive|monster performance|big leg|fourth-down gamble"),
    ("lowlights", r"chaos|brick|free throw parade|easy win|blowout|nothing burger|tempers flared|wire-to-wire|punt fest|butterfingers"),
]
ORDER = ["significance", "competitiveness", "highlights", "lowlights"]


def category(label):
    for name, pattern in CATEGORIES:
        if re.search(pattern, label, re.I):
            return name
    return "highlights"


def fetch(day=None):
    url = API + (f"?date={day}" if day else "")
    req = urllib.request.Request(url, headers={"User-Agent": "heatcheckr-website"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def scored_games(payload):
    games = [g for g in (payload.get("games") or []) if isinstance(g.get("excitement_score"), (int, float))]
    return sorted(games, key=lambda g: g["excitement_score"], reverse=True)


def latest_game_night():
    """(games, date string, is_last_night) or None."""
    payload = fetch()
    games = scored_games(payload)
    if games:
        return games, payload.get("games_date"), True
    try:
        start = date.fromisoformat(payload.get("games_date") or "")
    except ValueError:
        return None
    for back in range(1, LOOKBACK_DAYS + 1):
        day = (start - timedelta(days=back)).isoformat()
        games = scored_games(fetch(day))
        if games:
            return games, day, False
    return None


def render(games, day, is_last_night):
    d = date.fromisoformat(day)
    nice_date = f"{d.strftime('%A, %B')} {d.day}, {d.year}"
    title = "Last night's NBA games, spoiler-free." if is_last_night else "The latest NBA games, spoiler-free."
    rows = []
    for g in games:
        score = float(g["excitement_score"])
        bucket = min(10, max(1, int(score)))
        away = html.escape((g.get("away_team") or {}).get("name") or "")
        home = html.escape((g.get("home_team") or {}).get("name") or "")
        if not away or not home:
            continue
        labels = [(category(l), l) for l in (g.get("labels") or []) if isinstance(l, str)]
        labels = [(c, l) for c, l in labels if c != "spoilers"]
        labels.sort(key=lambda cl: ORDER.index(cl[0]))
        pills = "".join(f'<span class="ln-pill ln-{c}">{html.escape(l)}</span>' for c, l in labels)
        rows.append(
            f'      <li class="ln-game">'
            f'<span class="ln-score ln-s{bucket}" aria-label="Excitement score {score:.1f} out of 10">{score:.1f}</span>'
            f'<div class="ln-body"><h3 class="ln-teams">{away} <span class="ln-at">@</span> {home}</h3>'
            f'<div class="ln-pills">{pills}</div></div></li>'
        )
    if not rows:
        return None
    return "\n".join([
        START,
        f'    <div data-games-date="{day}">',
        '      <p class="section-label">NBA &middot; ' + html.escape(nice_date) + "</p>",
        f'      <h2 class="section-title">{title}</h2>',
        '      <p class="section-desc">Every game rated 0&ndash;10 for excitement, best first. No scores, no winners.</p>',
        "    </div>",
        '    <ol class="ln-list">',
        *rows,
        "    </ol>",
        '    <p class="ln-more">In the app: the HeatFlow chart, highlights, box scores, the Daily Briefing, the weekly Heat Report, alerts for your favorite teams, plus WNBA, EuroLeague and NFL.</p>',
        "    " + END,
    ])


def main():
    try:
        found = latest_game_night()
    except Exception as e:  # network error, bad JSON, API down
        print(f"API not usable ({type(e).__name__}); page left as is.")
        return 0
    if not found:
        print("No recent games; page left as is.")
        return 0
    block = render(*found)
    if not block:
        print("Nothing to render; page left as is.")
        return 0

    index = ROOT / "index.html"
    page = index.read_text(encoding="utf-8")
    if page.count(START) != 1 or page.count(END) != 1:
        print("Markers missing in index.html.")
        return 1
    head, rest = page.split(START)
    new_page = head + block + rest.split(END)[1]
    if new_page == page:
        print("Already up to date.")
        return 0
    index.write_text(new_page, encoding="utf-8")

    # Tell crawlers the home page changed.
    sitemap = ROOT / "sitemap.xml"
    xml = sitemap.read_text(encoding="utf-8")
    xml2 = re.sub(
        r"(<loc>https://www\.heatcheckr\.com/</loc>\s*<lastmod>)[^<]*(</lastmod>)",
        rf"\g<1>{date.today().isoformat()}\g<2>", xml, count=1,
    )
    if xml2 != xml:
        sitemap.write_text(xml2, encoding="utf-8")
    print(f"Updated: {len(found[0])} games for {found[1]}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
