#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS_PATH = ROOT / "data" / "predictions_all.json"
RESULTS_PATH = ROOT / "data" / "results.json"

START = date(2026, 6, 11)
END = date(2026, 7, 19)

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"


ALIASES = {
    "mexico": {"mexico", "méxico", "mex"},
    "south africa": {"south africa", "sudafrica", "sudáfrica", "rsa"},
    "south korea": {"south korea", "korea republic", "corea del sur", "kor"},
    "czechia": {"czechia", "czech republic", "república checa", "republica checa", "cze"},
    "canada": {"canada", "can"},
    "bosnia and herzegovina": {"bosnia and herzegovina", "bosnia", "bosnia y herzegovina", "bih"},
    "qatar": {"qatar", "catar", "qat"},
    "switzerland": {"switzerland", "suiza", "sui"},
    "brazil": {"brazil", "brasil", "bra"},
    "morocco": {"morocco", "marruecos", "mar"},
    "haiti": {"haiti", "haití", "hti"},
    "scotland": {"scotland", "escocia", "sco"},
    "united states": {"united states", "usa", "estados unidos"},
    "paraguay": {"paraguay", "par"},
    "australia": {"australia", "aus"},
    "turkey": {"turkey", "turkiye", "türkiye", "turquía", "turquia", "tur"},
    "germany": {"germany", "alemania", "ger"},
    "curacao": {"curacao", "curaçao", "curazao", "cuw"},
    "ivory coast": {"ivory coast", "côte d'ivoire", "costa de marfil", "civ"},
    "ecuador": {"ecuador", "ecu"},
    "netherlands": {"netherlands", "paises bajos", "países bajos", "holanda", "ned"},
    "japan": {"japan", "japon", "japón", "jpn"},
    "sweden": {"sweden", "suecia", "swe"},
    "tunisia": {"tunisia", "tunez", "túnez", "tun"},
    "belgium": {"belgium", "belgica", "bélgica", "bel"},
    "egypt": {"egypt", "egipto", "egy"},
    "iran": {"iran", "irán", "iri"},
    "new zealand": {"new zealand", "nueva zelanda", "nzl"},
    "spain": {"spain", "espana", "españa", "esp"},
    "cape verde": {"cape verde", "cabo verde", "cpv"},
    "saudi arabia": {"saudi arabia", "arabia saudí", "arabia saudi", "ksa"},
    "uruguay": {"uruguay", "uru"},
    "france": {"france", "francia", "fra"},
    "senegal": {"senegal", "sen"},
    "iraq": {"iraq", "irak", "irq"},
    "norway": {"norway", "noruega", "nor"},
    "argentina": {"argentina", "arg"},
    "algeria": {"algeria", "argelia", "dza"},
    "austria": {"austria", "aut"},
    "jordan": {"jordan", "jordania", "jor"},
    "portugal": {"portugal", "por"},
    "dr congo": {"dr congo", "congo dr", "rd congo", "cod"},
    "uzbekistan": {"uzbekistan", "uzbekistán", "uzb"},
    "colombia": {"colombia", "col"},
    "england": {"england", "inglaterra", "eng"},
    "croatia": {"croatia", "croacia", "cro"},
    "ghana": {"ghana", "gha"},
    "panama": {"panama", "panamá", "pan"},
}


def norm(value) -> str:
    text = str(value or "").lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def team_obj_from_competitor(comp: dict) -> dict:
    team = comp.get("team") or {}
    return {
        "name": team.get("displayName") or team.get("shortDisplayName") or team.get("name") or team.get("location"),
        "code": team.get("abbreviation"),
        "source": team.get("displayName") or team.get("name"),
    }


def team_code(team: dict | None) -> str:
    if not isinstance(team, dict):
        return ""
    return str(team.get("code") or team.get("abbreviation") or "").upper().strip()


def expand_aliases(values: set[str]) -> set[str]:
    out = set(values)
    for value in list(values):
        out |= {norm(alias) for alias in ALIASES.get(value, set())}
    return {v for v in out if v}


def tokens_from_prediction_team(team) -> set[str]:
    vals = []
    if isinstance(team, dict):
        vals += [team.get("name"), team.get("code"), team.get("source")]
    else:
        vals.append(team)
    return expand_aliases({norm(v) for v in vals if v})


def tokens_from_espn_competitor(comp) -> set[str]:
    team = comp.get("team") or {}
    vals = [
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("location"),
        team.get("abbreviation"),
    ]
    return expand_aliases({norm(v) for v in vals if v})


def fetch_day(day: date) -> list[dict]:
    url = f"{ESPN_SCOREBOARD}?limit=200&dates={day:%Y%m%d}"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    for attempt in range(1, 4):
        try:
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data.get("events", [])
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"Warning: ESPN request failed for {day} attempt {attempt}/3: {exc}")
            time.sleep(2 * attempt)

    print(f"Warning: giving up ESPN request for {day}")
    return []


