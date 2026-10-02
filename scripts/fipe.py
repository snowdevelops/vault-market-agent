"""
FIPE reference prices from the official FIPE site (veiculos.fipe.org.br).

The site's own pages load their data from a public JSON API; this module calls
the same endpoints:
  ConsultarTabelaDeReferencia        list of monthly tables (code 338 = outubro/2026)
  ConsultarAnoModeloPeloCodigoFipe   model years (and fuel codes) of a FIPE code
  ConsultarValorComTodosParametros   price of code + model year + fuel in a table
Older tables stay available, so past months can be loaded too (backfill_fipe.py).
The API is not formally documented and answers HTTP 429 when called too fast, so
calls are spaced and retried with growing pauses.

Used by fetch_market.py (daily) and backfill_fipe.py (one time). The basket of
popular models lives in config/fipe-basket.json. Standard library only.
"""
import csv
import json
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import REPO_DIR

BASE = "https://veiculos.fipe.org.br/api/veiculos/"
HEADERS = {"Referer": "https://veiculos.fipe.org.br/", "User-Agent": "vault-market-fetch/1.0"}
CAR = 1
ZERO_KM_YEAR = 32000          # FIPE's model year for new cars
BASKET_FILE = REPO_DIR / "config" / "fipe-basket.json"
HISTORY_FIELDS = ["reference_month", "table_code", "fipe_code", "model_year", "fuel", "label", "value", "fetched_on"]
MONTHS = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
          "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}


class FipeError(RuntimeError):
    pass


def _plain(text):
    text = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def parse_month(label):
    """'outubro/2026 ' or 'outubro de 2026 ' -> '2026-10'."""
    words = _plain(label).replace("/", " ").split()
    words = [w for w in words if w != "de"]
    if len(words) != 2 or words[0] not in MONTHS or not words[1].isdigit():
        raise FipeError(f"unexpected FIPE month: {label!r}")
    return f"{int(words[1]):04d}-{MONTHS[words[0]]:02d}"


def parse_tables(rows):
    """[(table code, 'YYYY-MM')], newest first."""
    if not isinstance(rows, list) or not rows:
        raise FipeError("FIPE returned no reference tables")
    tables = [(int(r["Codigo"]), parse_month(r["Mes"])) for r in rows]
    return sorted(tables, key=lambda t: t[1], reverse=True)


def parse_years(rows):
    """[(model year, fuel code)] from values like '2022-5'; new cars (32000) left out."""
    if isinstance(rows, dict) and rows.get("erro"):
        raise FipeError(f"FIPE: {rows.get('erro')}")
    out = []
    for r in rows:
        year, fuel = str(r["Value"]).split("-", 1)
        if int(year) != ZERO_KM_YEAR:
            out.append((int(year), int(fuel)))
    return out


def to_float(text):
    """'R$ 75.227,00' -> 75227.0"""
    return float(str(text).replace("R$", "").strip().replace(".", "").replace(",", "."))


def parse_price(data):
    if not isinstance(data, dict) or data.get("erro") or "Valor" not in data:
        raise FipeError(f"FIPE: {data.get('erro') if isinstance(data, dict) else 'bad answer'}")
    return {
        "value": to_float(data["Valor"]),
        "month": parse_month(data["MesReferencia"]),
        "model": " ".join(str(data.get("Modelo", "")).split()),
        "fipe_code": data.get("CodigoFipe", ""),
        "model_year": int(data.get("AnoModelo", 0)),
    }


