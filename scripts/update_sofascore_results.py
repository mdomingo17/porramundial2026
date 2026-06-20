#!/usr/bin/env python3
"""Update data/results.json using Sofascore event IDs already present in predictions_all.json.

This intentionally depends only on GitHub Actions + requests.
Sofascore does not provide a guaranteed public API, so if their endpoint changes,
this script will fail without breaking the published static site.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS_PATH = ROOT / "data" / "predictions_all.json"
RESULTS_PATH = ROOT / "data" / "results.json"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; porra-mundial-2026/1.0)",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.sofascore.com/",
})


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_event(event_id: str) -> dict[str, Any] | None:
    url = f"https://www.sofascore.com/api/v1/event/{event_id}"
    try:
        response = SESSION.get(url, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload.get("event") or payload
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: could not fetch event {event_id}: {exc}")
        return None


def event_is_finished(event: dict[str, Any]) -> bool:
    status = (event.get("status") or {})
    status_type = str(status.get("type") or "").lower()
    description = str(status.get("description") or "").lower()
    return status_type in {"finished", "afterpenalties", "afterextratime"} or "ended" in description or "finished" in description


def get_score(event: dict[str, Any]) -> tuple[int | None, int | None]:
    home_score = event.get("homeScore") or {}
    away_score = event.get("awayScore") or {}
    # current is the final full-time score for completed football events in Sofascore.
    h = home_score.get("current")
    a = away_score.get("current")
    if h is None or a is None:
        h = home_score.get("normaltime") or home_score.get("display")
        a = away_score.get("normaltime") or away_score.get("display")
    try:
        return int(h), int(a)
    except Exception:  # noqa: BLE001
        return None, None


def main() -> None:
    predictions = load_json(PREDICTIONS_PATH)
    schedule = predictions.get("schedule", [])
    old_results = load_json(RESULTS_PATH) if RESULTS_PATH.exists() else {"matches": []}
    old_by_id = {str(m.get("source_id")): m for m in old_results.get("matches", []) if m.get("source_id")}

    updated_matches = []
    completed = 0

    for match in schedule:
        source_id = str(match.get("source_id") or "")
        if not source_id:
            continue

        result = dict(match)
        previous = old_by_id.get(source_id, {})
        event = fetch_event(source_id)

        if event and event_is_finished(event):
            home_goals, away_goals = get_score(event)
            if home_goals is not None and away_goals is not None:
                result.update({
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "status": "finished",
                    "source": "Sofascore GitHub Action",
                })
                completed += 1
            else:
                result.update(previous or {"status": "scheduled"})
        elif previous.get("status") == "finished":
            # Keep known finished results if one Sofascore request fails.
            result.update(previous)
            completed += 1
        else:
            result.update({"home_goals": None, "away_goals": None, "status": "scheduled", "source": "Sofascore GitHub Action"})

        updated_matches.append(result)

    output = {
        "meta": {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "Sofascore event endpoints via GitHub Actions",
            "completed_matches": completed,
            "total_group_matches": len(updated_matches),
        },
        "matches": updated_matches,
    }
    save_json(RESULTS_PATH, output)
    print(f"Updated {completed}/{len(updated_matches)} matches")


if __name__ == "__main__":
    main()
