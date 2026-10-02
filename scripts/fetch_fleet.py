#!/usr/bin/env python3
"""
Monthly vehicle fleet of Uberlândia by vehicle type, from Senatran.

Senatran publishes "Frota por Município e Tipo" every month as an XLSX file only,
linked from https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-Senatran/frota-de-veiculos-<year>.
File names change between years (FrotaporMunicipioetipoDEZEMBRO2025.xlsx,
Frota_por_municipio_e_tipo_Agosto_2026.xlsx), so links are matched loosely. The
sheet has a header row (UF, MUNICIPIO, TOTAL, AUTOMOVEL, ...) and one row per
municipality, without IBGE codes, so the city is matched by UF and name.
The XLSX is read with zipfile and xml.etree.

  python3 scripts/fetch_fleet.py      fetch the newest month if it is not recorded yet

fetch_market.py calls update() every day; it returns at once while last month is
already recorded, so the page is checked daily only until the new month appears
and each spreadsheet (about 0.7 MB) is downloaded once.

Writes Market/data/fleet.csv in the agent folder: month, uf, municipio, ibge_code,
vehicle_type, count. Standard library only.
"""
import csv
import io
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

PAGE = "https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-Senatran/frota-de-veiculos-{year}"
UF = "MG"
MUNICIPIO = "UBERLANDIA"
# Verified at https://servicodados.ibge.gov.br/api/v1/localidades/municipios/3170206
IBGE_CODE = "3170206"
FLEET_FIELDS = ["month", "uf", "municipio", "ibge_code", "vehicle_type", "count"]
MONTHS = {"janeiro": 1, "fevereiro": 2, "marco": 3, "maro": 3, "abril": 4, "maio": 5, "junho": 6,
          "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
# Seen on the pages: Frota_por_municipio_e_tipo_Agosto_2026, FrotaporMunicpioeTipoMaio2025,
# copy2_of_Frota_por_municipio_tipo_Maro_2025 ('ç' dropped), frota-por-municipio-e-tipo-janeiro-2026.
LINK_NAME = re.compile(r"frotapormunici?pioe?tipo(" + "|".join(MONTHS) + r")(\d{4})")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_XLSX = 20 * 1024 * 1024


def plain(text):
    """Upper case without accents: 'Uberlândia' -> 'UBERLANDIA'."""
    text = unicodedata.normalize("NFKD", str(text))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).upper().split())


