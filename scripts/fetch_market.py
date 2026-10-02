#!/usr/bin/env python3
"""
Daily market data collector for the agent folder in the vault.

Pulls official/open data only (no portal scraping):
  - Banco Central SGS: Selic target, IPCA monthly, average auto-loan rate, new
    auto loans and auto-loan default rate (individuals)
  - Banco Central Focus survey: market median expectation for the Selic
  - FIPE reference prices from the official FIPE site (scripts/fipe.py), BrasilAPI
    as a fallback: the codes in watchlist.json and the basket of popular models in
    config/fipe-basket.json (fetched once per FIPE month)
  - Senatran: Uberlândia's vehicle fleet by type, monthly (scripts/fetch_fleet.py)

Reads (inside the agent folder in the vault, which is outside this repo):
  Market/watchlist.json        FIPE codes of the vehicles you track

Writes (inside the agent folder):
  Market/data/history.csv      append-only, one row per data point per run
  Market/data/fipe_history.csv basket values, one row per model year per FIPE month
  Market/data/fleet.csv        Uberlândia fleet by vehicle type, one block per month
  Market/data/latest.md        human-readable snapshot with trends, overwritten each run

Standard library only, so nothing to pip install.
Run from anywhere: python3 scripts/fetch_market.py
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

import fetch_fleet
import fipe
from common import agent_dir

# Banco Central SGS series codes. Metadata of each series (name, unit, periodicity)
# is on the Banco Central open data portal, https://dadosabertos.bcb.gov.br/dataset/<code>-...
SGS_SERIES = {
    432: "Selic target (% a.a.)",
    433: "IPCA monthly (%)",
    20749: "Avg auto loan rate, individuals (% a.a.)",
    # https://dadosabertos.bcb.gov.br/dataset/20673-concessoes-de-credito-com-recursos-livres---pessoas-fisicas---aquisicao-de-veiculos
    # "Concessões de crédito com recursos livres - Pessoas físicas - Aquisição de veículos", R$ millions per month
    20673: "New auto loans, individuals (R$ million/month)",
    # https://dadosabertos.bcb.gov.br/dataset/21121-inadimplencia-da-carteira-de-credito-com-recursos-livres---pessoas-fisicas---aquisicao-de-vei
    # "Inadimplência da carteira de crédito com recursos livres - Pessoas físicas - Aquisição de veículos":
    # % of the portfolio more than 90 days overdue, monthly
    21121: "Auto loan default rate, individuals (% >90 days)",
}
# Monthly series: fetch 13 points so latest.md can show the change over 3 and 12 months.
MONTHLY = {433, 20749, 20673, 21121}

HISTORY_FIELDS = ["run_date", "source", "key", "label", "period", "value"]
FLEET_TYPES = ["TOTAL", "AUTOMOVEL", "CAMINHONETE", "CAMIONETA", "UTILITARIO", "MOTOCICLETA", "MOTONETA"]


# ---------- helpers ----------------------------------------------------------

def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "vault-market-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_float(text):
    """Accepts '13.75', '13,75' or 'R$ 80.000,00' and returns a float."""
    s = str(text).replace("R$", "").strip()
    if "," in s:                      # Brazilian format: 80.000,00
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def fmt(value, decimals=2):
    return f"{value:,.{decimals}f}" if value is not None else "-"


# ---------- sources ----------------------------------------------------------

def parse_sgs(rows):
    """[{"data": "01/08/2026", "valor": "22551"}, ...] -> [(period, value)], oldest first."""
    return [(r["data"], to_float(r["valor"])) for r in rows]


def fetch_sgs(code, last=1):
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/{last}?formato=json"
    return parse_sgs(get_json(url))


def _month_key(period):
    """'01/08/2026' -> (2026, 8)."""
    d, m, y = period.split("/")
    return int(y), int(m)


def trend(points):
    """For a monthly series: (latest period, latest, 3 months before, 12 months before);
    a missing month gives None."""
    if not points:
        return None
    values = {_month_key(p): v for p, v in points}
    period, latest = points[-1]
    y, m = _month_key(period)

    def back(n):
        yy, mm = y, m - n
        while mm <= 0:
            yy, mm = yy - 1, mm + 12
        return values.get((yy, mm))
    return period, latest, back(3), back(12)


def change(now, before):
    if now is None or before is None:
        return "-"
    return f"{now - before:+,.2f}"


def fetch_focus_selic():
    """Latest Focus median for end-of-year Selic, current year and next.
    Field names follow the BCB Olinda OData service; if Banco Central changes them,
    this function fails loudly and the error lands in latest.md."""
    base = ("https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
            "ExpectativasMercadoAnuais")
    params = {
        "$top": "40",
        "$filter": "Indicador eq 'Selic' and baseCalculo eq 0",
        "$orderby": "Data desc",
        "$select": "Data,DataReferencia,Mediana",
        "$format": "json",
    }
    url = base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    rows = get_json(url)["value"]
    if not rows:
        raise RuntimeError("Focus returned no rows")
    latest_day = rows[0]["Data"]
    this_year = date.today().year
    wanted = {str(this_year), str(this_year + 1)}
    out = {}
    for r in rows:
        if r["Data"] == latest_day and r["DataReferencia"] in wanted:
            out[r["DataReferencia"]] = float(r["Mediana"])
    return latest_day, out


def fetch_fipe_brasilapi(code, model_year):
    rows = get_json(f"https://brasilapi.com.br/api/fipe/preco/v1/{code}")
    for r in rows:
        if int(r.get("anoModelo", 0)) == int(model_year):
            return fipe.parse_month(r["mesReferencia"]), to_float(r["valor"]), r.get("modelo", "")
    years = sorted({r.get("anoModelo") for r in rows})
    raise RuntimeError(f"model year {model_year} not found for {code}; available: {years}")


def fetch_fipe(code, model_year, client=None, table=None):
    """Official FIPE API first; BrasilAPI if that fails.
    Returns (source, 'YYYY-MM', value, model name)."""
    official_error = "no reference table"
    if client is not None and table is not None:
        try:
            p = client.price_for_year(code, model_year, table)
            return "FIPE", p["month"], p["value"], p["model"]
        except fipe.FipeError as e:
            official_error = e
    try:
        return ("FIPE-BrasilAPI",) + fetch_fipe_brasilapi(code, model_year)
    except Exception as e:
        raise RuntimeError(f"official FIPE: {official_error}; BrasilAPI: {e}")


# ---------- latest.md ----------------------------------------------------------

def render_latest(points, trends, basket_month, basket_rows, fleet_rows, errors, now=None):
    now = now or datetime.now()
    lines = [
        "---",
        "type: market-snapshot",
        f"updated: {now.isoformat(timespec='minutes')}",
        "---",
        "# Market snapshot",
        "",
        "Generated by scripts/fetch_market.py. Do not edit by hand.",
        "",
        "| Indicator | Period | Value | Source |",
        "|---|---|---|---|",
    ]
    for source, key, label, period, value in points:
        lines.append(f"| {label} | {period} | {value:,.2f} | {source} |")

    if trends:
        lines += ["", "## Trends (monthly series)", "",
                  "Change is in the series' own unit (percentage points for rates).", "",
                  "| Indicator | Latest month | Latest | 3 months before | 12 months before | Change 3m | Change 12m |",
                  "|---|---|---|---|---|---|---|"]
        for label, (period, latest, m3, m12) in trends:
            lines.append(f"| {label} | {period} | {fmt(latest)} | {fmt(m3)} | {fmt(m12)} "
                         f"| {change(latest, m3)} | {change(latest, m12)} |")

    if basket_month and basket_rows:
        lines += ["", f"## FIPE basket of popular used models ({basket_month})", "",
                  "Full history in Market/data/fipe_history.csv (one row per model year and FIPE month).", "",
                  "| Model | Segment | Model year | FIPE now (R$) | 12 months before (R$) | Change 12m |",
                  "|---|---|---|---|---|---|"]
        for r in basket_rows:
            pct = f"{r['change_pct']:+.1f}%" if r["change_pct"] is not None else "-"
            lines.append(f"| {r['label']} | {r['segment']} | {r['year']} | {fmt(r['now'], 0)} "
                         f"| {fmt(r['year_ago'], 0)} | {pct} |")

    month, fleet, year_ago = fetch_fleet.summary(fleet_rows)
    if month:
        lines += ["", f"## Vehicle fleet of Uberlândia (Senatran, {month})", "",
                  "Registered vehicles by type. Full history in Market/data/fleet.csv.", "",
                  "| Type | Vehicles | 12 months before | Change 12m |", "|---|---|---|---|"]
        for t in FLEET_TYPES:
            if t in fleet:
                before = year_ago.get(t)
                pct = f"{(fleet[t] / before - 1) * 100:+.1f}%" if before else "-"
                lines.append(f"| {t} | {fleet[t]:,} | {f'{before:,}' if before else '-'} | {pct} |")

    if errors:
        lines += ["", "## Fetch errors", ""] + [f"- {e}" for e in errors]
    return "\n".join(lines) + "\n"


# ---------- main -------------------------------------------------------------

def main():
    base = agent_dir()
    data_dir = base / "Market" / "data"
    history = data_dir / "history.csv"
    latest = data_dir / "latest.md"
    watchlist = base / "Market" / "watchlist.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    points, trends, errors = [], [], []

    for code, label in SGS_SERIES.items():
        try:
            series = fetch_sgs(code, 13 if code in MONTHLY else 1)
            if series:
                period, value = series[-1]
                points.append(("BCB-SGS", str(code), label, period, value))
            if code in MONTHLY and series:
                trends.append((label, trend(series)))
        except Exception as e:
            errors.append(f"SGS {code} ({label}): {e}")

    try:
        survey_day, medians = fetch_focus_selic()
        for year, value in sorted(medians.items()):
            points.append(("BCB-Focus", f"selic_{year}", f"Focus median Selic end-{year} (% a.a.)",
                           survey_day, value))
    except Exception as e:
        errors.append(f"Focus Selic: {e}")

    client, table, fipe_month = fipe.Client(), None, None
    try:
        table, fipe_month = client.tables()[0]
    except Exception as e:
        errors.append(f"FIPE reference tables: {e}")

    try:
        watch = json.loads(watchlist.read_text(encoding="utf-8")).get("vehicles", [])
    except FileNotFoundError:
        watch = []
        errors.append("watchlist not found at Market/watchlist.json")
    for v in watch:
        code = v.get("fipe_code", "")
        if not code or code.startswith("000000"):
            continue  # placeholder entry
        try:
            source, ref, value, model = fetch_fipe(code, v["model_year"], client, table)
            label = f"FIPE {v.get('label') or model} {v['model_year']}"
            points.append((source, f"{code}/{v['model_year']}", label, ref, value))
        except Exception as e:
            errors.append(f"FIPE {code}: {e}")

    # The basket: FIPE publishes one table a month, so only missing values are fetched.
    fipe_history = data_dir / "fipe_history.csv"
    basket_rows = []
    try:
        basket = fipe.load_basket()
        if table is not None:
            keys = fipe.history_keys(fipe.read_history(fipe_history))
            rows, basket_errors = fipe.fetch_month(client, basket, table, fipe_month, keys, run_date)
            fipe.append_history(fipe_history, rows)
            errors += basket_errors
        basket_rows = fipe.basket_summary(fipe.read_history(fipe_history), basket, fipe_month) if fipe_month else []
    except Exception as e:
        errors.append(f"FIPE basket: {e}")

    try:
        print(fetch_fleet.update(data_dir))
    except Exception as e:
        errors.append(f"Senatran fleet: {e}")

    # append to history
    new_file = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HISTORY_FIELDS)
        for source, key, label, period, value in points:
            w.writerow([run_date, source, key, label, period, value])

    latest.write_text(render_latest(points, trends, fipe_month, basket_rows,
                                    fetch_fleet.read_fleet(data_dir / "fleet.csv"), errors),
                      encoding="utf-8")

    print(f"{len(points)} data points saved, {len(errors)} errors")
    for e in errors:
        print("  !", e, file=sys.stderr)
    return 0 if points else 1


if __name__ == "__main__":
    sys.exit(main())
