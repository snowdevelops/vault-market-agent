#!/usr/bin/env python3
"""
Daily market data collector for the Business vault.

Pulls official/open data only (no portal scraping):
  - Banco Central SGS: Selic target, IPCA monthly, average auto-loan rate (individuals)
  - Banco Central Focus survey: market median expectation for the Selic
  - FIPE reference prices via BrasilAPI, for the codes listed in watchlist.json

Reads (inside the vault, which is outside this repo):
  Business/Market/watchlist.json     FIPE codes of the vehicles you track

Writes (inside the vault):
  Business/Market/data/history.csv   append-only, one row per data point per run
  Business/Market/data/latest.md     human-readable snapshot, overwritten each run

Standard library only, so nothing to pip install.
Run from anywhere: python3 scripts/fetch_market.py
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

from common import vault_dir

# Banco Central SGS series codes
SGS_SERIES = {
    432: "Selic target (% a.a.)",
    433: "IPCA monthly (%)",
    20749: "Avg auto loan rate, individuals (% a.a.)",
}

HISTORY_FIELDS = ["run_date", "source", "key", "label", "period", "value"]


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


# ---------- sources ----------------------------------------------------------

def fetch_sgs(code, last=1):
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/{last}?formato=json"
    rows = get_json(url)  # [{"data": "16/09/2026", "valor": "13.75"}, ...]
    return [(r["data"], to_float(r["valor"])) for r in rows]


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


def fetch_fipe(code, model_year):
    rows = get_json(f"https://brasilapi.com.br/api/fipe/preco/v1/{code}")
    for r in rows:
        if int(r.get("anoModelo", 0)) == int(model_year):
            return r["mesReferencia"].strip(), to_float(r["valor"]), r.get("modelo", "")
    years = sorted({r.get("anoModelo") for r in rows})
    raise RuntimeError(f"model year {model_year} not found for {code}; available: {years}")


# ---------- main -------------------------------------------------------------

def main():
    vault = vault_dir()
    data_dir = vault / "Business" / "Market" / "data"
    history = data_dir / "history.csv"
    latest = data_dir / "latest.md"
    watchlist = vault / "Business" / "Market" / "watchlist.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    points, errors = [], []

    for code, label in SGS_SERIES.items():
        try:
            for period, value in fetch_sgs(code):
                points.append(("BCB-SGS", str(code), label, period, value))
        except Exception as e:
            errors.append(f"SGS {code} ({label}): {e}")

    try:
        survey_day, medians = fetch_focus_selic()
        for year, value in sorted(medians.items()):
            points.append(("BCB-Focus", f"selic_{year}", f"Focus median Selic end-{year} (% a.a.)",
                           survey_day, value))
    except Exception as e:
        errors.append(f"Focus Selic: {e}")

    try:
        watch = json.loads(watchlist.read_text(encoding="utf-8")).get("vehicles", [])
    except FileNotFoundError:
        watch = []
        errors.append("watchlist not found at Business/Market/watchlist.json")
    for v in watch:
        code = v.get("fipe_code", "")
        if not code or code.startswith("000000"):
            continue  # placeholder entry
        try:
            ref, value, model = fetch_fipe(code, v["model_year"])
            label = f"FIPE {v.get('label') or model} {v['model_year']}"
            points.append(("FIPE-BrasilAPI", f"{code}/{v['model_year']}", label, ref, value))
        except Exception as e:
            errors.append(f"FIPE {code}: {e}")

    # append to history
    new_file = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HISTORY_FIELDS)
        for source, key, label, period, value in points:
            w.writerow([run_date, source, key, label, period, value])

    # overwrite snapshot
    lines = [
        "---",
        "type: market-snapshot",
        f"updated: {datetime.now().isoformat(timespec='minutes')}",
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
    if errors:
        lines += ["", "## Fetch errors", ""] + [f"- {e}" for e in errors]
    latest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"{len(points)} data points saved, {len(errors)} errors")
    for e in errors:
        print("  !", e, file=sys.stderr)
    return 0 if points else 1


if __name__ == "__main__":
    sys.exit(main())
