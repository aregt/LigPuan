"""LigPuan statik sayfa üreteci: python scripts/generate_site.py -> index.html

Katalog bu dosyada tutulur. Sayılar yalnızca data/site.json'dan okunur;
örnek takım/istatistik listesi burada yoktur. Tablo içeriği doğrudan
HTML'e yazılır, tarayıcıda JavaScript gerekmez.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "index.html"
SITE_FILE = ROOT / "data" / "site.json"

SITE = None
TEAM_BY_ID = {}
FULL_COVERAGE = 0

METRIC_FROM_SITE = {
    "sut": "shots",
    "isabetli_sut": "shots_on_target",
    "korner": "corners",
    "ofsayt": "offsides",
    "pas": "passes",
    "isabetli_pas": "accurate_passes",
    "topa_sahip": "possession",
    "rakip_sut": "shots_faced",
    "kurtaris": "saves",
    "faul": "fouls",
    "sari": "yellow_cards",
    "kirmizi": "red_cards",
    "gol_yemeden": "clean_sheets",
}

STAT_KEYS = tuple(METRIC_FROM_SITE)

CATEGORIES = [
    ("hucum", "Hücum"),
    ("pas-ve-oyun", "Pas ve Oyun"),
    ("savunma-ve-kale", "Savunma ve Kale"),
    ("disiplin", "Disiplin"),
]

LEADERS = [
    {
        "id": "fileleri-havalandiranlar",
        "cat": "hucum",
        "title": "Fileleri Havalandıranlar",
        "desc": "Gol yollarında en üretken takımlar",
        "metric": "gol",
        "col": "Atılan Gol",
        "unit": "gol",
        "secondary": "match",
        "layout": "wide",
    },
    {
        "id": "tetigi-en-cok-cekenler",
        "cat": "hucum",
        "title": "Tetiği En Çok Çekenler",
        "desc": "Gol olur mu bilinmez, denemekten vazgeçmeyenler",
        "metric": "sut",
        "col": "Toplam Şut",
        "unit": "şut",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "kaleyi-bulanlar",
        "cat": "hucum",
        "title": "Kaleyi Bulanlar",
        "desc": "Tribüne değil, çerçeveye",
        "metric": "isabetli_sut",
        "col": "İsabetli Şut",
        "unit": "şut",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "kose-bucak-hucum",
        "cat": "hucum",
        "title": "Köşe Bucak Hücum",
        "desc": "Rakip ceza alanının çevresinden ayrılmayanlar",
        "metric": "korner",
        "col": "Korner",
        "unit": "korner",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "bir-adim-fazla",
        "cat": "hucum",
        "title": "Bir Adım Fazla",
        "desc": "Savunma çizgisine en sık yakalananlar",
        "metric": "ofsayt",
        "col": "Ofsayt",
        "unit": "ofsayt",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "pas-fabrikasi",
        "cat": "pas-ve-oyun",
        "title": "Pas Fabrikası",
        "desc": "Topu dolaştırmayı sevenler",
        "metric": "pas",
        "col": "Toplam Pas",
        "unit": "pas",
        "secondary": "match",
        "layout": "wide",
    },
    {
        "id": "cetvelle-pas",
        "cat": "pas-ve-oyun",
        "title": "Cetvelle Pas",
        "desc": "Adres teslim oynayanlar",
        "metric": "isabetli_pas",
        "col": "İsabetli Pas",
        "unit": "pas",
        "secondary": "ratio",
        "ratio_of": "pas",
        "ratio_col": "Pas Başarı",
        "layout": "pair",
    },
    {
        "id": "top-bizde-kalsin",
        "cat": "pas-ve-oyun",
        "title": "Top Bizde Kalsın",
        "desc": "Oyunun kumandasını en çok elinde tutanlar",
        "metric": "topa_sahip",
        "col": "Topa Sahip Olma",
        "unit": "%",
        "secondary": "none",
        "layout": "pair",
    },
    {
        "id": "abluka-altindakiler",
        "cat": "savunma-ve-kale",
        "title": "Abluka Altındakiler",
        "desc": "Kalesinde en çok şut görenler",
        "metric": "rakip_sut",
        "col": "Rakip Şutu",
        "unit": "şut",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "eldivenler-mesaide",
        "cat": "savunma-ve-kale",
        "title": "Eldivenler Mesaide",
        "desc": "Kalecisine en çok iş düşen takımlar",
        "metric": "kurtaris",
        "col": "Kurtarış",
        "unit": "kurtarış",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "kapiyi-acik-unutanlar",
        "cat": "savunma-ve-kale",
        "title": "Kapıyı Açık Unutanlar",
        "desc": "Kalesini gole en sık kapatamayanlar",
        "metric": "yenilen_gol",
        "col": "Yenilen Gol",
        "unit": "gol",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "gecit-vermeyenler",
        "cat": "savunma-ve-kale",
        "title": "Geçit Vermeyenler",
        "desc": "Kalesini gole kapatanlar",
        "metric": "gol_yemeden",
        "col": "Gol Yemeden Biten Maç",
        "unit": "maç",
        "secondary": "none",
        "layout": "pair",
    },
    {
        "id": "duduk-koleksiyonerleri",
        "cat": "disiplin",
        "title": "Düdük Koleksiyonerleri",
        "desc": "Hakemin düdüğünü en sık duyanlar",
        "metric": "faul",
        "col": "Faul",
        "unit": "faul",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "sariya-abone",
        "cat": "disiplin",
        "title": "Sarıya Abone",
        "desc": "Sarı kartla en sık tanışanlar",
        "metric": "sari",
        "col": "Sarı Kart",
        "unit": "kart",
        "secondary": "match",
        "layout": "pair",
    },
    {
        "id": "erken-dus-kulubu",
        "cat": "disiplin",
        "title": "Erken Duş Kulübü",
        "desc": "Maçı arkadaşlarından önce bitirenler",
        "metric": "kirmizi",
        "col": "Kırmızı Kart",
        "unit": "kart",
        "secondary": "match",
        "layout": "wide",
    },
]

ROW_LIMIT = 5

MEASURES = {
    "fileleri-havalandiranlar": "atılan gol ve maç başına gol",
    "tetigi-en-cok-cekenler": "toplam şut ve maç başına şut",
    "kaleyi-bulanlar": "isabetli şut ve maç başına isabetli şut",
    "kose-bucak-hucum": "korner ve maç başına korner",
    "bir-adim-fazla": "ofsayt ve maç başına ofsayt",
    "pas-fabrikasi": "toplam pas ve maç başına pas",
    "cetvelle-pas": "isabetli pas ve pas başarı yüzdesi",
    "top-bizde-kalsin": "ortalama topa sahip olma yüzdesi",
    "abluka-altindakiler": "rakip şutu ve maç başına rakip şutu",
    "eldivenler-mesaide": "kurtarış ve maç başına kurtarış",
    "kapiyi-acik-unutanlar": "yenilen gol ve maç başına yenilen gol",
    "gecit-vermeyenler": "gol yemeden tamamlanan maç",
    "duduk-koleksiyonerleri": "faul ve maç başına faul",
    "sariya-abone": "sarı kart ve maç başına sarı kart",
    "erken-dus-kulubu": "kırmızı kart ve maç başına kırmızı kart",
}


def load_site(path=None):
    global SITE, TEAM_BY_ID, FULL_COVERAGE
    path = Path(path) if path is not None else SITE_FILE
    SITE = json.loads(path.read_text(encoding="utf-8"))
    teams = SITE.get("teams")
    if not isinstance(teams, list) or not teams:
        raise ValueError("site.json içinde teams listesi yok")
    TEAM_BY_ID = {team["id"]: team for team in teams}
    FULL_COVERAGE = SITE.get("coverage_total") or 0
    return SITE


def team_list():
    return SITE["teams"]


def standing_of(team_id):
    for row in SITE["standings"]:
        if row["team_id"] == team_id:
            return row
    raise KeyError(f"puan durumunda takım yok: {team_id}")


def metric_entry(team_id, metric):
    site_key = METRIC_FROM_SITE[metric]
    values = SITE["metrics"][site_key]["values"]
    if team_id not in values:
        raise KeyError(f"ölçü yok: {site_key}/{team_id}")
    return SITE["metrics"][site_key], values[team_id]


def coverage_of(team_id, metric):
    if metric in ("gol", "yenilen_gol"):
        return standing_of(team_id)["played"]
    _meta, entry = metric_entry(team_id, metric)
    return entry["coverage"]


def total_of(team_id, metric):
    if metric == "gol":
        return standing_of(team_id)["goals_for"]
    if metric == "yenilen_gol":
        return standing_of(team_id)["goals_against"]
    meta, entry = metric_entry(team_id, metric)
    if entry["coverage"] <= 0:
        return None
    if meta["kind"] == "average":
        return entry["sum"] / entry["coverage"]
    return entry["sum"]


def fmt_int(value):
    return f"{int(value):,}".replace(",", ".")


def fmt_decimal(value, digits=2):
    quantized = round(float(value), digits)
    return f"{quantized:.{digits}f}".replace(".", ",")


def fmt_total(value, metric):
    if value is None:
        return "—"
    if isinstance(value, float):
        return fmt_decimal(value, 1)
    if metric in ("pas", "isabetli_pas"):
        return fmt_int(value)
    return str(value)


def per_match(value, coverage, metric):
    if value is None or coverage <= 0:
        return "—"
    return fmt_rate(value / coverage)


def fmt_rate(rate):
    if abs(rate - round(rate)) < 1e-9:
        return str(int(round(rate)))
    return f"{rate:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def ratio_value(leader):
    numerator = leader["metric"]
    denominator = leader["ratio_of"]
    parts = []
    for team in team_list():
        team_id = team["id"]
        num_cov = coverage_of(team_id, numerator)
        den_cov = coverage_of(team_id, denominator)
        if num_cov <= 0 or den_cov <= 0 or num_cov != den_cov:
            parts.append((team_id, None))
            continue
        num = total_of(team_id, numerator)
        den = total_of(team_id, denominator)
        if num is None or den in (None, 0):
            parts.append((team_id, None))
            continue
        parts.append((team_id, num / den * 100))
    return dict(parts)


def ranked_rows(leader):
    metric = leader["metric"]
    rows = []
    for team in team_list():
        team_id = team["id"]
        coverage = coverage_of(team_id, metric)
        if coverage <= 0:
            continue
        total = total_of(team_id, metric)
        if total is None:
            continue
        rows.append({"id": team_id, "name": team["name"], "total": total, "coverage": coverage})
    rows.sort(key=lambda row: (-row["total"], row["name"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = str(index)
    return rows


def no_data_rows(leader):
    metric = leader["metric"]
    rows = []
    for team in team_list():
        if coverage_of(team["id"], metric) <= 0:
            rows.append({"id": team["id"], "name": team["name"], "coverage": 0, "rank": "—", "total": None})
    return rows


def render_row(row, leader):
    team = TEAM_BY_ID[row["id"]]
    rank_class = "rank" if row["total"] is not None else "rank rank-none"
    cells = [f'<th scope="row" class="{rank_class}">{row["rank"]}</th>']
    cells.append(
        f'<td class="team"><span class="badge {team["badge"]}" aria-hidden="true">{team["code"]}</span>{row["name"]}</td>'
    )
    if row["total"] is None:
        cells.append('<td class="value">—</td>')
        if leader["secondary"] in ("match", "ratio"):
            cells.append("<td>—</td>")
        cells.append(f'<td class="scope">{row["coverage"]}/{FULL_COVERAGE}</td>')
        return "                    <tr>" + "".join(cells) + "</tr>"
    cells.append(f'<td class="value">{fmt_total(row["total"], leader["metric"])}</td>')
    if leader["secondary"] == "match":
        cells.append(f'<td>{per_match(row["total"], row["coverage"], leader["metric"])}</td>')
    elif leader["secondary"] == "ratio":
        ratio = leader["_ratios"].get(row["id"])
        cells.append(f'<td>{"—" if ratio is None else fmt_decimal(ratio, 1) + " %"}</td>')
    cells.append(f'<td class="scope">{row["coverage"]}/{FULL_COVERAGE}</td>')
    return "                    <tr>" + "".join(cells) + "</tr>"


def render_leader(leader):
    leader["_ratios"] = ratio_value(leader) if leader["secondary"] == "ratio" else {}
    rows = ranked_rows(leader) + no_data_rows(leader)
    metric = leader["metric"]
    css = "leader leader--wide" if leader["layout"] == "wide" else "leader"
    scroll_id = f'{leader["id"]}-scroll'
    teams = team_list()

    headers = ['<th scope="col">S</th>', '<th scope="col">Takım</th>', f'<th scope="col">{leader["col"]}</th>']
    if leader["secondary"] == "match":
        headers.append('<th scope="col">Maç Başı</th>')
    elif leader["secondary"] == "ratio":
        headers.append(f'<th scope="col">{leader["ratio_col"]}</th>')
    headers.append('<th scope="col">Kapsam</th>')

    body = "\n".join(render_row(row, leader) for row in rows)

    measure = MEASURES[leader["id"]]
    measure_line = (
        f'Ölçü: {measure} (birim: {leader["unit"]}) · Tabloda {len(teams)} takım · '
        f'Kapsam: {FULL_COVERAGE} maç · Açmak için tabloya tıklayın'
    )

    lines = [
        f'          <section class="{css}" id="{leader["id"]}" aria-labelledby="{leader["id"]}-title">',
        '            <div class="leader-bar">',
        '              <div class="leader-bar-main">',
        f'                <h4 class="leader-name" id="{leader["id"]}-title"><a href="#{leader["id"]}">{leader["title"]}</a></h4>',
        f'                <span class="leader-state" aria-hidden="true"><span class="leader-state-mark">+</span>'
        f'<span class="leader-state-text">{len(teams)} takım</span></span>',
        "              </div>",
        f'              <span class="leader-desc">{leader["desc"]}</span>',
        "            </div>",
        f'            <p class="leader-measure">{measure_line}</p>',
        f'            <div class="leader-scroll" id="{scroll_id}" tabindex="0" role="region" '
        f'aria-expanded="false" aria-label="{leader["title"]} tablosu, tıklayınca {len(teams)} takımın tamamı görünür, dikey kaydırılabilir" '
        f'data-state-closed="{len(teams)} takım" data-state-open="İlk {ROW_LIMIT}">',
        '              <table class="leader-table">',
        f'                <caption class="sr-only">{leader["title"]}: {measure}, {len(teams)} takım</caption>',
        "                <thead>",
        "                  <tr>" + "".join(headers) + "</tr>",
        "                </thead>",
        "                <tbody>",
        body,
        "                </tbody>",
        "              </table>",
        "            </div>",
    ]

    if all(coverage_of(team["id"], metric) <= 0 for team in teams):
        lines.append(
            f'            <p class="leader-note">Bu ölçüde yayınlanacak veri yok. Ölçü: {measure}. '
            f"Eksik değer sıfır yazılmaz.</p>"
        )

    absent = [row["name"] for row in no_data_rows(leader)]
    if absent and len(absent) < len(teams):
        lines.append(
            f'            <p class="leader-note">Veri yok: {", ".join(absent)} · Ölçü: {measure} · '
            f"Kapsam 0/{FULL_COVERAGE} · Bu takımlara sayısal sıralama verilmedi.</p>"
        )

    ratio_missing = [
        team["name"]
        for team in teams
        if leader["secondary"] == "ratio"
        and leader["_ratios"].get(team["id"]) is None
        and coverage_of(team["id"], metric) > 0
    ]
    if ratio_missing:
        lines.append(
            f'            <p class="leader-note">Oran gösterilemeyen takım (pay ve payda kapsamı uyuşmuyor): {", ".join(ratio_missing)}.</p>'
        )

    lines.append("          </section>")
    return "\n".join(lines)


def standings_rows():
    rows = []
    for row in SITE["standings"]:
        team = TEAM_BY_ID[row["team_id"]]
        rows.append(
            {
                "id": row["team_id"],
                "name": team["name"],
                "code": team["code"],
                "badge": team["badge"],
                "o": row["played"],
                "g": row["won"],
                "b": row["drawn"],
                "m": row["lost"],
                "ag": row["goals_for"],
                "yg": row["goals_against"],
                "av": row["average"],
                "p": row["points"],
                "deduction": row.get("points_deduction", 0),
            }
        )
    rows.sort(key=lambda row: (-row["p"], -row["av"], row["name"]))
    return rows


def source_note():
    kind = SITE.get("source_kind")
    match_count = SITE.get("match_count") or 0
    if kind == "sample":
        return (
            f"Örnek sıralama · Gerçek lig verisi değildir · {FULL_COVERAGE} maçlık gösterim · "
            f"{match_count} saklanan maçtan hesaplandı"
        )
    if kind == "provider":
        penalties = [row for row in standings_rows() if row["deduction"]]
        note = f"{match_count} bitmiş maçtan hesaplandı · Kapsam {FULL_COVERAGE} maç"
        if penalties:
            listed = ", ".join(f'{row["name"]} −{row["deduction"]}' for row in penalties)
            note += f' · Ceza puanı: {listed} (<a href="https://www.tff.org/?pageID=198">TFF</a>)'
        return note
    if kind == "mixed":
        return "Kaynaklar karışık; örnek ve sağlayıcı kayıtları aynı yayında birleşmedi sayılmaz"
    return f"{match_count} saklanan maçtan hesaplandı · Kapsam {FULL_COVERAGE} maç"


def render_standings():
    rows = standings_rows()
    teams = team_list()
    lines = [
        '    <section class="standings" id="puan-durumu" aria-labelledby="standings-title">',
        '      <div class="section-bar">',
        '        <h1 class="section-bar-title" id="standings-title">PUAN DURUMU</h1>',
        '        <span class="section-bar-note">BU LİG HEPİMİZİN</span>',
        "      </div>",
        "",
        '      <p class="scroll-hint">Tüm istatistikler için tabloyu kaydırın <span aria-hidden="true">↔</span></p>',
        '      <div class="table-scroll" tabindex="0" role="region" aria-label="Puan durumu tablosu, dikey ve yatay kaydırılabilir">',
        '        <table class="standings-table">',
        f'          <caption class="sr-only">Türkiye Süper Lig puan durumu, {len(teams)} takım, {FULL_COVERAGE}/{FULL_COVERAGE} maç</caption>',
        "          <thead>",
        "            <tr>",
        '              <th scope="col">S</th>',
        '              <th scope="col">Takım</th>',
        '              <th scope="col">O</th>',
        '              <th scope="col">G</th>',
        '              <th scope="col">B</th>',
        '              <th scope="col">M</th>',
        '              <th scope="col">AV</th>',
        '              <th scope="col">P</th>',
        "            </tr>",
        "          </thead>",
        "          <tbody>",
    ]

    for index, row in enumerate(rows, start=1):
        av_class = "av-up" if row["av"] > 0 else ("av-down" if row["av"] < 0 else "av-zero")
        point_class = "points top" if index == 1 else "points"
        lines.append(
            "            <tr>"
            f'<th scope="row" class="rank">{index}</th>'
            f'<td class="team"><span class="badge {row["badge"]}" aria-hidden="true">{row["code"]}</span>{row["name"]}</td>'
            f'<td>{row["o"]}</td><td>{row["g"]}</td><td>{row["b"]}</td><td>{row["m"]}</td>'
            f'<td class="{av_class}">{row["av"]:+d}</td>'
            f'<td class="{point_class}">{row["p"]}</td>'
            "</tr>"
        )

    lines += [
        "          </tbody>",
        "        </table>",
        "      </div>",
        "",
        '      <div class="standings-foot">',
        f'        <p class="sample-note">{source_note()}</p>',
        "      </div>",
        "    </section>",
    ]
    return "\n".join(lines)


def render_group(cat_id, cat_title):
    leaders = [leader for leader in LEADERS if leader["cat"] == cat_id]
    blocks = []
    pending = []

    def flush():
        nonlocal pending
        if not pending:
            return
        if len(pending) == 1:
            blocks.append(render_leader(pending[0]))
        else:
            inner = "\n".join(render_leader(leader) for leader in pending)
            blocks.append('          <div class="leader-pair">\n' + inner + "\n          </div>")
        pending = []

    for leader in leaders:
        if leader["layout"] == "pair":
            pending.append(leader)
            if len(pending) == 2:
                flush()
        else:
            flush()
            blocks.append(render_leader(leader))
    flush()

    head = [
        f'      <section class="leader-group" id="{cat_id}" aria-labelledby="{cat_id}-baslik">',
        '        <div class="leader-group-head">',
        f'          <h3 class="leader-group-title" id="{cat_id}-baslik">{cat_title}</h3>',
        f'          <span class="leader-group-count">{len(leaders)} tablo · {len(team_list())} takım</span>',
        "        </div>",
    ]
    return "\n".join(head + blocks + ["      </section>"])


def this_week_items():
    latest = SITE.get("latest_round")
    latest_count = SITE.get("latest_round_match_count") or 0
    match_count = SITE.get("match_count") or 0
    if latest is None:
        return ["Haftalık kesit yok; tablolardaki sayılar sezon toplamından gelir."]
    return [
        f"Son tamamlanan hafta: {latest}. Hafta.",
        f"Bu haftada {latest_count} maç var.",
        f"Sezon toplamı: {match_count} maç.",
    ]


def render_this_week():
    sample_mark = " <span>ÖRNEK</span>" if SITE.get("source_kind") == "sample" else ""
    items = this_week_items()
    lines = [
        '          <section class="this-week" aria-labelledby="this-week-title">',
        f'            <h3 class="this-week-title" id="this-week-title">BU HAFTA{sample_mark}</h3>',
        '            <ol class="this-week-list">',
    ]
    for index, text in enumerate(items, start=1):
        lines.append("              <li>")
        lines.append(f'                <span class="this-week-num" aria-hidden="true">{index}</span>')
        lines.append(f"                <p>{text}</p>")
        lines.append("              </li>")
    lines += ["            </ol>", "          </section>"]
    return "\n".join(lines)


def render_insights():
    entries = SITE.get("insights") or []
    if not entries:
        return ""
    titles = {
        "pas-var-puan-yok": "Pas Var, Puan Yok",
        "sut-cok-gol-nerede": "Şut Çok, Gol Nerede?",
        "evde-aslan": "Evde Aslan, Deplasmanda Kedi",
        "devre-arasi-rahatti": "Devre Arası Rahattı, Son Düdük Değil",
    }
    cards = []
    for entry in entries:
        kind = entry["id"]
        team = TEAM_BY_ID[entry["team_id"]]
        if kind == "pas-var-puan-yok":
            number = f'{fmt_rate(entry["passes"] / entry["coverage"])} pas / maç'
            detail = f'Aynı {entry["coverage"]} maçta yalnızca {fmt_rate(entry["points"] / entry["coverage"])} puan / maç. Pas ortalaması yüksek, puan ortalaması lig medyanının altında.'
            scope = f'Kapsam: {entry["coverage"]}/{entry["played"]} maç · Puan ve pas aynı maçlardan'
        elif kind == "sut-cok-gol-nerede":
            number = f'{entry["shots"]} şut · {entry["goals"]} gol'
            detail = 'Şut ortalaması ligin üst yarısında; gol/şut oranı bu grupta en düşük.'
            scope = f'Kapsam: {entry["coverage"]}/{entry["played"]} maç · Goller yalnızca şutu bilinen maçlardan'
        elif kind == "evde-aslan":
            number = f'{fmt_rate(entry["home_points"] / entry["home_games"])} / {fmt_rate(entry["away_points"] / entry["away_games"])}'
            detail = 'Evde / deplasmanda maç başına puan. İki saha arasındaki fark ligin en büyüğü.'
            scope = f'Kapsam: {entry["home_games"]} iç saha · {entry["away_games"]} deplasman maçı'
        elif kind == "devre-arasi-rahatti":
            number = f'{entry["dropped_points"]} puan bıraktı'
            detail = f'İlk yarıyı önde bitirdiği {entry["lost_leads"]} maçta galibiyeti koruyamadı.'
            scope = f'Kapsam: {entry["coverage"]}/{entry["played"]} maç · İlk yarı ve maç sonu skorları'
        else:
            continue
        cards.append(
            f'      <article class="insight-card" id="{kind}">\n'
            f'        <h3><a href="#{kind}">{titles[kind]}</a></h3>\n'
            f'        <p class="insight-team">{escape(team["name"])}</p>\n'
            f'        <p class="insight-number">{number}</p>\n'
            f'        <p class="insight-detail">{detail}</p>\n'
            f'        <p class="insight-scope">{scope}</p>\n'
            '      </article>'
        )
    if not cards:
        return ""
    pending = (
        '' if any(entry["id"] == "devre-arasi-rahatti" for entry in entries) else
        '      <p class="insights-pending">Devre Arası Rahattı, Son Düdük Değil: '
        'İlk yarı skorları henüz kayıtlı değil; veri geldiğinde bu karşılaştırma açılacak.</p>\n'
    )
    return (
        '    <section class="insights" id="sahanin-ters-kosesi" aria-labelledby="insights-title">\n'
        '      <header class="insights-heading"><h2 id="insights-title">SAHANIN TERS KÖŞESİ</h2>'
        '<p>Tabloda ilk bakışta görünmeyenler</p></header>\n'
        '      <div class="insights-grid">\n' + "\n".join(cards) + '\n      </div>\n' + pending +
        '    </section>'
    )


def build_main():
    parts = [
        '    <aside class="ad ad--leaderboard" aria-label="Reklam">\n'
        '      <span class="ad-label">Reklam alanı · 970×90</span>\n'
        "    </aside>",
        "    <div class=\"season-strip\">\n"
        f'      <span class="season-strip-title">{league_strip()}</span>\n'
        '      <span class="season-strip-motto">Daha fazla futbol | Daha fazla veri | Her zaman LigPuan</span>\n'
        "    </div>",
        render_standings(),
    ]
    parts.append(
        '    <aside class="ad ad--matchday" aria-label="Reklam">\n'
        '      <span class="ad-label">Reklam alanı · Maç günü sponsoru</span>\n'
        "    </aside>"
    )
    insights = render_insights()
    if insights:
        parts.append(insights)

    jump = "\n".join(
        f'          <a href="#{cat_id}-baslik">{cat_title}</a>' for cat_id, cat_title in CATEGORIES
    )
    parts.append(
        '    <section class="leaders" id="ligin-enleri" aria-labelledby="leaders-title">\n'
        '      <header class="leaders-heading">\n'
        '        <h2 class="leaders-title" id="leaders-title">LİGİN ENLERİ</h2>\n'
        '        <span class="leaders-rule" aria-hidden="true"></span>\n'
        '        <span class="leaders-kicker">SAHADAKİ SAYILARIN HİKÂYESİ</span>\n'
        "      </header>\n"
        '      <nav class="leader-jump" aria-label="Liderlik kategorileri">\n'
        f"{jump}\n"
        "      </nav>\n"
        '      <div class="leaders-layout">\n'
        '        <div class="leaders-main">\n'
        + render_group(*CATEGORIES[0])
        + "\n"
        + render_group(*CATEGORIES[1])
        + "\n"
        + '          <aside class="ad ad--weekly" aria-label="Reklam">\n'
        + '            <span class="ad-label">Haftanın liderliği · Sponsor alanı</span>\n'
        + "          </aside>\n"
        + render_group(*CATEGORIES[2])
        + "\n"
        + render_group(*CATEGORIES[3])
        + "\n"
        + "        </div>\n"
        "\n"
        '        <aside class="commercial-rail">\n'
        '          <div class="ad ad--rectangle" aria-label="Reklam">\n'
        '            <span class="ad-label">Reklam alanı</span>\n'
        '            <span class="ad-size">300×250</span>\n'
        "          </div>\n"
        "\n"
        + render_this_week()
        + "\n"
        "\n"
        '          <div class="ad ad--skyscraper" aria-label="Reklam">\n'
        '            <span class="ad-label">Reklam alanı</span>\n'
        '            <span class="ad-size">300×600</span>\n'
        "          </div>\n"
        "        </aside>\n"
        "      </div>\n"
        "    </section>"
    )
    return "\n\n".join(parts)


def league_strip():
    league = SITE.get("league") or "Türkiye Süper Lig"
    return f"{league.translate(str.maketrans({'i': 'İ', 'ı': 'I'})).upper()} · {season_label()}"


def season_label():
    season = SITE.get("season") or ""
    if isinstance(season, str) and "-" in season:
        return season.replace("-", "/", 1)
    return season


def parse_generated_at(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_stale(generated, now=None):
    if generated is None:
        return True
    now = now or datetime.now(timezone.utc)
    return generated.date() < now.date()


def compact_stamp(generated):
    return f"{generated:%d.%m.%Y %H:%M} UTC"


def updated_markup():
    generated = parse_generated_at(SITE.get("generated_at"))
    stale = is_stale(generated)
    css = "updated is-stale" if stale else "updated"
    if generated is None:
        inner = '<span class="preview-dot" aria-hidden="true"></span>Son güncelleme yok'
        return f'<p class="{css}">{inner}</p>'
    label = "Eski veri" if stale else "Son güncelleme"
    stamp = compact_stamp(generated)
    iso = generated.replace(microsecond=0).isoformat()
    inner = (
        f'<span class="preview-dot" aria-hidden="true"></span>{label} '
        f'<time datetime="{iso}">{stamp}</time>'
    )
    return f'<p class="{css}">{inner}</p>'


def render_head():
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LigPuan — Türkiye Süper Lig puan durumu ve ligin enleri</title>
  <meta name="description" content="Türkiye Süper Lig puan durumu ve ligin enleri: gol, şut, korner, pas, topa sahip olma, savunma ve disiplin sıralamaları tek sayfada.">
  <link rel="canonical" href="https://ligpuan.com/">
  <link rel="icon" href="data:,">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto+Condensed:wght@400;600;700;800&display=swap">
  <link rel="stylesheet" href="styles.css">
  <script>document.documentElement.classList.add("has-js");</script>
</head>
<body>
  <a class="skip-link" href="#puan-durumu">Puan durumuna geç</a>
  <header class="masthead">
    <div class="site-shell masthead-inner">
      <a class="brand" href="#puan-durumu">
        <svg class="brand-mark" viewBox="0 0 34 34" width="34" height="34" aria-hidden="true" focusable="false">
          <rect x="1" y="1" width="32" height="32" fill="none" stroke="#fdce44" stroke-width="2"></rect>
          <circle cx="10" cy="10" r="3.2" fill="#fdce44"></circle>
          <circle cx="24" cy="10" r="3.2" fill="#fdce44"></circle>
          <circle cx="17" cy="17" r="3.2" fill="#fdce44"></circle>
          <circle cx="10" cy="24" r="3.2" fill="#fdce44"></circle>
          <circle cx="24" cy="24" r="3.2" fill="#fdce44"></circle>
        </svg>
        <span class="brand-name">LigPuan</span>
      </a>

      <nav class="nav" aria-label="Ana menü">
        <a href="#puan-durumu">Puan Durumu</a>
        <a href="#ligin-enleri">Ligin Enleri</a>
        <a href="#veri-ve-yontem">Veri ve Yöntem</a>
      </nav>

      <p class="season">
        <span class="season-label">{season_label()}</span>
        <span class="season-caption">SEZON</span>
      </p>

      {updated_markup()}
    </div>
  </header>
"""


