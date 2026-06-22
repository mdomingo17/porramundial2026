#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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

    with urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    return data.get("events", [])


def event_finished(event: dict) -> bool:
    competitions = event.get("competitions") or []

    if not competitions:
        return False

    status = competitions[0].get("status") or event.get("status") or {}
    status_type = status.get("type") or {}

    state = norm(status_type.get("state"))
    description = norm(status_type.get("description"))

    return (
        bool(status_type.get("completed"))
        or state == "post"
        or "full time" in description
        or "final" in description
    )


def event_score(event: dict):
    competitions = event.get("competitions") or [{}]
    competitors = competitions[0].get("competitors") or []

    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)

    if not home or not away:
        return None

    return int(home.get("score", 0)), int(away.get("score", 0)), home, away


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


def load_json(path: Path, fallback):
    if not path.exists():
        return fallback

    return json.loads(path.read_text(encoding="utf-8"))


def main():
    predictions = load_json(PREDICTIONS_PATH, {"schedule": []})
    old = load_json(RESULTS_PATH, {"matches": []})

    old_by_id = {m.get("match_id"): m for m in old.get("matches", [])}

    today = datetime.now(timezone.utc).date()

    # Mira desde el inicio hasta mañana.
    # Así no se pierden resultados aunque GitHub haya saltado algún schedule.
    start = START
    end = min(END, today + timedelta(days=1))

    events = []

    for i in range((end - start).days + 1):
        day = start + timedelta(days=i)
        day_events = fetch_day(day)
        print(day.isoformat(), len(day_events), "events")
        events.extend(day_events)

    matches = []
    changed = 0
    finished_count = 0

    for match in predictions.get("schedule", []):
        match_id = match.get("match_id")
        previous = old_by_id.get(match_id)

        result = dict(match)
        event = match_event(match, events)

        if event and event_finished(event):
            score = event_score(event)

            if score:
                home_goals, away_goals, _, _ = score

                result.update(
                    {
                        "home_goals": home_goals,
                        "away_goals": away_goals,
                        "status": "finished",
                        "source": "ESPN public scoreboard",
                        "espn_event_id": event.get("id"),
                    }
                )

                finished_count += 1

                if (
                    not previous
                    or previous.get("home_goals") != home_goals
                    or previous.get("away_goals") != away_goals
                    or previous.get("status") != "finished"
                ):
                    changed += 1

        elif previous and previous.get("status") == "finished":
            result.update(previous)
            finished_count += 1

        else:
            result.update(
                {
                    "home_goals": None,
                    "away_goals": None,
                    "status": "scheduled",
                    "source": "ESPN public scoreboard",
                }
            )

        matches.append(result)

    out = {
        "meta": {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "ESPN public scoreboard via GitHub Actions",
            "completed_matches": finished_count,
            "new_or_changed_matches": changed,
            "total_group_matches": len(matches),
        },
        "matches": matches,
    }

    print(f"Finished {finished_count}/{len(matches)}; new/changed {changed}")

    if changed == 0 and old.get("matches"):
        print("No new finished matches. Leaving results.json unchanged.")
        return

    RESULTS_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("results.json updated")


if __name__ == "__main__":
    main()