def http_post(url, data, headers, timeout):
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class Client:
    """Spaced, retrying calls to the FIPE API. `post` is injectable for tests."""

    def __init__(self, post=http_post, min_interval=1.5, backoff=(30, 60, 120, 240),
                 sleep=time.sleep, clock=time.monotonic, timeout=30):
        self._post = post
        self.min_interval = min_interval
        self.backoff = backoff
        self.sleep = sleep
        self.clock = clock
        self.timeout = timeout
        self._last = None
        self.calls = 0

    def call(self, method, params=None):
        data = urllib.parse.urlencode(params or {}).encode()
        for attempt in range(len(self.backoff) + 1):
            if self._last is not None:
                wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
            self.calls += 1
            try:
                body = self._post(BASE + method, data, dict(HEADERS), self.timeout)
                return json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == len(self.backoff):
                    raise FipeError(f"FIPE {method}: HTTP {e.code}") from None
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                if attempt == len(self.backoff):
                    raise FipeError(f"FIPE {method}: {e}") from None
            self.sleep(self.backoff[attempt])
        raise FipeError(f"FIPE {method}: no answer")

    def tables(self):
        return parse_tables(self.call("ConsultarTabelaDeReferencia"))

    def years(self, fipe_code, table):
        return parse_years(self.call("ConsultarAnoModeloPeloCodigoFipe", {
            "codigoTipoVeiculo": CAR, "codigoTabelaReferencia": table, "modeloCodigoExterno": fipe_code}))

    def price(self, fipe_code, year, fuel, table):
        return parse_price(self.call("ConsultarValorComTodosParametros", {
            "codigoTabelaReferencia": table, "codigoTipoVeiculo": CAR, "anoModelo": year,
            "codigoTipoCombustivel": fuel, "tipoVeiculo": "carro", "modeloCodigoExterno": fipe_code,
            "tipoConsulta": "codigo"}))

    def price_for_year(self, fipe_code, year, table):
        """Price when only code and model year are known (the watchlist)."""
        fuels = [f for y, f in self.years(fipe_code, table) if y == int(year)]
        if not fuels:
            raise FipeError(f"model year {year} not found for {fipe_code}")
        return self.price(fipe_code, year, fuels[0], table)


# ---------- basket and history ---------------------------------------------------

def load_basket(path=BASKET_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))["vehicles"]


def read_history(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def history_keys(rows):
    return {(r["reference_month"], r["fipe_code"], str(r["model_year"])) for r in rows}


def append_history(path, rows):
    path = Path(path)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def missing(basket, keys, month):
    """(vehicle, year) pairs of the basket that have no value for `month` yet."""
    return [(v, y) for v in basket for y in v["years"] if (month, v["fipe_code"], str(y)) not in keys]


def fetch_month(client, basket, table, month, keys, fetched_on, progress=None):
    """Fetch the basket's missing values for one table. Returns (rows, errors)."""
    rows, errors = [], []
    for v, year in missing(basket, keys, month):
        try:
            p = client.price(v["fipe_code"], year, v["fuel"], table)
            if p["month"] != month:
                raise FipeError(f"asked for {month}, got {p['month']}")
            rows.append({"reference_month": month, "table_code": table, "fipe_code": v["fipe_code"],
                         "model_year": year, "fuel": v["fuel"], "label": v["label"],
                         "value": f"{p['value']:.2f}", "fetched_on": fetched_on})
        except FipeError as e:
            errors.append(f"FIPE basket {v['fipe_code']} {year} ({month}): {e}")
        if progress:
            progress()
    return rows, errors


def month_minus(month, n):
    y, m = map(int, month.split("-"))
    m -= n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


def basket_summary(rows, basket, month):
    """One line per basket model year: value in `month`, 12 months before, change %."""
    values = {(r["reference_month"], r["fipe_code"], str(r["model_year"])): float(r["value"]) for r in rows}
    out = []
    for v in basket:
        for y in v["years"]:
            now = values.get((month, v["fipe_code"], str(y)))
            before = values.get((month_minus(month, 12), v["fipe_code"], str(y)))
            change = (now / before - 1) * 100 if now and before else None
            out.append({"label": v["label"], "year": y, "segment": v.get("segment", ""),
                        "now": now, "year_ago": before, "change_pct": change})
    return out