def render_footer():
    return """  <footer class="site-footer">
    <p class="site-shell method-note" id="veri-ve-yontem"><strong>Veri ve yöntem</strong> Tablolar <code>data/site.json</code> yayın dosyasından üretilir; tarayıcıda boş kabuk doldurulmaz. Sezon toplamları saklanan bitmiş maçlardan yeniden hesaplanır. Aynı maç kimliği ikinci kez sayılmaz. Eksik alan sıfır yazılmaz; kapsam 0 olan takım sıralamanın sonunda “—” ve “Veri yok” notuyla durur. Eşitlikte sıra: puan, averaj, takım adı (A–Z). Maç başı değer kapsamdaki maç sayısına bölünür. Yüzde ve oran yalnızca pay ile paydanın kapsamı aynıysa yazılır. Rakip şutu rakibin şutundan hesaplanır. Topa sahip olma toplanmaz, kapsamdaki maçların ortalamasıdır. Puan 3G+B’den doğrulanmış ceza puanı düşülerek bulunur. Ters Köşe içgörüleri yalnızca aynı maçlarda bilinen alanları karşılaştırır; en az beş istatistikli maç, iç/dış saha farkı için her sahada en az iki maç gerekir. İlk yarı kartı yalnızca iki devre skoru da bilinen maçlardan hesaplanır. Bu kartlar gelecek maç tahmini değildir. Veri bir günden eskiyse “Eski veri” uyarısı gösterilir. Açılışta liderlik tablolarında ilk beş satır görünür; JavaScript çalışmazsa bütün satırlar açıktır.</p>
    <div class="site-shell footer-inner">
      <div class="footer-brand">
        <svg class="footer-mark" viewBox="0 0 34 34" width="24" height="24" aria-hidden="true" focusable="false">
          <rect x="1" y="1" width="32" height="32" fill="none" stroke="#fdce44" stroke-width="2"></rect>
          <circle cx="10" cy="10" r="3.2" fill="#fdce44"></circle>
          <circle cx="24" cy="10" r="3.2" fill="#fdce44"></circle>
          <circle cx="17" cy="17" r="3.2" fill="#fdce44"></circle>
          <circle cx="10" cy="24" r="3.2" fill="#fdce44"></circle>
          <circle cx="24" cy="24" r="3.2" fill="#fdce44"></circle>
        </svg>
        <span class="footer-name">LigPuan</span>
        <span class="footer-line">Türkiye Süper Lig istatistik platformu.</span>
      </div>
      <ul class="footer-links">
        <li><a href="#veri-ve-yontem">Veri ve Yöntem</a></li>
      </ul>
      <p class="footer-motto">Futbol sayılarla daha güzel</p>
    </div>
  </footer>"""


