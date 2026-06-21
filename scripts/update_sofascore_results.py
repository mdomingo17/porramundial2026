#!/usr/bin/env python3
"""Update data/results.json from Sofascore scheduled-events endpoint.

Important: the IDs inside predictions_all.json are internal ESPN/ChatGPT ids, not Sofascore ids.
So this script does NOT rely on source_id. It queries Sofascore by calendar date and
matches World Cup games by team names/codes.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS_PATH = ROOT / "data" / "predictions_all.json"
RESULTS_PATH = ROOT / "data" / "results.json"

TOURNAMENT_ID = 16
SEASON_ID = 58210
TOURNAMENT_START = date(2026, 6, 11)
TOURNAMENT_END = date(2026, 7, 19)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.sofascore.com/es/football/tournament/world/world-championship/16#id:58210",
    "Origin": "https://www.sofascore.com",
})

ALIASES = {
    "mexico": {"mexico", "méxico"},
    "south africa": {"south africa", "sudafrica", "sudáfrica"},
    "south korea": {"south korea", "korea republic", "republic of korea", "corea del sur"},
    "czechia": {"czechia", "czech republic", "republica checa", "república checa"},
    "bosnia and herzegovina": {"bosnia and herzegovina", "bosnia", "bosnia herzegovina", "bosnia y herzegovina"},
    "qatar": {"qatar", "catar"},
    "switzerland": {"switzerland", "suiza"},
    "brazil": {"brazil", "brasil"},
    "morocco": {"morocco", "marruecos"},
    "haiti": {"haiti", "haití"},
    "scotland": {"scotland", "escocia"},
    "united states": {"united states", "usa", "estados unidos", "united states of america"},
    "paraguay": {"paraguay"},
    "australia": {"australia"},
    "turkey": {"turkey", "turkiye", "türkiye", "turquia", "turquía"},
    "germany": {"germany", "alemania"},
    "curacao": {"curacao", "curaçao", "curazao"},
    "ivory coast": {"ivory coast", "cote divoire", "côte d’ivoire", "côte d'ivoire", "costa de marfil"},
    "ecuador": {"ecuador"},
    "netherlands": {"netherlands", "paises bajos", "países bajos", "holanda"},
    "japan": {"japan", "japon", "japón"},
    "sweden": {"sweden", "suecia"},
    "tunisia": {"tunisia", "tunez", "túnez"},
    "belgium": {"belgium", "belgica", "bélgica"},
    "egypt": {"egypt", "egipto"},
    "iran": {"iran", "irán"},
    "new zealand": {"new zealand", "nueva zelanda"},
    "spain": {"spain", "espana", "españa"},
    "cape verde": {"cape verde", "cabo verde"},
    "saudi arabia": {"saudi arabia", "arabia saudi", "arabia saudí"},
    "uruguay": {"uruguay"},
    "france": {"france", "francia"},
    "senegal": {"senegal"},
    "iraq": {"iraq", "irak"},
    "norway": {"norway", "noruega"},
    "argentina": {"argentina"},
    "algeria": {"algeria", "argelia"},
    "austria": {"austria"},
    "jordan": {"jordan", "jordania"},
    "portugal": {"portugal"},
    "dr congo": {"dr congo", "d r congo", "congo dr", "congo", "rd congo", "republica democratica del congo", "república democrática del congo"},
    "uzbekistan": {"uzbekistan", "uzbekistán"},
    "colombia": {"colombia"},
    "england": {"england", "inglaterra"},
    "croatia": {"croatia", "croacia"},
    "ghana": {"ghana"},
    "panama": {"panama", "panamá"},
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return text


def team_tokens(team: dict[str, Any] | str) -> set[str]:
    if isinstance(team, dict):
        values = [team.get("name"), team.get("source"), team.get("code")]
    else:
        values = [team]
    out = {norm(v) for v in values if v}
    for v in list(out):
        out |= {norm(a) for a in ALIASES.get(v, set())}
    return {v for v in out if v}


def event_team_tokens(team: dict[str, Any]) -> set[str]:
    vals = [team.get("name"), team.get("shortName"), team.get("slug"), team.get("nameCode")]
    country = team.get("country") or {}
    vals += [country.get("name"), country.get("alpha2"), country.get("alpha3")]
    out = {norm(v) for v in vals if v}
    for v in list(out):
        out |= {norm(a) for a in ALIASES.get(v, set())}
    return {v for v in out if v}


def is_world_cup_event(event: dict[str, Any]) -> bool:
    tournament = event.get("tournament") or {}
    unique = tournament.get("uniqueTournament") or {}
    season = event.get("season") or {}
    if unique.get("id") == TOURNAMENT_ID and season.get("id") == SEASON_ID:
        return True
    # Fallback if Sofascore changes nesting but leaves names readable.
    text = norm(" ".join(str(x or "") for x in [tournament.get("name"), unique.get("name"), season.get("name")]))
    return "world championship" in text or "world cup" in text


def fetch_events_for_day(day: date) -> list[dict[str, Any]]:
    url = f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{day.isoformat()}"
    try:
        response = SESSION.get(url, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: could not fetch {day}: {exc}")
        return []
    return [e for e in payload.get("events", []) if is_world_cup_event(e)]


def event_is_finished(event: dict[str, Any]) -> bool:
    status = event.get("status") or {}
    status_type = norm(status.get("type"))
    status_desc = norm(status.get("description"))
    return status_type in {"finished", "afterpenalties", "afterextratime"} or "finished" in status_desc or "ended" in status_desc


def get_score(event: dict[str, Any]) -> tuple[int | None, int | None]:
    home_score = event.get("homeScore") or {}
    away_score = event.get("awayScore") or {}
    for key in ("current", "normaltime", "display"):
        h, a = home_score.get(key), away_score.get(key)
        if h is not None and a is not None:
            try:
                return int(h), int(a)
            except Exception:  # noqa: BLE001
                pass
    return None, None


def event_key(event: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    return frozenset(event_team_tokens(event.get("homeTeam") or {})), frozenset(event_team_tokens(event.get("awayTeam") or {}))


def match_event(match: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    want_home = team_tokens(match.get("home_team") or {})
    want_away = team_tokens(match.get("away_team") or {})
    for event in events:
        ev_home, ev_away = event_key(event)
        if want_home & ev_home and want_away & ev_away:
            return event
    return None


def date_window() -> list[date]:
    today = datetime.now(timezone.utc).date()
    start = max(TOURNAMENT_START, today - timedelta(days=3))
    end = min(TOURNAMENT_END, today + timedelta(days=1))
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def main() -> None:
    predictions = load_json(PREDICTIONS_PATH)
    schedule = predictions.get("schedule", [])
    old_results = load_json(RESULTS_PATH) if RESULTS_PATH.exists() else {"matches": []}
    old_by_match_id = {str(m.get("match_id")): m for m in old_results.get("matches", []) if m.get("match_id")}

    events: list[dict[str, Any]] = []
    for day in date_window():
        day_events = fetch_events_for_day(day)
        print(f"{day.isoformat()}: {len(day_events)} World Cup events")
        events.extend(day_events)

    updated_matches = []
    completed = 0
    newly_found = 0

    for match in schedule:
        match_id = str(match.get("match_id") or "")
        previous = old_by_match_id.get(match_id, {})
        result = dict(match)

        event = match_event(match, events)
        if event and event_is_finished(event):
            home_goals, away_goals = get_score(event)
            if home_goals is not None and away_goals is not None:
                result.update({
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "status": "finished",
                    "source": "Sofascore scheduled-events GitHub Action",
                    "sofascore_event_id": event.get("id"),
                })
                completed += 1
                if previous.get("status") != "finished" or previous.get("home_goals") != home_goals or previous.get("away_goals") != away_goals:
                    newly_found += 1
            else:
                result.update(previous or {"home_goals": None, "away_goals": None, "status": "scheduled"})
        elif previous.get("status") == "finished":
            result.update(previous)
            completed += 1
        else:
            result.update({"home_goals": None, "away_goals": None, "status": "scheduled", "source": "Sofascore scheduled-events GitHub Action"})

        updated_matches.append(result)

    output = {
        "meta": {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "Sofascore scheduled-events via GitHub Actions",
            "completed_matches": completed,
            "new_or_changed_matches": newly_found,
            "total_group_matches": len(updated_matches),
        },
        "matches": updated_matches,
    }
    save_json(RESULTS_PATH, output)
    print(f"Updated {completed}/{len(updated_matches)} finished matches; new/changed: {newly_found}")


if __name__ == "__main__":
    main()
