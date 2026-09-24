"""LP-05 ana veri güncelleyici: python scripts/fetch_goal_data.py

GOAL API'den 2026/27 Türkiye Süper Lig verisini çeker, yerel maç modeline
çevirir ve doğrulama geçerse yayınlar. Kaynak maç dosyası ayrı tutulur
(data/matches/goal-superlig-2026-27.json); eski lig dosyasına dokunulmaz.
data/site.json aynı doğrulama ve toplama hattıyla (update_data) üretilir.

Kurallar:
- Anahtar yalnızca GOAL_API_KEY ortam değişkeninden ya da depo dışındaki
  anahtar dosyasından okunur; loga, rapora veya veriye yazılmaz.
- Anahtar yoksa gerçek veri çekilmiş gibi davranılmaz (çıkış kodu 2).
- Her istekte zaman aşımı vardır; geçici hatalarda en fazla üç deneme
  yapılır. Takım kimlikleri yalnızca team-map.json'daki açık eşlemeyle
  doğrulanır; ada bakıp eşleştirme yapılmaz. Eşleme boşsa ilk anahtarlı
  çalışma puan durumu ve fikstürü çekip tüm kimlikleri ad ipuçlarıyla
  listeler; hiçbir yayın dosyası değişmez.
- Bitmiş maçlar yalnızca son RECHECK_DAYS günde yeniden çekilir; daha
  eski kayıtlar temel alanları eksik olmadıkça olduğu gibi korunur.
  Temel alan eksikse yaşa bakılmaksızın yeniden çekilir. Tekillik anahtarı
  sağlayıcı + maç kimliğidir; aynı maç ikinci kez toplamlara eklenmez.
- "Alan yok" ile "veri var, değer 0" ayrı tutulur: bulunmayan alan
  yazılmaz, bulunan sıfır 0 olarak yazılır. Çelişen aynı alan atlanır.
- Kırmızı kart, ev/deplasman dağılımı güvenilir biçimde eşlenebiliyorsa
  takımlara yazılır; dağılım yoksa takım değeri üretilmez, durum
  günlük özetine yazılır.
- Doğrulama, ağ veya tutarlılık hatasında yayındaki data/site.json
  değişmez (çıkış kodu 1).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_goal_api as probe
import update_data

ROOT = Path(__file__).resolve().parent.parent
TEAM_MAP = ROOT / "data" / "team-map.json"
GOAL_FILE = ROOT / "data" / "matches" / "goal-superlig-2026-27.json"
PROVIDER = "goal_api"
TIMEOUT = 20
TRIES = 3
RECHECK_DAYS = 3

# LP-04'te ölçülen ve takıma yazılabilen alanlar (ölçü adı -> yerel anahtar).
ROW_FIELDS = (
    ("Şut", "shots"),
    ("İsabetli şut", "shots_on_target"),
    ("Korner", "corners"),
    ("Topa sahip olma", "possession"),
    ("Pas", "passes"),
    ("İsabetli pas", "accurate_passes"),
    ("Faul", "fouls"),
    ("Ofsayt", "offsides"),
    ("Kurtarış", "saves"),
    ("Sarı kart", "yellow_cards"),
    ("Kırmızı kart", "red_cards"),
)

# Yayın doğrulamasının %80 eşiğini dayattığı temel alanlar. Bunlar eksikken
# kaydın yaşı ne olursa olsun yeniden çekilir; doluysa yaş sınırı uygulanır.
CORE_FIELDS = ("shots", "passes", "corners")

STAT_LABELS = {
    alias: local
    for label, local in ROW_FIELDS
    if label in probe.FIELD_ALIASES
    for alias in probe.FIELD_ALIASES[label]
    if alias != "accurate_passes"
}
STAT_LABELS.update({"accurate_passes": "accurate_passes", "passes_accurate": "accurate_passes"})

SCORE_PAIRS = (
    ("homeTeamScore", "awayTeamScore"),
    ("homeGoals", "awayGoals"),
    ("home_goals", "away_goals"),
    ("homeScore", "awayScore"),
    ("home_score", "away_score"),
)
HALF_TIME_SCORE_PAIRS = (("homeTeamHalftimeScore", "awayTeamHalftimeScore"),)
SCORE_OBJECTS = ("score", "goals", "fullTime", "full_time", "result")
ROUND_KEYS = ("matchRound", "round", "roundNumber", "round_number", "week", "matchday", "match_day", "tour")
DATE_KEYS = ("matchDate", "kickoffUtc", "date", "kickoff", "match_date")


class FetchError(Exception):
    pass


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise FetchError(f"bozuk JSON: {path} ({error})") from error


class ApiClient:
    """Zaman aşımlı, en fazla üç denemeli istemci. Anahtarı başlıkta taşır."""

    def __init__(self, key, timeout=TIMEOUT, tries=TRIES, sleep=time.sleep, transport=None):
        self.key = key
        self.base = None
        self.auth = None
        self.timeout = timeout
        self.tries = tries
        self.sleep = sleep
        self.transport = transport or self._url_transport
        self.calls = 0
        self.failures = []
        self.last_headers = {}

    def _headers(self):
        headers = {"Accept": "application/json", "User-Agent": "LigPuan-veri-guncelleyici"}
        if self.auth == "bearer":
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            headers["X-API-Key"] = self.key
        return headers

    def _url_transport(self, request):
        # Kota başlıkları gövdede değil yanıt başlığında gelir; hata yanıtında da
        # okunur, böylece 429 sonrası kalan kotayı görmek mümkün olur.
        try:
            with urlopen(request, timeout=self.timeout) as response:
                self.last_headers = dict(response.headers)
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            self.last_headers = dict(error.headers or {})
            raise

    def get(self, path, tries=None, **params):
        query = f"?{urlencode(params)}" if params else ""
        request = Request(f"{self.base}{path}{query}", headers=self._headers())
        attempts = tries if tries is not None else self.tries
        last = "istek yapılamadı"
        for attempt in range(1, attempts + 1):
            self.calls += 1
            try:
                body = self.transport(request)
                try:
                    return json.loads(body), None
                except json.JSONDecodeError:
                    last = "yanıt JSON değil"
                    break
            except HTTPError as error:
                if error.code == 429:
                    payload = error.read().decode("utf-8", errors="replace")
                    if "RATE_LIMIT_EXCEEDED" in payload:
                        # Günlük kota doldu. Denemek işe yaramaz; kota
                        # dönene kadar beklemek dışarıda bırakılır.
                        self.failures.append(f"{path}: günlük kota doldu")
                        return None, "günlük kota doldu (HTTP 429)"
                    last = "HTTP 429"
                elif 500 <= error.code < 600:
                    last = f"HTTP {error.code}"
                else:
                    self.failures.append(f"{path}: HTTP {error.code}")
                    return None, f"HTTP {error.code}"
            except (URLError, TimeoutError, OSError) as error:
                reason = getattr(error, "reason", error)
                last = "zaman aşımı" if isinstance(error, TimeoutError) else f"ağ hatası: {reason}"
            if attempt < attempts:
                self.sleep(attempt)
        self.failures.append(f"{path}: {last}")
        return None, last


def discover(client):
    attempts = []
    for base in probe.BASE_CANDIDATES:
        for auth in probe.AUTH_STYLES:
            client.base, client.auth = base, auth
            data, error = client.get("/leagues", tries=1, limit=1)
            if data is not None:
                attempts.append(f"{base} + {auth}: başarılı")
                return attempts
            attempts.append(f"{base} + {auth}: {error}")
    client.base = None
    return attempts


def build_team_lookup():
    payload = read_json(TEAM_MAP)
    if not payload or not isinstance(payload.get("teams"), list):
        raise FetchError(f"takım eşleme dosyası okunamadı: {TEAM_MAP}")
    lookup = {}
    for team in payload["teams"]:
        provider_id = (team.get("provider_ids") or {}).get(PROVIDER)
        if provider_id is None:
            continue
        provider_id = str(provider_id)
        if provider_id in lookup:
            raise FetchError(f"sağlayıcı kimliği iki yerel takıma bağlı: {provider_id}")
        lookup[provider_id] = team["id"]
    return payload.get("season"), payload.get("league"), lookup


def extract_team_stats(payload, notes=None, fixture_id="?"):
    """Only full-time rows count; absent or conflicting fields stay absent."""
    rows = ((payload.get("data") or {}).get("match") or {}).get("fullTime")
    if not isinstance(rows, list):
        return {}, {}
    home, away, conflicts = {}, {}, set()

    def value(raw, percent):
        if isinstance(raw, bool):
            return None
        if isinstance(raw, str):
            raw = raw.strip().removesuffix("%") if percent else raw.strip()
        try:
            number = float(raw) if percent else int(raw)
        except (TypeError, ValueError):
            return None
        if number < 0 or (percent and number > 100) or (not percent and str(raw).strip() not in (str(number),)):
            return None
        return number if percent else int(number)

    for row in rows:
        if not isinstance(row, dict):
            continue
        field = STAT_LABELS.get(probe.normalized(row.get("type", "")))
        if field is None or field in conflicts:
            continue
        home_value, away_value = value(row.get("home"), field == "possession"), value(row.get("away"), field == "possession")
        if home_value is None or away_value is None:
            continue
        if field in home and (home[field], away[field]) != (home_value, away_value):
            home.pop(field)
            away.pop(field)
            conflicts.add(field)
            if notes is not None:
                notes.append(f"maç {fixture_id}: {field} tam maç yanıtında çelişkili; takıma yazılmadı")
            continue
        home[field], away[field] = home_value, away_value
    return home, away


def fixture_sides(fixture):
    for home_key, away_key in (("homeTeamId", "awayTeamId"), ("home_team_id", "away_team_id")):
        if fixture.get(home_key) is not None and fixture.get(away_key) is not None:
            return str(fixture[home_key]), str(fixture[away_key])
    home = fixture.get("home") or fixture.get("homeTeam")
    away = fixture.get("away") or fixture.get("awayTeam")
    if isinstance(home, dict) and isinstance(away, dict):
        if home.get("id") is not None and away.get("id") is not None:
            return str(home["id"]), str(away["id"])
    return None, None


def fixture_scores(fixture, pairs=SCORE_PAIRS, objects=SCORE_OBJECTS):
    def score(value):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        return None

    for home_key, away_key in pairs:
        home, away = score(fixture.get(home_key)), score(fixture.get(away_key))
        if home is not None and away is not None:
            return home, away
    for key in objects:
        node = fixture.get(key)
        if isinstance(node, dict):
            for home_key, away_key in (("home", "away"), ("homeTeam", "awayTeam")):
                home, away = score(node.get(home_key)), score(node.get(away_key))
                if home is not None and away is not None:
                    return home, away
    return None


def fixture_half_time_scores(fixture):
    return fixture_scores(fixture, HALF_TIME_SCORE_PAIRS, ())


def fixture_date(fixture):
    for key in DATE_KEYS:
        value = fixture.get(key)
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
    return None


def fixture_round(fixture):
    for key in ROUND_KEYS:
        value = fixture.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def check_contradictions(match_id, home, away):
    problems = []
    for side, values in (("ev", home), ("deplasman", away)):
        shots = values.get("shots")
        on_target = values.get("shots_on_target")
        if is_number(shots) and is_number(on_target) and on_target > shots:
            problems.append(f"maç {match_id}: isabetli şut ({on_target}) > toplam şut ({shots}) [{side}]")
        passes = values.get("passes")
        accurate = values.get("accurate_passes")
        if is_number(passes) and is_number(accurate) and accurate > passes:
            problems.append(f"maç {match_id}: isabetli pas ({accurate}) > toplam pas ({passes}) [{side}]")
    if is_number(home.get("possession")) and is_number(away.get("possession")):
        total = home["possession"] + away["possession"]
        if abs(total - 100) > 5:
            problems.append(f"maç {match_id}: topa sahip olma toplamı {total}")
    return problems


def fetch_match(client, fixture_id, home_id, away_id, notes):
    """Tek maçın istatistiğini yerel takım değerlerine çevirir."""
    payload, error = client.get(f"/fixtures/{fixture_id}/statistics")
    if payload is None:
        raise FetchError(f"maç {fixture_id}: istatistik alınamadı ({error})")
    home, away = extract_team_stats(payload, notes, fixture_id)
    red_note = None
    if "red_cards" not in home or "red_cards" not in away:
        resolution = probe.resolve_cards(client, fixture_id, payload, notes)
        split = resolution.get("split")
        if resolution["red_present"] and split is not None:
            home["red_cards"], away["red_cards"] = split[0], split[1]
            red_note = f"kırmızı kart {resolution['source']} kaynağından ev–deplasman dağılımıyla yazıldı"
        elif resolution["red_present"]:
            red_note = "kırmızı kart gözlendi ancak ev–deplasman dağılımı ölçülen yanıtta yok; takıma yazılmadı"
        else:
            reason = f" ({resolution['reason']})" if resolution.get("reason") else ""
            red_note = f"kırmızı kart alanı yok{reason}; takıma yazılmadı"
    if "yellow_cards" not in home and "yellow_cards" not in away:
        notes.append(f"maç {fixture_id}: sarı kart alanı istatistikte yok; takıma yazılmadı")
    problems = check_contradictions(fixture_id, home, away)
    if problems:
        raise FetchError("; ".join(problems))
    return home, away, red_note


def provider_names(standings, fixtures):
    """Sağlayıcı kimliklerine ad ipuçları toplar; yalnızca insana gösterilir, eşlemede kullanılmaz."""
    names = {}

    def take(pid, node):
        if pid not in names and isinstance(node, dict):
            for key in ("name", "teamName", "team_name", "shortName", "code"):
                if node.get(key):
                    names[pid] = str(node[key])
                    break

    for row in standings:
        if not isinstance(row, dict):
            continue
        nested = row.get("team") if isinstance(row.get("team"), dict) else None
        target = nested if nested is not None else row
        for key in ("id", "teamId", "team_id"):
            if target.get(key) is not None:
                take(str(target[key]), nested or row)
                break
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        for side in ("home", "homeTeam", "away", "awayTeam"):
            node = fixture.get(side)
            if isinstance(node, dict) and node.get("id") is not None:
                take(str(node["id"]), node)
    return names


def load_stored():
    payload = read_json(GOAL_FILE)
    if payload is None:
        return {}
    if payload.get("provider") != PROVIDER or not isinstance(payload.get("matches"), list):
        raise FetchError(f"sağlayıcı maç dosyası beklenen biçimde değil: {GOAL_FILE}")
    return {match["match_id"]: match for match in payload["matches"]}


def run(client, today=None):
    """Güncellemeyi çalıştırır; özet sözlüğü döndürür. Dosyalar burada yazılır."""
    today = today or date.today()
    cutoff = (today - timedelta(days=RECHECK_DAYS)).isoformat()
    notes = []
    discovery = discover(client)
    if client.base is None:
        raise FetchError("hiçbir taban adres ve kimlik doğrulama biçimi çalışmadı: " + "; ".join(discovery))

    critical_failures_before = len(client.failures)
    league = probe.find_league(client, notes)
    league_id = None
    for key_name in ("id", "leagueId"):
        if isinstance(league, dict) and league.get(key_name) is not None:
            league_id = league[key_name]
    if league_id is None:
        raise FetchError("lig kimliği bulunamadı; " + "; ".join(notes))

    standings = probe.collect_pages(client, f"/standings/{league_id}", "standings", notes)
    fixtures = probe.collect_pages(client, f"/leagues/{league_id}/fixtures", "fixtures", notes, season="2026")
    fixtures = [fixture for fixture in fixtures if isinstance(fixture, dict) and probe.in_season(fixture)]
    if len(client.failures) > critical_failures_before or not standings or not fixtures:
        raise FetchError("lig, puan durumu veya fikstür eksik alındı; son doğru yayın korundu")

    # Eşleme bilerek burada okunur: eşleme boş olsa bile ilk anahtarlı çalışma
    # puan durumu ve fikstürü çeker, ardından tüm kimlikleri listeler.
    season, league_name, lookup = build_team_lookup()
    provider_ids = set()
    for fixture in fixtures:
        home_pid, away_pid = fixture_sides(fixture)
        if home_pid is not None:
            provider_ids.add(home_pid)
        if away_pid is not None:
            provider_ids.add(away_pid)
    provider_ids |= probe.team_ids_of(standings)
    unknown = sorted(pid for pid in provider_ids if pid not in lookup)
    if unknown:
        names = provider_names(standings, fixtures)
        listed = ", ".join(f"{pid} ({names.get(pid, 'ad yok')})" for pid in unknown)
        raise FetchError(
            f"açık eşlemesi olmayan {len(unknown)} sağlayıcı takım var: {listed}. "
            "Eşleme team-map.json provider_ids.goal_api alanına elle yazılır; "
            "ada bakılarak otomatik eşleşme yapılmaz."
        )

    stored = load_stored()
    existing = sorted(update_data.MATCH_DIR.glob("*.json"))
    others = []
    if existing:
        others = [match for match in update_data.load_matches(season, league_name) if match.get("_source") != GOAL_FILE.name]
    to_fetch, skipped_old, skipped_unfinished, seen = [], 0, 0, set()
    for fixture in fixtures:
        raw_id = probe.fixture_id(fixture)
        if raw_id is None:
            raise FetchError("kimliksiz fikstür kaydı var; yarım yanıt yayınlanmaz")
        match_id = f"goal-{raw_id}"
        if match_id in seen:
            continue
        seen.add(match_id)
        if not probe.has_finished_status(fixture):
            skipped_unfinished += 1
            continue
        previous = stored.get(match_id)
        previous_stats = (previous or {}).get("stats") or {}
        core_complete = all(
            isinstance(values, dict) and all(field in values for field in CORE_FIELDS)
            for values in previous_stats.values()
        ) and len(previous_stats) == 2
        if previous is not None and previous.get("date", "") < cutoff and core_complete:
            skipped_old += 1
            continue
        to_fetch.append((match_id, fixture))

    foreign = [match for match in others if match.get("provider") != PROVIDER]
    if foreign and (stored or to_fetch):
        sources = sorted({str(match.get("_source", "?")) for match in foreign})
        raise FetchError(
            f"örnek veri ({len(foreign)} maç: {', '.join(sources)}) ile gerçek veri aynı yayında birleşemez. "
            "Güvenli geçiş: data/matches/2026-27.json dosyasını data/matches dışına taşı "
            "(öneri: data/arsiv/2026-27-ornek.json), team-map.json eşlemelerini doldur, "
            "komutu tekrar çalıştır. Geri alma: dosyayı geri taşıyıp update_data.py çalıştır."
        )

    fetched = []
    red_notes = []
    field_hits = {local: 0 for _label, local in ROW_FIELDS}
    for match_id, fixture in to_fetch:
        home_pid, away_pid = fixture_sides(fixture)
        if home_pid is None or away_pid is None:
            raise FetchError(f"maç {match_id}: ev/deplasman takımı okunamadı; yarım kayıt yayınlanmaz")
        home_id, away_id = lookup[home_pid], lookup[away_pid]
        scores = fixture_scores(fixture)
        if scores is None:
            raise FetchError(f"maç {match_id}: bitmiş maçta skor yok; tutarsız veri yayınlanmaz")
        match_date = fixture_date(fixture)
        if match_date is None:
            raise FetchError(f"maç {match_id}: tarih yok; kayıt yayınlanmaz")
        round_no = fixture_round(fixture)
        if round_no is None:
            raise FetchError(f"maç {match_id}: hafta bilgisi okunamadı; kayıt yayınlanmaz")
        home, away, red_note = fetch_match(client, raw_id_for(match_id), home_id, away_id, notes)
        if red_note:
            red_notes.append(f"maç {match_id}: {red_note}")
        for local in field_hits:
            if local in home or local in away:
                field_hits[local] += 1
        home_goals, away_goals = scores
        fetched.append(
            {
                "match_id": match_id,
                "provider": PROVIDER,
                "round": round_no,
                "date": match_date,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_goals": home_goals,
                "away_goals": away_goals,
                "stats": {home_id: home, away_id: away},
            }
        )

    merged = dict(stored)
    for record in fetched:
        merged[record["match_id"]] = record
    for fixture in fixtures:
        raw_id = probe.fixture_id(fixture)
        record = merged.get(f"goal-{raw_id}") if raw_id is not None else None
        half_time = fixture_half_time_scores(fixture) if record is not None else None
        if half_time is None:
            continue
        if half_time[0] > record["home_goals"] or half_time[1] > record["away_goals"]:
            raise FetchError(f"maç {raw_id}: ilk yarı skoru maç sonu skorunu aşıyor; yayın korunuyor")
        record["half_time_home_goals"], record["half_time_away_goals"] = half_time
    goal_records = list(merged.values())

    if fetched and max(field_hits["shots"], field_hits["passes"], field_hits["corners"]) * 10 < len(fetched) * 8:
        raise FetchError("çekilen maçların en az %80'inde temel istatistik yok; önceki yayın korundu")

    map_season, league_label, teams = update_data.load_teams()
    if map_season != season:
        raise FetchError(f"takım haritası sezonu uyuşmuyor: {map_season} (beklenen {season})")
    combined = others + goal_records
    if not combined:
        raise FetchError("yayınlanacak maç kaydı yok; data/site.json değiştirilmedi")
    update_data.validate(teams, combined)
    state = update_data.aggregate(teams, combined)
    site = update_data.build_site(map_season, league_label, teams, combined, state)

    write_goal_file(season, league_label, goal_records)
    update_data.publish_site(site)
    return {
        "league_id": league_id,
        "standings_rows": len(standings),
        "fixtures": len(fixtures),
        "fetched": len(fetched),
        "stored_total": len(goal_records),
        "skipped_old": skipped_old,
        "skipped_unfinished": skipped_unfinished,
        "field_hits": field_hits,
        "processed": len(fetched),
        "red_notes": red_notes,
        "notes": notes,
        "calls": client.calls,
        "failures": list(client.failures),
        "match_count": len(combined),
        "team_count": len(teams),
    }


def raw_id_for(match_id):
    return match_id[len("goal-"):] if match_id.startswith("goal-") else match_id


def write_goal_file(season, league, records):
    payload = {
        "season": season,
        "league": league,
        "provider": PROVIDER,
        "note": "GOAL API'den çekilen maç kayıtları. Aynı sağlayıcı kimliği ikinci kez eklenmez.",
        "match_count": len(records),
        "matches": records,
    }
    temporary = GOAL_FILE.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, GOAL_FILE)


def print_summary(summary):
    print(f"lig: puan tablosu satırı {summary['standings_rows']}, fikstür {summary['fixtures']}")
    print(
        f"maçlar: {summary['fetched']} çekildi, sağlayıcı dosyasında toplam {summary['stored_total']}; "
        f"{summary['skipped_old']} eski kayıt korundu, {summary['skipped_unfinished']} bitmemiş atlandı"
    )
    coverage = ", ".join(
        f"{local} {summary['field_hits'][local]}/{summary['processed']}" for _label, local in ROW_FIELDS
    )
    print(f"alan kapsamı (işlenen maç): {coverage}")
    for line in summary["red_notes"][:10]:
        print(f"kırmızı kart: {line}")
    for line in summary["notes"][:10]:
        print(f"not: {line}")
    print(f"çağrı: {summary['calls']}, hata: {len(summary['failures'])}")
    for failure in summary["failures"][:10]:
        print(f"hata: {failure}")
    print(f"yayın: data/site.json güncellendi ({summary['match_count']} maç, {summary['team_count']} takım)")


def main():
    key, key_source = probe.load_key()
    if not key:
        print("GOAL_API_KEY tanımlı değil ve depo dışı anahtar dosyası yok. Veri çekilmedi.")
        print("Gerçek API'den veri çekilmiş gibi davranılmadı; hiçbir dosya değişmedi.")
        return 2
    client = ApiClient(key)
    try:
        summary = run(client)
    except (FetchError, update_data.DataError) as error:
        print(f"HATA: {error}", file=sys.stderr)
        print("Yarım veya tutarsız güncelleme yayınlanmadı; data/site.json değiştirilmedi.", file=sys.stderr)
        probe.print_quota(client.last_headers)
        return 1
    print(f"anahtar: {key_source} (değer yazılmadı)")
    print_summary(summary)
    probe.print_quota(client.last_headers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
