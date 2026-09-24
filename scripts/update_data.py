"""LigPuan yerel veri modeli: python scripts/update_data.py

data/team-map.json ve data/matches/*.json okunur, doğrulanır, sezon toplamları
saklanan maçlardan yeniden hesaplanır ve data/site.json'a tek işlemde yazılır.
Doğrulama başarısızsa yayındaki dosya değişmez.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
TEAM_MAP = ROOT / "data" / "team-map.json"
MATCH_DIR = ROOT / "data" / "matches"
SITE_FILE = ROOT / "data" / "site.json"

STAT_KEYS = (
    "shots",
    "shots_on_target",
    "corners",
    "offsides",
    "passes",
    "accurate_passes",
    "possession",
    "saves",
    "fouls",
    "yellow_cards",
    "red_cards",
)

AVERAGE_KEYS = ("possession",)

MATCH_REQUIRED = (
    "match_id",
    "round",
    "date",
    "home_team_id",
    "away_team_id",
    "home_goals",
    "away_goals",
    "stats",
)


class DataError(Exception):
    pass


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DataError(f"dosya yok: {path}") from error
    except json.JSONDecodeError as error:
        raise DataError(f"bozuk JSON: {path} ({error})") from error


def load_teams():
    payload = read_json(TEAM_MAP)
    teams = payload.get("teams")
    if not isinstance(teams, list) or not teams:
        raise DataError("team-map.json içinde teams listesi yok")
    seen = set()
    result = []
    for team in teams:
        for key in ("id", "name", "code", "badge"):
            if not isinstance(team.get(key), str) or not team[key]:
                raise DataError(f"takım alanı eksik: {team}")
        if team["id"] in seen:
            raise DataError(f"takım kimliği tekrar ediyor: {team['id']}")
        seen.add(team["id"])
        deduction = team.get("points_deduction", 0)
        if not isinstance(deduction, int) or isinstance(deduction, bool) or deduction < 0:
            raise DataError(f"geçersiz ceza puanı: {team['id']}")
        row = {"id": team["id"], "name": team["name"], "code": team["code"], "badge": team["badge"]}
        if deduction:
            row["points_deduction"] = deduction
        result.append(row)
    return payload.get("season"), payload.get("league"), result


def load_matches(season, league):
    files = sorted(MATCH_DIR.glob("*.json"))
    if not files:
        raise DataError(f"maç dosyası yok: {MATCH_DIR}")
    matches = []
    for path in files:
        payload = read_json(path)
        file_season = payload.get("season")
        if file_season is None:
            raise DataError(f"maç dosyasında sezon alanı yok: {path.name}")
        if file_season != season:
            raise DataError(f"sezon uyuşmuyor: {path.name} -> {file_season} (beklenen {season})")
        if payload.get("league") != league:
            raise DataError(f"lig uyuşmuyor: {path.name} -> {payload.get('league')} (beklenen {league})")
        payload_matches = payload.get("matches")
        if not isinstance(payload_matches, list):
            raise DataError(f"matches listesi yok: {path}")
        for match in payload_matches:
            match = dict(match)
            match["_source"] = path.name
            matches.append(match)
    return matches


def validate(teams, matches):
    team_ids = {team["id"] for team in teams}
    seen = {}
    for match in matches:
        label = f"{match.get('_source')}:{match.get('match_id')}"
        for key in MATCH_REQUIRED:
            if key not in match:
                raise DataError(f"zorunlu alan eksik ({key}): {label}")
        if match["match_id"] in seen:
            raise DataError(f"maç kimliği tekrar ediyor: {match['match_id']}")
        seen[match["match_id"]] = label
        if match["home_team_id"] not in team_ids:
            raise DataError(f"bilinmeyen takım: {label} -> {match['home_team_id']}")
        if match["away_team_id"] not in team_ids:
            raise DataError(f"bilinmeyen takım: {label} -> {match['away_team_id']}")
        if match["home_team_id"] == match["away_team_id"]:
            raise DataError(f"takım kendisiyle eşleşmiş: {label}")
        for key in ("home_goals", "away_goals"):
            value = match[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise DataError(f"geçersiz gol değeri ({key}): {label} -> {value}")
        half_keys = ("half_time_home_goals", "half_time_away_goals")
        if any(key in match for key in half_keys):
            if not all(key in match for key in half_keys):
                raise DataError(f"eksik ilk yarı skor çifti: {label}")
            for half_key, full_key in zip(half_keys, ("home_goals", "away_goals")):
                value = match[half_key]
                if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= match[full_key]:
                    raise DataError(f"geçersiz ilk yarı skoru ({half_key}): {label} -> {value}")
        stats = match["stats"]
        if not isinstance(stats, dict):
            raise DataError(f"stats nesnesi değil: {label}")
        for team_id, values in stats.items():
            if team_id not in team_ids:
                raise DataError(f"stats içinde bilinmeyen takım: {label} -> {team_id}")
            if team_id not in (match["home_team_id"], match["away_team_id"]):
                raise DataError(f"istatistik maçta oynamayan takıma ait: {label} -> {team_id}")
            if not isinstance(values, dict):
                raise DataError(f"takım istatistiği nesne değil: {label} -> {team_id}")
            for key, value in values.items():
                if key not in STAT_KEYS:
                    raise DataError(f"bilinmeyen istatistik alanı: {label} -> {key}")
                if value is None:
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                    raise DataError(f"geçersiz istatistik değeri: {label} -> {key}={value}")


def new_team_state():
    return {
        "played": 0,
        "won": 0,
        "drawn": 0,
        "lost": 0,
        "goals_for": 0,
        "goals_against": 0,
        "clean_sheets": 0,
        "shots_faced": {"sum": 0, "coverage": 0},
        "metrics": {key: {"sum": 0, "coverage": 0} for key in STAT_KEYS},
    }


def aggregate(teams, matches):
    state = {team["id"]: new_team_state() for team in teams}

    def add_metric(team_id, key, value):
        if value is None:
            return
        state[team_id]["metrics"][key]["sum"] += value
        state[team_id]["metrics"][key]["coverage"] += 1

    for match in matches:
        home = match["home_team_id"]
        away = match["away_team_id"]
        home_goals = match["home_goals"]
        away_goals = match["away_goals"]
        for team_id in (home, away):
            state[team_id]["played"] += 1
        state[home]["goals_for"] += home_goals
        state[home]["goals_against"] += away_goals
        state[away]["goals_for"] += away_goals
        state[away]["goals_against"] += home_goals
        if home_goals > away_goals:
            state[home]["won"] += 1
            state[away]["lost"] += 1
        elif home_goals < away_goals:
            state[away]["won"] += 1
            state[home]["lost"] += 1
        else:
            state[home]["drawn"] += 1
            state[away]["drawn"] += 1
        if away_goals == 0:
            state[home]["clean_sheets"] += 1
        if home_goals == 0:
            state[away]["clean_sheets"] += 1
        for team_id, values in match["stats"].items():
            for key, value in values.items():
                add_metric(team_id, key, value)
        home_shots = match["stats"].get(home, {}).get("shots")
        away_shots = match["stats"].get(away, {}).get("shots")
        if away_shots is not None:
            state[home]["shots_faced"]["sum"] += away_shots
            state[home]["shots_faced"]["coverage"] += 1
        if home_shots is not None:
            state[away]["shots_faced"]["sum"] += home_shots
            state[away]["shots_faced"]["coverage"] += 1
    return state


def standings_rows(teams, state):
    rows = []
    for team in teams:
        team_state = state[team["id"]]
        deduction = team.get("points_deduction", 0)
        points = 3 * team_state["won"] + team_state["drawn"] - deduction
        rows.append(
            {
                "team_id": team["id"],
                "name": team["name"],
                "played": team_state["played"],
                "won": team_state["won"],
                "drawn": team_state["drawn"],
                "lost": team_state["lost"],
                "goals_for": team_state["goals_for"],
                "goals_against": team_state["goals_against"],
                "average": team_state["goals_for"] - team_state["goals_against"],
                "points": points,
                "points_deduction": deduction,
            }
        )
    rows.sort(key=lambda row: (-row["points"], -row["average"], row["name"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def classify_source(matches):
    kinds = set()
    for match in matches:
        match_id = str(match.get("match_id", ""))
        provider = match.get("provider")
        if provider == "goal_api" or match_id.startswith("goal-"):
            kinds.add("provider")
        elif provider == "ornek" or match_id.startswith("ornek-"):
            kinds.add("sample")
        else:
            kinds.add("local")
    if not kinds:
        return "empty"
    if len(kinds) == 1:
        return kinds.pop()
    return "mixed"


def latest_round_info(matches):
    rounds = [match["round"] for match in matches if isinstance(match.get("round"), int)]
    if not rounds:
        return None, 0
    latest = max(rounds)
    count = sum(1 for match in matches if match.get("round") == latest)
    return latest, count


def build_insights(teams, matches):
    """Maç eşleşmesini koruyan dört kısa içgörü; yetersiz kapsamda kart üretmez."""
    rows = {team["id"]: {"played": 0, "passes": 0, "pass_points": 0, "pass_games": 0,
                         "shots": 0, "shot_goals": 0, "shot_games": 0,
                         "home_points": 0, "home_games": 0, "away_points": 0, "away_games": 0,
                         "half_games": 0, "lost_leads": 0, "dropped_points": 0}
            for team in teams}

    def points(scored, conceded):
        return 3 if scored > conceded else (1 if scored == conceded else 0)

    for match in matches:
        half_known = "half_time_home_goals" in match and "half_time_away_goals" in match
        for side, team_id, scored, conceded, half_scored, half_conceded in (
            ("home", match["home_team_id"], match["home_goals"], match["away_goals"],
             match.get("half_time_home_goals"), match.get("half_time_away_goals")),
            ("away", match["away_team_id"], match["away_goals"], match["home_goals"],
             match.get("half_time_away_goals"), match.get("half_time_home_goals")),
        ):
            row = rows[team_id]
            earned = points(scored, conceded)
            row["played"] += 1
            row[f"{side}_games"] += 1
            row[f"{side}_points"] += earned
            values = match["stats"].get(team_id, {})
            if values.get("passes") is not None:
                row["passes"] += values["passes"]
                row["pass_points"] += earned
                row["pass_games"] += 1
            if values.get("shots") is not None:
                row["shots"] += values["shots"]
                row["shot_goals"] += scored
                row["shot_games"] += 1
            if half_known:
                row["half_games"] += 1
                if half_scored > half_conceded and earned < 3:
                    row["lost_leads"] += 1
                    row["dropped_points"] += 3 - earned

    insights = []
    pass_rows = [(team_id, row) for team_id, row in rows.items() if row["pass_games"] >= 5]
    if len(pass_rows) >= 4:
        pass_median = median(row["passes"] / row["pass_games"] for _, row in pass_rows)
        point_median = median(row["pass_points"] / row["pass_games"] for _, row in pass_rows)
        candidates = [(team_id, row) for team_id, row in pass_rows
                      if row["passes"] / row["pass_games"] >= pass_median
                      and row["pass_points"] / row["pass_games"] < point_median]
        if candidates:
            team_id, row = max(candidates, key=lambda item: (item[1]["passes"] / item[1]["pass_games"],
                                                              -item[1]["pass_points"] / item[1]["pass_games"], item[0]))
            insights.append({"id": "pas-var-puan-yok", "team_id": team_id, "passes": row["passes"],
                             "points": row["pass_points"], "coverage": row["pass_games"], "played": row["played"]})

    shot_rows = [(team_id, row) for team_id, row in rows.items() if row["shot_games"] >= 5 and row["shots"] > 0]
    if len(shot_rows) >= 4:
        shot_median = median(row["shots"] / row["shot_games"] for _, row in shot_rows)
        candidates = [(team_id, row) for team_id, row in shot_rows
                      if row["shots"] / row["shot_games"] >= shot_median]
        if candidates:
            team_id, row = min(candidates, key=lambda item: (item[1]["shot_goals"] / item[1]["shots"],
                                                              -item[1]["shots"], item[0]))
            insights.append({"id": "sut-cok-gol-nerede", "team_id": team_id, "shots": row["shots"],
                             "goals": row["shot_goals"], "coverage": row["shot_games"], "played": row["played"]})

    candidates = [(team_id, row) for team_id, row in rows.items()
                  if row["home_games"] >= 2 and row["away_games"] >= 2
                  and row["home_points"] / row["home_games"] > row["away_points"] / row["away_games"]]
    if candidates:
        team_id, row = max(candidates, key=lambda item: (item[1]["home_points"] / item[1]["home_games"]
                                                          - item[1]["away_points"] / item[1]["away_games"], item[0]))
        insights.append({"id": "evde-aslan", "team_id": team_id,
                         "home_points": row["home_points"], "home_games": row["home_games"],
                         "away_points": row["away_points"], "away_games": row["away_games"]})

    candidates = [(team_id, row) for team_id, row in rows.items()
                  if row["half_games"] >= 5 and row["lost_leads"] > 0]
    if candidates:
        team_id, row = max(candidates, key=lambda item: (item[1]["dropped_points"], item[1]["lost_leads"], item[0]))
        insights.append({"id": "devre-arasi-rahatti", "team_id": team_id,
                         "lost_leads": row["lost_leads"], "dropped_points": row["dropped_points"],
                         "coverage": row["half_games"], "played": row["played"]})
    return insights


def build_site(season, league, teams, matches, state):
    metrics = {
        key: {
            "kind": "average" if key in AVERAGE_KEYS else "count",
            "values": {
                team["id"]: {
                    "sum": state[team["id"]]["metrics"][key]["sum"],
                    "coverage": state[team["id"]]["metrics"][key]["coverage"],
                }
                for team in teams
            },
        }
        for key in STAT_KEYS
    }
    metrics["clean_sheets"] = {
        "kind": "count",
        "values": {
            team["id"]: {"sum": state[team["id"]]["clean_sheets"], "coverage": state[team["id"]]["played"]}
            for team in teams
        },
    }
    metrics["shots_faced"] = {
        "kind": "count",
        "values": {
            team["id"]: {
                "sum": state[team["id"]]["shots_faced"]["sum"],
                "coverage": state[team["id"]]["shots_faced"]["coverage"],
            }
            for team in teams
        },
    }
    latest_round, latest_round_match_count = latest_round_info(matches)
    return {
        "season": season,
        "league": league,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "match_count": len(matches),
        "coverage_total": max((row["played"] for row in standings_rows(teams, state)), default=0),
        "source_kind": classify_source(matches),
        "latest_round": latest_round,
        "latest_round_match_count": latest_round_match_count,
        "teams": teams,
        "standings": standings_rows(teams, state),
        "metrics": metrics,
        "insights": build_insights(teams, matches) if classify_source(matches) == "provider" else [],
    }


def write_site(site):
    temporary = SITE_FILE.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(site, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, SITE_FILE)


def publish_site(site):
    """Render before replacing either public file; restore JSON if HTML fails."""
    if site["source_kind"] == "mixed":
        raise DataError("örnek ve gerçek maçlar aynı yayında birleştirilemez")
    if SITE_FILE.resolve() != (ROOT / "data" / "site.json").resolve():
        write_site(site)
        return

    import generate_site

    html = generate_site.build_page(site)
    previous = SITE_FILE.read_bytes() if SITE_FILE.exists() else None
    temporary_html = (ROOT / "index.html.tmp")
    try:
        temporary_html.write_text(html, encoding="utf-8", newline="\n")
        write_site(site)
        os.replace(temporary_html, ROOT / "index.html")
    except Exception:
        if previous is not None:
            temporary_site = SITE_FILE.with_suffix(".json.tmp")
            temporary_site.write_bytes(previous)
            os.replace(temporary_site, SITE_FILE)
        elif SITE_FILE.exists():
            SITE_FILE.unlink()
        temporary_html.unlink(missing_ok=True)
        raise


def main():
    try:
        season, league, teams = load_teams()
        matches = load_matches(season, league)
        validate(teams, matches)
        state = aggregate(teams, matches)
        site = build_site(season, league, teams, matches, state)
        publish_site(site)
    except DataError as error:
        print(f"HATA: {error}", file=sys.stderr)
        print("Yayındaki data/site.json değiştirilmedi.", file=sys.stderr)
        return 1
    print(f"ok: {len(matches)} maç, {len(teams)} takım -> {SITE_FILE.name}")
    for key in ("shots", "passes", "possession"):
        covered = sum(1 for team in teams if state[team["id"]]["metrics"][key]["coverage"] > 0)
        print(f"  {key}: {covered}/{len(teams)} takımda veri var")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