def event_finished(event: dict) -> bool:
    competitions = event.get("competitions") or []
    if not competitions:
        return False

    status = competitions[0].get("status") or event.get("status") or {}
    status_type = status.get("type") or {}

    state = norm(status_type.get("state"))
    description = norm(status_type.get("description"))
    detail = norm(status_type.get("detail"))

    return (
        bool(status_type.get("completed"))
        or state == "post"
        or "full time" in description
        or "final" in description
        or "full time" in detail
        or "final" in detail
    )


def event_status(event: dict) -> str:
    if event_finished(event):
        return "finished"

    competitions = event.get("competitions") or []
    status = (competitions[0].get("status") if competitions else None) or event.get("status") or {}
    status_type = status.get("type") or {}
    state = norm(status_type.get("state"))
    if state == "in":
        return "live"
    return "scheduled"


def event_score(event: dict):
    competitions = event.get("competitions") or [{}]
    competitors = competitions[0].get("competitors") or []

    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)

    if not home or not away:
        return None

    def parse_score(value):
        try:
            return int(value)
        except Exception:
            return None

    home_score = parse_score(home.get("score"))
    away_score = parse_score(away.get("score"))

    if home_score is None or away_score is None:
        return None

    return home_score, away_score, home, away


def event_winner_code(event: dict) -> str | None:
    competitions = event.get("competitions") or [{}]
    competitors = competitions[0].get("competitors") or []
    for comp in competitors:
        if comp.get("winner") is True:
            return team_code(team_obj_from_competitor(comp))
    scored = event_score(event)
    if not scored:
        return None
    home_goals, away_goals, home, away = scored
    if home_goals > away_goals:
        return team_code(team_obj_from_competitor(home))
    if away_goals > home_goals:
        return team_code(team_obj_from_competitor(away))
    return None


def match_event(match: dict, events: list[dict]):
    source_id = str(match.get("source_id") or "")
    want_home = tokens_from_prediction_team(match.get("home_team"))
    want_away = tokens_from_prediction_team(match.get("away_team"))

    for event in events:
        if source_id and str(event.get("id")) == source_id:
            return event

        scored = event_score(event)
        if not scored:
            continue

        _, _, home, away = scored
        if want_home & tokens_from_espn_competitor(home) and want_away & tokens_from_espn_competitor(away):
            return event

    return None


def event_text(event: dict) -> str:
    competitions = event.get("competitions") or []
    comp = competitions[0] if competitions else {}
    notes = comp.get("notes") or []
    note_text = " ".join(str(n.get("headline") or n.get("text") or "") for n in notes if isinstance(n, dict))
    values = [
        event.get("name"),
        event.get("shortName"),
        event.get("season", {}).get("type"),
        event.get("season", {}).get("slug"),
        comp.get("type", {}).get("text") if isinstance(comp.get("type"), dict) else comp.get("type"),
        note_text,
    ]
    return norm(" ".join(str(v or "") for v in values))


