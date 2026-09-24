"""GOAL API kabul testi: python scripts/check_goal_api.py

Anahtarı yalnızca yerel GOAL_API_KEY ortam değişkeninden veya depo dışındaki
anahtar dosyasından okur; Süper Lig ölçümünü docs/super-lig-api-kabul-sonucu.md
dosyasına yazar. Anahtar hiçbir zaman koda, rapora, komut çıktısına veya yayın
dosyasına yazılmaz. Anahtar yoksa ölçüm
yapılmaz; rapor "test bekliyor" durumuyla ve hazırlık adımlarıyla yazılır.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "docs" / "super-lig-api-kabul-sonucu.md"

KEY_ENV = "GOAL_API_KEY"
KEY_FILE = Path.home() / ".ligpuan" / "goal-api.key"
BASE_CANDIDATES = ("https://api.goal-api.com/v1", "https://goal-api.com/api/v1")
AUTH_STYLES = ("bearer", "x-api-key")
PAGE_LIMIT = 100
MIN_FINISHED = 10
FREE_DAILY_QUOTA = 1000
TIMEOUT = 30

FIELD_ALIASES = {
    "Şut": ("shots", "total_shots", "shots_total", "shot"),
    "İsabetli şut": ("shots_on_target", "shots_on_goal", "shotsontarget", "shots_target"),
    "Korner": ("corners", "corner_kicks", "corner"),
    "Topa sahip olma": ("possession", "ball_possession", "possession_percentage"),
    "Pas": ("passes", "total_passes", "accurate_passes", "passes_total"),
    "Faul": ("fouls", "foul"),
    "Ofsayt": ("offsides", "offside"),
    "Kurtarış": ("saves", "goalkeeper_saves", "saves_total"),
    "Sarı kart": ("yellow_cards", "yellowcards", "yellow_card"),
    "Kırmızı kart": ("red_cards", "redcards", "red_card"),
}

THRESHOLDS = {
    "Şut": 90,
    "Sarı kart": 90,
    "Kırmızı kart": 90,
    "Korner": 90,
    "Pas": 80,
    "Topa sahip olma": 80,
    "İsabetli şut": None,
    "Faul": None,
    "Ofsayt": None,
    "Kurtarış": None,
}


class Client:
    def __init__(self, key):
        self.key = key
        self.base = None
        self.auth = None
        self.calls = 0
        self.failures = []

    def _headers(self):
        headers = {"Accept": "application/json", "User-Agent": "LigPuan-kabul-testi"}
        if self.auth == "bearer":
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            headers["X-API-Key"] = self.key
        return headers

    def get(self, path, **params):
        if self.base is None:
            return None, "taban adres seçilmedi"
        query = f"?{urlencode(params)}" if params else ""
        request = Request(f"{self.base}{path}{query}", headers=self._headers())
        self.calls += 1
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                body = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(body), None
                except json.JSONDecodeError:
                    return None, "yanıt JSON değil"
        except HTTPError as error:
            self.failures.append(f"HTTP {error.code} ({path})")
            return None, f"HTTP {error.code}"
        except URLError as error:
            self.failures.append(f"ağ hatası ({path}): {error.reason}")
            return None, f"ağ hatası: {error.reason}"
        except TimeoutError:
            self.failures.append(f"zaman aşımı ({path})")
            return None, "zaman aşımı"

    def discover(self):
        attempts = []
        for base in BASE_CANDIDATES:
            for auth in AUTH_STYLES:
                self.base, self.auth = base, auth
                data, error = self.get("/leagues", limit=1)
                if data is not None:
                    attempts.append(f"{base} + {auth}: başarılı")
                    return attempts
                attempts.append(f"{base} + {auth}: {error}")
        self.base = None
        return attempts


def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "leagues", "fixtures", "teams", "standings", "statistics"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return as_list(value)
        return [payload]
    return []


def statistic_values(payload):
    """Return observed values by field, including GOAL API's type/home/away rows."""
    found = {field: [] for field in FIELD_ALIASES}

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for field, aliases in FIELD_ALIASES.items():
                label = re.sub(r"[^a-z0-9]+", "_", str(node.get("type", "")).lower()).strip("_")
                if label in aliases and all(node.get(side) not in (None, "") for side in ("home", "away")):
                    found[field].extend((node["home"], node["away"]))
                for alias in aliases:
                    if alias in node and node[alias] not in (None, "", [], {}):
                        found[field].append(node[alias])
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)

    visit(payload)
    return found


