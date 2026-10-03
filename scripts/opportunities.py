#!/usr/bin/env python3
"""
Local listings priced well below comparable ones (the bot's /oportunidades).

  python3 scripts/opportunities.py     print the current list from the vault

Reads the owner's Market/listings.md table and Market/comparables.csv together,
dropping a car that appears in both. A listing is an opportunity when its asking
price is more than 15% below the average of at least two other listings of the
same model with a model year within 2 years. Prices that look wrong, listings
marked as sold or gone, and listings seen more than 30 days ago are left out.

The link to open a listing comes from its Obs cell: a full URL, or `fb:` plus the
Marketplace listing number. Read-only; standard library only.
"""
import csv
import hashlib
import io
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

import capture
import deals

LISTINGS = Path("Market") / "listings.md"
LISTINGS_HEADER = ["Data", "Modelo", "Ano", "Km", "Preço", "Canal", "Obs"]

DISCOUNT = 0.15              # more than 15% under comparable listings
MIN_PEERS = 2
YEAR_WINDOW = 2              # comparable: same model, model year within +-2
PRICE_BOUNDS = (5_000, 300_000)
MIN_SHARE_OF_AVG = 0.4       # under 40% of the comparable average looks like a typo or a scam
MAX_AGE_DAYS = 30
MAX_LIST = 10
GONE = ("vendido", "saiu do ar")

BRANDS = {"fiat", "volkswagen", "vw", "chevrolet", "gm", "hyundai", "renault", "toyota",
          "jeep", "ford", "honda", "nissan", "peugeot", "citroen"}
SECOND_WORDS = {"plus", "sedan", "weekend"}   # separate models: Onix Plus, Palio Weekend

URL = re.compile(r"https?://\S+")
FB_ID = re.compile(r"\bfb:(\d{5,25})\b")
MARKETPLACE_ITEM = "https://www.facebook.com/marketplace/item/{}/"


def model_key(name):
    """'FIAT strada cs 1.4 flex' -> 'strada'; 'Chevrolet Ônix Plus' -> 'onix plus'."""
    words = [w for w in re.findall(r"[a-z0-9]+", deals.normalize(name)) if w not in BRANDS]
    if not words:
        return ""
    if len(words) > 1 and words[1] in SECOND_WORDS:
        return f"{words[0]} {words[1]}"
    return words[0]


def listing_link(obs):
    """First URL in the Obs cell, else the Marketplace page for `fb:<number>`, else None."""
    obs = str(obs or "")
    m = URL.search(obs)
    if m:
        return m.group(0).rstrip(").,;")
    m = FB_ID.search(obs)
    return MARKETPLACE_ITEM.format(m.group(1)) if m else None


def clean_obs(obs):
    """Obs without the link, for display."""
    text = FB_ID.sub("", URL.sub("", str(obs or "")))
    return " ".join(text.replace(" ;", ";").split()).strip(" ;,")


def _number(text, kind):
    try:
        return capture.parse_number(text, kind) if str(text or "").strip() else None
    except capture.CaptureError:
        return None


def _year(text):
    years = re.findall(r"(?:19|20)\d\d", str(text or ""))
    return int(years[-1]) if years else None


def _date(text):
    try:
        return date.fromisoformat(str(text or "").strip())
    except ValueError:
        return None


def make_row(cells, source):
    """Row from {column: text} with the listings note's Portuguese column names."""
    row = {k: str(cells.get(k) or "").strip() for k in LISTINGS_HEADER}
    row.update({
        "source": source,
        "model": model_key(row["Modelo"]),
        "year": _year(row["Ano"]),
        "km": _number(row["Km"], "km"),
        "price": _number(row["Preço"], "price"),
        "seen": _date(row["Data"]),
    })
    row["key"] = listing_key(row)
    return row


def listing_key(row):
    """Short stable id for a row, used in the bot's button data."""
    raw = "|".join(row[k] for k in LISTINGS_HEADER)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def parse_listings(text):
    """Rows of the table in Market/listings.md. The table ends at the first line
    that is not a table row."""
    lines = str(text or "").splitlines()
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and cells[:2] == LISTINGS_HEADER[:2]:
            header, body = cells, lines[i + 2:]
            break
    else:
        return []
    rows = []
    for line in body:
        if not line.lstrip().startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(make_row(dict(zip(header, cells)), "listings"))
    return rows


def parse_comparables(text):
    """Rows of Market/comparables.csv (written by /comp), in the same shape."""
    names = dict(zip(capture.COMPARABLES_HEADER, LISTINGS_HEADER))
    reader = csv.DictReader(io.StringIO(str(text or "")))
    return [make_row({names[k]: v for k, v in r.items() if k in names}, "comparables") for r in reader]