SCRIPT = """  <script>
    (function () {
      var regions = Array.prototype.slice.call(document.querySelectorAll(".leader-scroll"));

      function setState(region, expanded) {
        var section = region.closest(".leader");
        region.setAttribute("aria-expanded", expanded ? "true" : "false");
        section.classList.toggle("is-expanded", expanded);
        section.querySelector(".leader-state-mark").textContent = expanded ? "\\u2212" : "+";
        section.querySelector(".leader-state-text").textContent = expanded ? region.getAttribute("data-state-open") : region.getAttribute("data-state-closed");
        if (!expanded) {
          region.scrollTop = 0;
        }
      }

      function closeOthers(region) {
        regions.forEach(function (other) {
          if (other !== region && other.getAttribute("aria-expanded") === "true") {
            setState(other, false);
          }
        });
      }

      regions.forEach(function (region) {
        var card = region.closest(".leader");
        card.addEventListener("click", function () {
          closeOthers(region);
          setState(region, true);
        });
        region.addEventListener("keydown", function (event) {
          if (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar") {
            return;
          }
          event.preventDefault();
          var expanded = region.getAttribute("aria-expanded") === "true";
          if (expanded) {
            setState(region, false);
          } else {
            closeOthers(region);
            setState(region, true);
          }
        });
      });
    })();
  </script>"""


def build_page(site=None):
    if site is not None:
        global SITE, TEAM_BY_ID, FULL_COVERAGE
        SITE = site
        TEAM_BY_ID = {team["id"]: team for team in SITE["teams"]}
        FULL_COVERAGE = SITE.get("coverage_total") or 0
    elif SITE is None:
        load_site()
    return "\n".join(
        [render_head(), '  <main class="site-shell">', build_main(), "  </main>", "", render_footer(), "", SCRIPT, "</body>", "</html>", ""]
    )


def main():
    if SITE is None:
        load_site()
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(build_page())


if __name__ == "__main__":
    load_site()
    main()