def normalized(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def classify_card_text(text):
    word = str(text).casefold()
    if "red" in word:
        return "Kırmızı kart"
    if "yellow" in word:
        return "Sarı kart"
    return None


def card_observations(payload):
    """Kart alanlarını ve kart olaylarını gezer; (alan, adet, yol, ev-deplasman) dörtlüleri döndürür.

    Aynı nesnede hem etiket hem sayı alanı varsa yalnızca etiket üzerinden tek
    kez sayılır; ikisi de yazılmaz.
    """
    found = []

    def visit(node, path):
        if isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{path}[{index}]")
            return
        if not isinstance(node, dict):
            return
        label = " ".join(str(node.get(key, "")) for key in ("type", "detail", "kind", "name", "card", "cardType"))
        kind = classify_card_text(label) if "card" in label.casefold() else None
        if kind:
            amount = None
            split = None
            sides = [node.get(side) for side in ("home", "away")]
            if all(isinstance(side, (int, float)) and not isinstance(side, bool) for side in sides):
                amount = sides[0] + sides[1]
                split = (sides[0], sides[1])
            if amount is None:
                for key in ("count", "total", "value", "quantity"):
                    if isinstance(node.get(key), (int, float)) and not isinstance(node.get(key), bool):
                        amount = node[key]
                        break
            if amount is None:
                amount = 1
            found.append((kind, amount, f"{path} [{label.strip()}]", split))
        else:
            context = f"{label} {path}"
            for key, value in node.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    leaf = normalized(key)
                    if "card" in leaf or (leaf in ("red", "yellow") and "card" in context.casefold()):
                        leaf_kind = classify_card_text(leaf)
                        if leaf_kind:
                            found.append((leaf_kind, value, f"{path}.{key}", None))
                elif isinstance(value, (dict, list)):
                    visit(value, f"{path}.{key}")
                    return
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                visit(value, f"{path}.{key}")

    visit(payload, "$")
    return found


def card_totals(observations):
    totals = {"Kırmızı kart": 0, "Sarı kart": 0}
    for kind, amount, _path, _split in observations:
        totals[kind] += amount
    return totals


def fetch_card_source(client, match_id, notes):
    payload, error = client.get(f"/fixtures/{match_id}/cards")
    if payload is not None:
        return "cards", payload, None
    payload_events, error_events = client.get(f"/fixtures/{match_id}/events")
    if payload_events is not None:
        return "events", payload_events, None
    reason = f"cards: {error}, events: {error_events}"
    notes.append(f"maç {match_id}: kart kaynağı yanıt vermedi ({reason})")
    return None, None, reason


def resolve_cards(client, match_id, statistics_payload, notes):
    """Kırmızı kart alanı istatistikte yoksa yedek kaynağı dener; kaynağı ve durumu döndürür."""
    statistics_observations = card_observations(statistics_payload)
    statistics_red = any(kind == "Kırmızı kart" for kind, _amount, _path, _split in statistics_observations)
    if statistics_red:
        red_splits = [
            split for kind, _amount, _path, split in statistics_observations
            if kind == "Kırmızı kart" and split is not None
        ]
        return {
            "source": "istatistik",
            "observations": statistics_observations,
            "totals": card_totals(statistics_observations),
            "split": red_splits[0] if red_splits else None,
            "red_present": True,
            "note": "kırmızı kart istatistik yanıtında bulundu",
            "reason": None,
        }
    source, payload, reason = fetch_card_source(client, match_id, notes)
    if payload is None:
        return {
            "source": "yok",
            "observations": statistics_observations,
            "totals": card_totals(statistics_observations),
            "red_present": False,
            "note": "istatistikte kırmızı kart alanı yok; yedek kaynak yanıt vermedi",
            "reason": reason,
        }
    fallback_observations = card_observations(payload)
    observations = statistics_observations + fallback_observations
    statistics_totals = card_totals(statistics_observations)
    fallback_totals = card_totals(fallback_observations)
    totals = {}
    for kind in ("Kırmızı kart", "Sarı kart"):
        if any(kind == observed_kind for observed_kind, _amount, _path, _split in statistics_observations):
            totals[kind] = statistics_totals[kind]
        else:
            totals[kind] = fallback_totals[kind]
    red_splits = [
        split for observed_kind, _amount, _path, split in observations
        if observed_kind == "Kırmızı kart" and split is not None
    ]
    return {
        "source": source,
        "observations": observations,
        "totals": totals,
        "split": red_splits[0] if red_splits else None,
        "red_present": True,
        "note": f"kırmızı kart {source} kaynağından; bu maçta gözlenen kırmızı kart: {totals['Kırmızı kart']}",
        "reason": None,
    }


def team_ids_of(rows):
    result = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        nested = row.get("team") if isinstance(row.get("team"), dict) else None
        if nested is not None and any(nested.get(key) is not None for key in ("id", "teamId", "team_id")):
            target = nested
        else:
            target = row
        for key in ("id", "teamId", "team_id"):
            if target.get(key) is not None:
                result.add(str(target[key]))
                break
    return result


def fixture_team_ids(fixtures):
    result = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        for key in ("homeTeamId", "home_team_id", "awayTeamId", "away_team_id"):
            if fixture.get(key) is not None:
                result.add(str(fixture[key]))
        for side in ("home", "homeTeam", "away", "awayTeam"):
            node = fixture.get(side)
            if isinstance(node, dict) and node.get("id") is not None:
                result.add(str(node["id"]))
    return result


def find_league(client, notes):
    leagues = collect_pages(client, "/leagues", "leagues", notes)
    for league in leagues:
        if not isinstance(league, dict):
            continue
        name = str(league.get("name") or league.get("leagueName") or "").strip().casefold()
        country = str(league.get("country") or league.get("countryName") or "").casefold()
        if re.fullmatch(r"(?:trendyol\s+)?(?:süper|super)\s+lig", name) and (
            "turkey" in country or "türkiye" in country or "turkiye" in country or country == "tr"
        ):
            return league
    notes.append("Türkiye Süper Lig, lig listesinde ülke ve tam ad birlikte doğrulanarak bulunamadı")
    return None


def collect_pages(client, path, key, notes, **params):
    items = []
    offset = 0
    while True:
        data, error = client.get(path, limit=PAGE_LIMIT, offset=offset, **params)
        if data is None:
            notes.append(f"{path} alınamadı: {error}")
            break
        page = as_list(data)
        if not page:
            break
        items.extend(page)
        pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
        if isinstance(pagination, dict) and "hasMore" in pagination:
            if not pagination["hasMore"]:
                break
        elif len(page) < PAGE_LIMIT:
            break
        offset += len(page)
        if offset > 5000:
            notes.append(f"{path}: sayfalama sınırına ulaşıldı")
            break
    return items


def has_finished_status(fixture):
    status = fixture.get("status") or fixture.get("matchStatus") or fixture.get("match_status")
    if isinstance(status, dict):
        status = status.get("short") or status.get("name")
    period = fixture.get("clock", {}).get("period") if isinstance(fixture.get("clock"), dict) else None
    return str(status or period or "").upper() in ("FINISHED", "FT", "ENDED", "COMPLETED") or fixture.get("finished") is True


def in_season(fixture):
    date = fixture.get("matchDate") or fixture.get("kickoffUtc") or fixture.get("date")
    if not isinstance(date, str) or len(date) < 10:
        return False
    day = date[:10]
    return "2026-07-01" <= day <= "2027-06-30"


def fixture_id(fixture):
    for key in ("id", "fixtureId", "matchId"):
        if fixture.get(key) is not None:
            return fixture[key]
    return None


def load_key():
    """Anahtarı yalnızca ortam değişkeninden ya da depo dışındaki anahtar dosyasından okur."""
    value = os.environ.get(KEY_ENV)
    if value and value.strip():
        return value.strip(), f"{KEY_ENV} ortam değişkeni"
    try:
        if KEY_FILE.is_file():
            value = KEY_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value, f"depo dışı anahtar dosyası ({KEY_FILE})"
    except OSError:
        return None, None
    return None, None


def report_has_measurement():
    if not REPORT.is_file():
        return False
    text = REPORT.read_text(encoding="utf-8", errors="replace")
    return "ölçüm yapıldı" in text


def main():
    key, key_source = load_key()
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not key:
        if report_has_measurement():
            print(f"{KEY_ENV} tanımlı değil ve depo dışı anahtar dosyası yok. Ölçüm yapılmadı.")
            print("Raporda daha önce yapılmış bir ölçüm var; üzerine yazılmadı.")
            return 2
        write_waiting_report(stamp)
        print("GOAL_API_KEY tanımlı değil. Ölçüm yapılmadı.")
        print(f"Hazırlık: {KEY_ENV} ortam değişkenine anahtarı ekleyin veya depo dışında bir anahtar dosyası oluşturun: {KEY_FILE}")
        print(f"Rapor 'test bekliyor' durumuyla yazıldı: {REPORT.relative_to(ROOT)}")
        return 2

    client = Client(key)
    notes = []
    discovery = client.discover()
    if client.base is None:
        write_failed_report(stamp, discovery, notes)
        print("Hiçbir taban adres ve kimlik doğrulama biçimi çalışmadı. Rapor ayrıntıyı içeriyor.")
        return 1

    league = find_league(client, notes)
    league_id = None
    for key_name in ("id", "leagueId"):
        if isinstance(league, dict) and league.get(key_name) is not None:
            league_id = league[key_name]
    if league_id is None:
        notes.append("lig kimliği bulunamadı; takım, puan tablosu ve fikstür ölçülemedi")
        teams, teams_season, standings, fixtures = [], [], [], []
    else:
        teams = collect_pages(client, f"/leagues/{league_id}/teams", "teams", notes)
        teams_season = collect_pages(client, f"/leagues/{league_id}/teams", "teams", notes, season="2026")
        standings = collect_pages(client, f"/standings/{league_id}", "standings", notes)
        fixtures = collect_pages(client, f"/leagues/{league_id}/fixtures", "fixtures", notes, season="2026")

    in_season_fixtures = [fixture for fixture in fixtures if in_season(fixture)]
    if len(in_season_fixtures) != len(fixtures):
        notes.append(f"2026/27 dışında veya tarihi okunamayan {len(fixtures) - len(in_season_fixtures)} fikstür örneklemden çıkarıldı")
    fixtures = in_season_fixtures
    finished = [fixture for fixture in fixtures if has_finished_status(fixture)]
    sample = finished[: max(MIN_FINISHED, 10)]

    stats_notes = []
    counts = {field: 0 for field in FIELD_ALIASES}
    contradictions = []
    missing_examples = []
    evidence = {field: [] for field in FIELD_ALIASES}
    card_sources = {}
    card_source_by_match = {}
    card_totals_by_match = {"Kırmızı kart": 0, "Sarı kart": 0}
    for fixture in sample:
        match_id = fixture_id(fixture)
        if match_id is None:
            continue
        payload, error = client.get(f"/fixtures/{match_id}/statistics")
        if payload is None:
            stats_notes.append(f"maç {match_id}: {error}")
            continue
        values = statistic_values(payload)
        resolution = resolve_cards(client, match_id, payload, stats_notes)
        card_sources[resolution["source"]] = card_sources.get(resolution["source"], 0) + 1
        card_source_by_match[match_id] = resolution
        observations = resolution["observations"]
        observed = resolution["totals"]
        for kind in card_totals_by_match:
            card_totals_by_match[kind] += observed[kind]
        card_present = {
            "Kırmızı kart": resolution["red_present"],
            "Sarı kart": any(kind == "Sarı kart" for kind, _amount, _path, _split in observations),
        }
        for field in FIELD_ALIASES:
            paths = [path for kind, _amount, path, _split in observations if kind == field]
            present = card_present[field] if field in card_present else bool(values[field])
            if present:
                counts[field] += 1
                if paths and len(evidence[field]) < 3:
                    evidence[field].append(f"maç {match_id} · {resolution['source']}: {paths[0]}")
            elif len(missing_examples) < 5:
                missing_examples.append(f"maç {match_id}: {field} alanı yok")
        shots = [value for value in values["Şut"] if isinstance(value, (int, float))]
        on_target = [value for value in values["İsabetli şut"] if isinstance(value, (int, float))]
        if shots and on_target and max(on_target) > max(shots):
            contradictions.append(f"maç {match_id}: isabetli şut ({max(on_target)}) > toplam şut ({max(shots)})")
        possession = [value for value in values["Topa sahip olma"] if isinstance(value, (int, float))]
        if possession and len(possession) >= 2 and abs(sum(possession[:2]) - 100) > 5:
            contradictions.append(f"maç {match_id}: topa sahip olma toplamı {sum(possession[:2])}")

    standings_teams = team_ids_of(standings)
    league_teams = team_ids_of(teams)
    league_teams_season = team_ids_of(teams_season) if teams_season else set()
    playing_teams = fixture_team_ids(fixtures)
    season_teams = standings_teams or playing_teams
    team_sets = {
        "lig uç noktası (tüm sezonlar)": len(league_teams),
        "lig uç noktası (season=2026)": len(league_teams_season),
        "puan tablosu satırları": len(standings_teams),
        "fikstürde oynayan takımlar": len(playing_teams),
    }
    extra_teams = sorted(league_teams - season_teams)
    missing_teams = sorted(season_teams - league_teams)

    inspected = len(sample)
    rows = []
    for field, threshold in THRESHOLDS.items():
        rate = round(100 * counts[field] / inspected, 1) if inspected else 0.0
        if inspected < MIN_FINISHED:
            verdict = "ölçülemedi"
        elif threshold is None:
            verdict = "rapor"
        elif rate >= threshold:
            verdict = "geçti"
        else:
            verdict = "kaldı"
        rows.append((field, counts[field], inspected, rate, threshold, verdict))

    daily_calls = 3 + len(finished[:20]) * 2
    write_measured_report(
        stamp,
        discovery,
        notes + stats_notes,
        league,
        league_id,
        teams,
        standings,
        fixtures,
        finished,
        inspected,
        rows,
        missing_examples,
        contradictions,
        client,
        daily_calls,
        evidence=evidence,
        card_sources=card_sources,
        card_totals_by_match=card_totals_by_match,
        card_source_by_match=card_source_by_match,
        team_sets=team_sets,
        extra_teams=extra_teams,
        missing_teams=missing_teams,
    )
    print(f"{'Ölçüm tamamlandı' if inspected >= MIN_FINISHED else 'Ölçüm yetersiz'}: {client.calls} çağrı, {len(finished)} bitmiş maç, {inspected} maç incelendi.")
    print(f"Rapor: {REPORT.relative_to(ROOT)}")
    return 0 if inspected >= MIN_FINISHED else 1


def table(rows):
    lines = ["| Alan | Veri gelen maç / incelenen maç | Doluluk | Eşik | Sonuç |", "|---|---|---|---|---|"]
    for field, found, inspected, rate, threshold, verdict in rows:
        threshold_text = "—" if threshold is None else f"%{threshold}"
        lines.append(f"| {field} | {found} / {inspected} | {'%' + str(rate) if inspected else '—'} | {threshold_text} | {verdict} |")
    return lines


def write_waiting_report(stamp):
    lines = [
        "# GOAL API kabul testi sonucu",
        "",
        f"Durum: **test bekliyor** — gerçek anahtarla ölçüm yapılmadı.",
        f"Rapor tarihi: {stamp}",
        "Anahtar kaynağı: `GOAL_API_KEY` ortam değişkeni veya depo dışındaki anahtar dosyası. Anahtar bu rapora yazılmaz.",
        "",
        "## 1. Neden bekliyor",
        "",
        f"`{KEY_ENV}` ortam değişkeni tanımlı değildi. Bu yüzden sağlayıcıya hiç istek yapılmadı; hiçbir sayı üretilmedi ve sağlayıcı kabul edilmiş sayılmaz.",
        "",
        "## 2. Hazırlık adımları",
        "",
        f"1. goal-api.com üzerinden ücretsiz hesap aç ve bir API anahtarı üret (ücretsiz plan: günde {FREE_DAILY_QUOTA} istek, tek anahtar, kredi kartı istenmez).",
        f"2. Anahtarı yalnızca yerel ortam değişkenine koy: `$env:{KEY_ENV} = \"...\"` (PowerShell) veya `export {KEY_ENV}=...` (bash). Anahtarı depoya, rapora veya komut geçmişine yazma.",
        "3. Ölçümü çalıştır: `python scripts/check_goal_api.py`",
        "4. Rapor bu dosyaya ölçüm sonucuyla yeniden yazılır; sonucu görev kaydına işle.",
        "",
        "## 3. Ölçülecek maddeler (betikte hazır)",
        "",
        "1. 2026/27 Türkiye Süper Lig erişimi, lig kimliği ve takım sayısı.",
        "2. Puan tablosu ve sezon fikstürünün alınabilirliği (sayfalama ile tüm kayıtlar).",
        f"3. En az {MIN_FINISHED} bitmiş maçta şut, isabetli şut, kart, korner, topa sahip olma, pas, faul, ofsayt ve kurtarış doluluğu; her alan için veri gelen maç / incelenen maç.",
        "4. Toplam çağrı sayısı, hata yanıtları ve ücretsiz kotaya göre günlük ihtiyaç.",
        "5. Eksik veya çelişkili alan örnekleri (eksik veri sıfır sayılmaz).",
        "",
        "## 4. Eşikler",
        "",
        "| Alan grubu | Eşik |",
        "|---|---|",
        "| Şut, kart, korner | %90 |",
        "| Pas, topa sahip olma | %80 |",
        "| Diğer alanlar | ayrı raporlanır |",
        "",
        "## 5. Resmî belgelerde görülen çelişki (ölçümde doğrulanacak)",
        "",
        "| Konu | `/documentation` ve `what-is-goal-api` | `/coverage` örneği ve `llms.txt` |",
        "|---|---|---|",
        "| Taban adres | `https://api.goal-api.com/v1` | `https://goal-api.com/api/v1` |",
        "| Kimlik doğrulama | `Authorization: Bearer <anahtar>` | `X-API-Key: <anahtar>` |",
        "",
        "Betik iki taban adresi ve iki başlık biçimini de dener, çalışanı rapora yazar. Çalışan biçim doğrulanmadan sağlayıcı kabul edilmez.",
        "",
        "## 6. Bilinen kapsam bilgisi (sağlayıcının kendi sayfasından)",
        "",
        "- Sağlayıcının Süper Lig kapsamı ve 2026/27 erişimi gerçek anahtarla ölçülür; önceki 1. Lig ölçümü bu lig için kanıt değildir.",
        "- 2026/27 sezonunun ücretsiz planda açık olup olmadığı yalnızca gerçek anahtarla doğrulanabilir. Bu yüzden bu rapor sağlayıcıyı kabul etmiş saymaz.",
        "",
        "## 7. Sınırlar",
        "",
        "- Ağ isteği yapılmadı; `data/site.json`, `index.html` ve örnek maç dosyaları değiştirilmedi.",
        f"- Ölçüm yalnızca `python scripts/check_goal_api.py` komutuyla ve yerel anahtarla yapılır.",
        "",
    ]
    write(lines)


def write_failed_report(stamp, discovery, notes):
    lines = [
        "# GOAL API kabul testi sonucu",
        "",
        "Durum: **test bekliyor** — anahtar bulundu, ancak hiçbir taban adres/kimlik doğrulama biçimi çalışmadı.",
        f"Rapor tarihi: {stamp}",
        "Anahtar kaynağı: `GOAL_API_KEY` ortam değişkeni veya depo dışındaki anahtar dosyası. Anahtar bu rapora yazılmaz.",
        "",
        "## Denenen biçimler",
        "",
    ]
    lines += [f"- {item}" for item in discovery]
    lines += ["", "## Notlar", ""]
    lines += [f"- {item}" for item in notes] or ["- yok"]
    lines += [
        "",
        "## Sonuç",
        "",
        "Sağlayıcı kabul edilmedi. Tablo, fikstür ve istatistik doluluğu ölçülemedi. Sağlayıcı ekibiyle iletişime geçip erişim biçimini doğrulayın.",
        "",
    ]
    write(lines)


def write_measured_report(
    stamp,
    discovery,
    notes,
    league,
    league_id,
    teams,
    standings,
    fixtures,
    finished,
    inspected,
    rows,
    missing_examples,
    contradictions,
    client,
    daily_calls,
    evidence=None,
    card_sources=None,
    card_totals_by_match=None,
    card_source_by_match=None,
    team_sets=None,
    extra_teams=None,
    missing_teams=None,
):
    evidence = evidence or {}
    card_sources = card_sources or {}
    card_totals_by_match = card_totals_by_match or {}
    card_source_by_match = card_source_by_match or {}
    team_sets = team_sets or {}
    extra_teams = extra_teams or []
    missing_teams = missing_teams or []
    league_name = ""
    if isinstance(league, dict):
        league_name = str(league.get("name") or league.get("leagueName") or "")
    lines = [
        "# GOAL API kabul testi sonucu",
        "",
        f"Durum: {'ölçüm yapıldı' if inspected >= MIN_FINISHED else 'test geçersiz — yeterli bitmiş maç örneklemi yok'} (rapor tarihi: {stamp}). Kabul kararı eşiklere göre aşağıda.",
        "Anahtar kaynağı: `GOAL_API_KEY` ortam değişkeni veya depo dışındaki anahtar dosyası. Anahtar bu rapora yazılmaz.",
        "",
        "## 1. Erişim",
        "",
        f"- Çalışan taban adres ve kimlik doğrulama: {client.base} + {client.auth}",
        f"- Lig: {league_name or 'bilinmiyor'} (kimlik: {league_id})",
        f"- Puan tablosu satırı: {len(standings)}",
        f"- Fikstür kaydı: {len(fixtures)} (bitmiş: {len(finished)})",
        "",
        "## 2. Takım sayısı: kaynaklar arası fark",
        "",
    ]
    lines += [f"- {name}: {count}" for name, count in team_sets.items()] or ["- takım kümesi ölçülemedi"]
    lines += [
        "",
        f"Sezon takım kümesi olarak puan tablosu satırları (yoksa fikstürde oynayan takımlar) esas alındı: {team_sets.get('puan tablosu satırları', 0)} kayıt.",
        "Lig uç noktası sezon filtresi olmadan geçmiş sezonların takımlarını da döndürdüğü için tek başına sezon kümesi sayılmaz.",
    ]
    if extra_teams:
        lines.append(f"- Yalnızca lig uç noktasında olan (geçmiş sezon) takım kimlikleri: {', '.join(extra_teams[:8])}{' …' if len(extra_teams) > 8 else ''}")
    if missing_teams:
        lines.append(f"- Sezon kümesinde olup lig uç noktasında bulunmayan takım kimlikleri: {', '.join(missing_teams[:8])}{' …' if len(missing_teams) > 8 else ''}")
    lines += [
        "",
        "## 3. Alan doluluğu",
        "",
    ]
    lines += table(rows)
    lines += [
        "",
        f"İncelenen maç sayısı: {inspected}. Eksik veri sıfır kabul edilmedi.",
        "",
        "## 4. Kart alanı nereden geldi",
        "",
    ]
    lines += [f"- Kaynak kullanımı — {name}: {count} maç" for name, count in card_sources.items()] or ["- kart kaynağı ölçülemedi"]
    if card_totals_by_match:
        lines.append(
            "- Örneklemde gözlenen kart toplamı: "
            + ", ".join(f"{kind} {value}" for kind, value in card_totals_by_match.items())
        )
    for field in ("Kırmızı kart", "Sarı kart"):
        for item in evidence.get(field, []):
            lines.append(f"- {field} alan yolu — {item}")
    lines += [
        "",
        "Bir alanın hiç bulunmaması ile alanın bulunup değerin sıfır olması ayrı sayılır: ilk durumda alan 'yok' yazılır, ikinci durumda alan 'var' sayılır ve değer 0 olarak raporlanır.",
        "",
        "## 5. Maç bazında kırmızı kart kaynağı",
        "",
        "| Maç | Kırmızı kart kaynağı | Bu maçtaki kırmızı kart | Dağılım (ev–deplasman) | Durum |",
        "|---|---|---|---|---|",
    ]
    if card_source_by_match:
        for match_id, resolution in card_source_by_match.items():
            red_total = resolution["totals"]["Kırmızı kart"] if resolution["source"] != "yok" else "bilinmiyor"
            split = resolution.get("split")
            split_text = f"{split[0]} – {split[1]}" if split else "—"
            status = resolution["note"]
            if resolution.get("reason"):
                status = f"{status} · neden: {resolution['reason']}"
            lines.append(f"| {match_id} | {resolution['source']} | {red_total} | {split_text} | {status} |")
    else:
        lines.append("| — | — | — | — | maç bazında kart kaynağı ölçülemedi |")
    lines += [
        "",
        "## 6. Çağrılar, hatalar ve kota",
        "",
        f"- Bu ölçümde yapılan çağrı: {client.calls}",
        f"- Hata sayısı: {len(client.failures)}",
        f"- Ücretsiz kota: günde {FREE_DAILY_QUOTA} istek",
        f"- Günlük tahmini ihtiyaç (günde en çok 20 bitmiş maç varsayımıyla): ~{daily_calls} istek",
        "",
        "## 7. Eksik veya çelişkili alan örnekleri",
        "",
    ]
    lines += [f"- {item}" for item in missing_examples] or ["- eksik alan örneği yok"]
    lines += [f"- {item}" for item in contradictions] or ["- çelişki örneği yok"]
    lines += ["", "## 8. Notlar", ""]
    lines += [f"- {item}" for item in notes if item] or ["- yok"]
    lines += [""]
    write(lines)


def write(lines):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