def merge(listings, comparables):
    """Both sources, without counting a car twice (same model, year, km and price)."""
    seen = {(r["model"], r["year"], r["km"], r["price"]) for r in listings}
    return listings + [r for r in comparables if (r["model"], r["year"], r["km"], r["price"]) not in seen]


def load_rows(work):
    def read(rel):
        path = Path(work) / rel
        return path.read_text(encoding="utf-8") if path.exists() else ""
    return merge(parse_listings(read(LISTINGS)), parse_comparables(read(capture.COMPARABLES)))


def _usable(row):
    return row["model"] and row["year"] and row["price"] is not None \
        and PRICE_BOUNDS[0] <= row["price"] <= PRICE_BOUNDS[1]


def compare(row, rows):
    """(peer average, number of peers, lowest and highest peer year) or None."""
    peers = [r for r in rows if r is not row and _usable(r) and r["model"] == row["model"]
             and abs(r["year"] - row["year"]) <= YEAR_WINDOW]
    if len(peers) < MIN_PEERS:
        return None
    years = [r["year"] for r in peers]
    return mean(r["price"] for r in peers), len(peers), min(years), max(years)


def find_opportunities(rows, today=None, limit=MAX_LIST):
    today = today or date.today()
    oldest = today - timedelta(days=MAX_AGE_DAYS)
    found = []
    for row in rows:
        if not _usable(row) or not row["seen"] or row["seen"] < oldest:
            continue
        if any(word in deals.normalize(row["Obs"]) for word in GONE):
            continue
        comparison = compare(row, rows)
        if comparison is None:
            continue
        avg = comparison[0]
        if MIN_SHARE_OF_AVG * avg <= row["price"] < (1 - DISCOUNT) * avg:
            found.append(opportunity(row, comparison))
    found.sort(key=lambda op: op["discount"], reverse=True)
    return found[:limit]


def opportunity(row, comparison):
    avg, n, low, high = comparison
    return {"row": row, "avg": avg, "n_peers": n, "years": (low, high),
            "discount": 1 - row["price"] / avg, "key": row["key"]}


def find_row(rows, key):
    return next((r for r in rows if r["key"] == key), None)


def title(row):
    price = capture.format_number(row["price"]) if row["price"] is not None else "sem preço"
    return f"{row['Modelo']} {row['year'] or row['Ano'] or 'ano ?'} — {price}"


def format_list(ops):
    if not ops:
        return ("Nenhuma oportunidade agora: nenhum anúncio dos últimos "
                f"{MAX_AGE_DAYS} dias está mais de {round(DISCOUNT * 100)}% abaixo de pelo menos "
                f"{MIN_PEERS} anúncios do mesmo modelo (ano ±{YEAR_WINDOW}).")
    lines = [f"Oportunidades ({len(ops)}): anúncios mais de {round(DISCOUNT * 100)}% abaixo "
             f"dos parecidos (mesmo modelo, ano ±{YEAR_WINDOW}). Toque num número para ver o anúncio.", ""]
    for i, op in enumerate(ops, 1):
        lines.append(f"{i}. {title(op['row'])}")
        lines.append(f"   {round(op['discount'] * 100)}% abaixo ({op['n_peers']} anúncios)")
    lines += ["", "Preços pedidos, não preços de venda."]
    return "\n".join(lines)


def format_detail(row, rows):
    lines = [title(row)]
    comparison = compare(row, rows) if _usable(row) else None
    if comparison:
        avg, n, low, high = comparison
        diff = 1 - row["price"] / avg
        where = "abaixo" if diff >= 0 else "acima"
        span = f"ano {low}" if low == high else f"anos {low} a {high}"
        lines.append(f"{round(abs(diff) * 100)}% {where} da média de {capture.format_number(round(avg))} "
                     f"({n} anúncios de {row['model'].title()}, {span})")
    if row["km"] is not None:
        lines.append(f"Km: {capture.format_number(row['km'], 'km')}")
    if row["Canal"]:
        lines.append(f"Canal: {row['Canal']}")
    if clean_obs(row["Obs"]):
        lines.append(f"Obs: {clean_obs(row['Obs'])}")
    if row["Data"]:
        lines.append(f"Visto em {row['Data']}")
    lines.append("Preço pedido, não preço de venda.")
    if not listing_link(row["Obs"]):
        lines.append("Sem link: anote a URL do anúncio na coluna Obs para abrir por aqui.")
    return "\n".join(lines)


def main():
    from common import agent_dir
    rows = load_rows(agent_dir())
    print(format_list(find_opportunities(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