def http_get(url, timeout=60, limit=MAX_XLSX):
    req = urllib.request.Request(url, headers={"User-Agent": "vault-market-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError(f"{url} is larger than {limit} bytes")
    return data


def find_links(html, page_url):
    """{'YYYY-MM': url} of the 'Frota por Município e Tipo' spreadsheets on a page."""
    out = {}
    for href in re.findall(r'href="([^"]+\.xlsx)"', html, re.I):
        url = urllib.parse.urljoin(page_url, href)
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        key = re.sub(r"[^a-z0-9]", "", plain(name).lower())
        m = LINK_NAME.search(key)
        if m:
            out.setdefault(f"{m.group(2)}-{MONTHS[m.group(1)]:02d}", url)
    return out


def read_xlsx_rows(data):
    """Rows of the first worksheet as lists of strings (empty cells as '')."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    sheet_path = "xl/worksheets/sheet1.xml"
    shared_path = "xl/sharedStrings.xml"
    if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
        rels = {r.get("Id"): r.get("Target") for r in
                ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(f"{PKG_REL_NS}Relationship")}
        first = ET.fromstring(z.read("xl/workbook.xml")).find(f"{NS}sheets/{NS}sheet")
        target = rels.get(first.get(f"{REL_NS}id")) if first is not None else None
        if target:
            sheet_path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        for target in rels.values():
            if target and target.endswith("sharedStrings.xml"):
                shared_path = target.lstrip("/") if target.startswith("/") else "xl/" + target
    shared = []
    if shared_path in names:
        for si in ET.fromstring(z.read(shared_path)).iter(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    rows = []
    for row in ET.fromstring(z.read(sheet_path)).iter(f"{NS}row"):
        values, idx = [], -1
        for c in row.iter(f"{NS}c"):
            m = re.match(r"[A-Z]+", c.get("r") or "")
            idx = _col_index(m.group(0)) if m else idx + 1   # cells may skip empty columns
            v = c.find(f"{NS}v")
            if c.get("t") == "s" and v is not None:
                value = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{NS}t"))
            else:
                value = v.text if v is not None and v.text is not None else ""
            values += [""] * (idx + 1 - len(values))
            values[idx] = value
        rows.append(values)
    return rows


def _col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def city_fleet(rows, uf=UF, municipio=MUNICIPIO):
    """{vehicle type: count} for one municipality, using the sheet's header row."""
    header = None
    for row in rows:
        names = [plain(c) for c in row]
        if header is None:
            if "UF" in names and "MUNICIPIO" in names:
                header = names
            continue
        if len(row) < 2:
            continue
        cells = dict(zip(header, row))
        if plain(cells.get("UF", "")) == uf and plain(cells.get("MUNICIPIO", "")) == plain(municipio):
            out = {}
            for name, value in cells.items():
                if name in ("UF", "MUNICIPIO") or not name:
                    continue
                try:
                    out[name] = int(float(value))
                except ValueError:
                    continue
            return out
    if header is None:
        raise RuntimeError("fleet sheet has no UF/MUNICIPIO header row")
    raise RuntimeError(f"{municipio}/{uf} not found in the fleet sheet")


def read_fleet(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_fleet(path, month, fleet):
    path = Path(path)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(FLEET_FIELDS)
        for vehicle_type, count in fleet.items():
            w.writerow([month, UF, MUNICIPIO, IBGE_CODE, vehicle_type, count])


def previous_month(today):
    return f"{today.year - 1}-12" if today.month == 1 else f"{today.year}-{today.month - 1:02d}"


def month_minus_12(month):
    y, m = month.split("-")
    return f"{int(y) - 1}-{m}"


def update(data_dir, today=None, get=http_get):
    """Record the newest month (and, on the first run, the same month a year
    earlier, so a 12-month change exists from day one). Returns a short message."""
    today = today or date.today()
    path = Path(data_dir) / "fleet.csv"
    recorded = {r["month"] for r in read_fleet(path)}
    if previous_month(today) in recorded:
        return f"fleet: {previous_month(today)} already recorded"
    links = {}
    for year in (today.year, today.year - 1):
        url = PAGE.format(year=year)
        try:
            html = get(url, limit=5 * 1024 * 1024).decode("utf-8", "replace")
        except Exception as e:
            if year == today.year and today.month > 2:
                raise RuntimeError(f"Senatran page {year}: {e}")
            continue
        for month, link in find_links(html, url).items():
            links.setdefault(month, link)
    if not links:
        raise RuntimeError("no 'Frota por Município e Tipo' spreadsheet found on the Senatran pages")
    newest = max(links)
    wanted = [m for m in (newest, month_minus_12(newest) if not recorded else None)
              if m and m in links and m not in recorded]
    if not wanted:
        return f"fleet: newest published month {newest} already recorded"
    for month in sorted(wanted):
        fleet = city_fleet(read_xlsx_rows(get(links[month])))
        append_fleet(path, month, fleet)
    return f"fleet: recorded {', '.join(sorted(wanted))}"


def summary(rows):
    """(newest month, {type: count}, {type: count 12 months before} or {})."""
    by_month = {}
    for r in rows:
        try:
            by_month.setdefault(r["month"], {})[r["vehicle_type"]] = int(r["count"])
        except (KeyError, ValueError):
            continue
    if not by_month:
        return None, {}, {}
    newest = max(by_month)
    return newest, by_month[newest], by_month.get(month_minus_12(newest), {})


def main():
    from common import agent_dir
    data_dir = agent_dir() / "Market" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    print(update(data_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