def event_date(event: dict) -> date | None:
    raw = event.get("date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        return None


def detect_stage(event: dict, group_source_ids: set[str]) -> str:
    event_id = str(event.get("id") or "")
    if event_id in group_source_ids:
        return "group"

    txt = event_text(event)

    if "round of 32" in txt or "dieciseis" in txt:
        return "round_of_32"
    if "round of 16" in txt or "octav" in txt:
        return "round_of_16"
    if "quarter" in txt or "cuarto" in txt:
        return "quarterfinals"
    if "semi" in txt:
        return "semifinals"
    if "third" in txt or "3rd" in txt or "place" in txt or "tercer" in txt:
        return "third_place"
    if "final" in txt:
        return "final"

    d = event_date(event)
    if not d:
        return "unknown"

    # Fallback por calendario del Mundial 2026.
    if d <= date(2026, 6, 27):
        return "group"
    if d <= date(2026, 7, 3):
        return "round_of_32"
    if d <= date(2026, 7, 7):
        return "round_of_16"
    if d <= date(2026, 7, 11):
        return "quarterfinals"
    if d <= date(2026, 7, 15):
        return "semifinals"
    if d == date(2026, 7, 18):
        return "third_place"
    if d >= date(2026, 7, 19):
        return "final"

    return "unknown"


def load_json(path: Path, fallback):
    if not path.exists() or path.stat().st_size == 0:
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def result_from_event(event: dict, base: dict | None, group_source_ids: set[str]) -> dict | None:
    scored = event_score(event)
    if not scored:
        return None

    home_goals, away_goals, home_comp, away_comp = scored
    home_team = team_obj_from_competitor(home_comp)
    away_team = team_obj_from_competitor(away_comp)

    if not home_team.get("code") or not away_team.get("code"):
        return None

    if team_code(home_team) == "TBD" or team_code(away_team) == "TBD":
        return None

    result = dict(base or {})
    stage = (base or {}).get("stage") or detect_stage(event, group_source_ids)

    result.update(
        {
            "match_id": (base or {}).get("match_id") or f"ESPN_{event.get('id')}",
            "source_id": str(event.get("id") or ""),
            "stage": stage,
            "home_team": (base or {}).get("home_team") or home_team,
            "away_team": (base or {}).get("away_team") or away_team,
            "actual_home_team": home_team,
            "actual_away_team": away_team,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "winner_code": event_winner_code(event),
            "status": event_status(event),
            "source": "ESPN public scoreboard",
            "espn_event_id": str(event.get("id") or ""),
            "kickoff": event.get("date"),
        }
    )

    if stage == "group" and base:
        result["group"] = base.get("group")
        result["matchday"] = base.get("matchday")

    return result


def material_match_view(match: dict) -> dict:
    return {
        "match_id": match.get("match_id"),
        "source_id": str(match.get("source_id") or ""),
        "stage": match.get("stage"),
        "group": match.get("group"),
        "home_code": team_code(match.get("home_team") or match.get("actual_home_team")),
        "away_code": team_code(match.get("away_team") or match.get("actual_away_team")),
        "home_goals": match.get("home_goals"),
        "away_goals": match.get("away_goals"),
        "winner_code": match.get("winner_code"),
        "status": match.get("status"),
        "kickoff": match.get("kickoff"),
    }


def main():
    predictions = load_json(PREDICTIONS_PATH, {"schedule": []})
    old = load_json(RESULTS_PATH, {"matches": []})

    group_source_ids = {str(m.get("source_id")) for m in predictions.get("schedule", []) if m.get("source_id")}
    old_by_id = {m.get("match_id"): m for m in old.get("matches", [])}
    old_by_source_id = {str(m.get("source_id")): m for m in old.get("matches", []) if m.get("source_id")}

    events = []
    for i in range((END - START).days + 1):
        day = START + timedelta(days=i)
        day_events = fetch_day(day)
        print(day.isoformat(), len(day_events), "events")
        events.extend(day_events)

    used_event_ids = set()
    matches = []

    # 1) Mantener los 72 partidos de grupos con sus match_id originales.
    for scheduled_match in predictions.get("schedule", []):
        previous = old_by_id.get(scheduled_match.get("match_id"))
        event = match_event(scheduled_match, events)
        result = None

        if event:
            used_event_ids.add(str(event.get("id") or ""))
            result = result_from_event(event, scheduled_match, group_source_ids)

        if not result and previous and previous.get("status") == "finished":
            result = previous

        if not result:
            result = dict(scheduled_match)
            result.update(
                {
                    "home_goals": None,
                    "away_goals": None,
                    "winner_code": None,
                    "status": "scheduled",
                    "source": "ESPN public scoreboard",
                }
            )

        matches.append(result)

    # 2) Añadir partidos de eliminatorias de ESPN cuando tengan equipos reales.
    for event in events:
        event_id = str(event.get("id") or "")
        if not event_id or event_id in used_event_ids:
            continue

        stage = detect_stage(event, group_source_ids)
        if stage == "group":
            continue

        previous = old_by_source_id.get(event_id)
        result = result_from_event(event, previous, group_source_ids)
        if result:
            result["stage"] = stage
            matches.append(result)
            used_event_ids.add(event_id)

    # 3) Preservar partidos antiguos no devueltos temporalmente por ESPN.
    current_ids = {m.get("match_id") for m in matches}
    current_source_ids = {str(m.get("source_id") or "") for m in matches}
    for old_match in old.get("matches", []):
        if old_match.get("match_id") in current_ids:
            continue
        if old_match.get("source_id") and str(old_match.get("source_id")) in current_source_ids:
            continue
        if old_match.get("status") in {"finished", "live", "scheduled"}:
            matches.append(old_match)

    old_material = sorted((material_match_view(m) for m in old.get("matches", [])), key=lambda x: str(x.get("match_id")))
    new_material = sorted((material_match_view(m) for m in matches), key=lambda x: str(x.get("match_id")))
    changed = old_material != new_material

    completed = sum(1 for m in matches if m.get("status") == "finished")

    out = {
        "meta": {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "ESPN public scoreboard via GitHub Actions",
            "completed_matches": completed,
            "total_matches": len(matches),
            "group_matches": sum(1 for m in matches if m.get("stage") == "group"),
            "knockout_matches": sum(1 for m in matches if m.get("stage") != "group"),
            "changed": changed,
        },
        "matches": matches,
    }

    print(f"Finished {completed}/{len(matches)}; changed={changed}")

    if not changed and old.get("matches"):
        print("No material changes. Leaving results.json unchanged.")
        return

    RESULTS_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("results.json updated")


if __name__ == "__main__":
    main()
