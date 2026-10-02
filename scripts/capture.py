"""
Field data captured from Telegram, written into the agent folder by plain Python.
No LLM is involved, so capturing costs no Claude usage.

Commands (the bot passes the text after the command; fields are separated by ';'):
  comp     modelo ; ano ; km ; preço ; canal ; [observação]  -> Market/comparables.csv
  lead     negócio ; canal ; tipo ; [valor da oferta] ; [obs] -> leads table of Deals/<negócio>.md
  preco    negócio ; novo preço ; motivo                     -> price log of Deals/<negócio>.md
  venda    negócio ; preço final ; canal ; [data]            -> frontmatter of Deals/<negócio>.md
  nota     texto livre                                       -> Market/field-notes.md
  undo     reverts the most recent capture

Every write is atomic (temp file + rename) and changes only what it adds. Each one
is recorded in a small journal (.state/captures.json, gitignored, on the server)
with the file's previous content, so `undo` can restore it exactly. Undo refuses
when the file changed after the capture, so it never overwrites later edits.
Replies are in Portuguese because they go to the owner. Standard library only.
"""
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import deals

JOURNAL_MAX = 20
LEAD_TYPES = ("pergunta", "visita", "oferta")
COMPARABLES = Path("Market") / "comparables.csv"
FIELD_NOTES = Path("Market") / "field-notes.md"
COMPARABLES_HEADER = ["date", "model", "year", "km", "price", "channel", "note"]
FIELD_NOTES_HEAD = ("---\ntype: field-notes\n---\n# Notas de campo\n\n"
                    "O que o dono ouviu em lojas, leilões e de outros revendedores. "
                    "O bot do Telegram acrescenta uma linha por /nota; o agente só lê este arquivo.\n\n")
UNDO_HINT = "Errou? /desfazer desfaz esta captura."

LIMITS = {"price": (1000, 5_000_000), "km": (0, 2_000_000)}
NUMBER_HINT = {
    "price": "Exemplos aceitos: 72900, 72.900, 72,9k, 72,9 mil, R$ 72.900,00",
    "km": "Exemplos aceitos: 65000, 65.000, 65mil, 65k, 65 mil km",
}

USAGE = {
    "comp": "/comp modelo ; ano ; km ; preço ; canal ; [observação]\n"
            "ex.: /comp Onix LT 1.0 ; 2019 ; 65mil ; 72,9k ; OLX ; único dono",
    "lead": "/lead negócio ; canal ; tipo ; [valor da oferta] ; [observação]\n"
            "tipo: pergunta, visita ou oferta\n"
            "ex.: /lead onix-2019 ; Marketplace ; oferta ; 68 mil ; quer pagar à vista",
    "preco": "/preco negócio ; novo preço ; motivo\n"
             "ex.: /preco onix-2019 ; 71.500 ; dia 15 sem visitas",
    "venda": "/venda negócio ; preço final ; canal ; [data]\n"
             "ex.: /venda onix-2019 ; R$ 70.000,00 ; OLX ; 02/10/2026",
    "nota": "/nota texto livre\n"
            "ex.: /nota Leilão de sexta teve muitos Onix 2019 com sinistro",
}


class Table:
    """A table in a deal note: the headings that identify its section (Portuguese
    from the template first, English from older notes), its header, and the
    headings it should come before when the section has to be added."""

    def __init__(self, headings, header, before):
        self.headings = headings
        self.header = header
        self.before = before


PRICE_LOG = Table(("Histórico de preço", "Price log"),
                  ["| Data | Preço | Motivo |", "|---|---|---|"],
                  ("Registro de contatos", "Leads log", "Resultado e lições", "Outcome and lessons"))
LEADS_LOG = Table(("Registro de contatos", "Leads log"),
                  ["| Data | Canal | Tipo (pergunta / visita / oferta) | Valor da oferta | Resultado |",
                   "|---|---|---|---|---|"],
                  ("Resultado e lições", "Outcome and lessons"))


class CaptureError(Exception):
    """Bad input or a refused write; the message is shown to the owner as is."""


# ---------- parsing ------------------------------------------------------------

def parse_number(text, kind="price"):
    """Parse Brazilian number forms: 72900, 72.900, 72,9k, 72,9 mil, R$ 72.900,00;
    km also 65mil, 65k and a trailing 'km'. Returns an int, or a float with up to
    two decimals. Raises CaptureError for anything ambiguous or out of range."""
    raw = str(text or "").strip()
    s = raw.lower().replace("r$", "").strip()
    if kind == "km":
        s = re.sub(r"\s*km$", "", s)
    mult = 1
    m = re.fullmatch(r"(.*?)\s*(mil|k)", s)
    if m:
        s, mult = m.group(1).strip(), 1000
    value = None
    if re.fullmatch(r"\d+", s):
        value = Decimal(s)
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):              # 72.900
        value = Decimal(s.replace(".", ""))
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{1,2}", s):      # 72.900,00
        value = Decimal(s.replace(".", "").replace(",", "."))
    elif re.fullmatch(r"\d+,\d{1,2}", s):                    # 72,9 (with k/mil) or 72900,50
        value = Decimal(s.replace(",", "."))
    elif mult > 1 and re.fullmatch(r"\d+\.\d{1,2}", s):      # 72.9k
        value = Decimal(s)
    label = "km" if kind == "km" else "valor"
    if value is None:
        raise CaptureError(f"Não entendi o {label} \"{raw}\". {NUMBER_HINT[kind]}")
    value *= mult
    low, high = LIMITS[kind]
    if not low <= value <= high:
        raise CaptureError(f"O {label} \"{raw}\" ficou fora do esperado ({format_number(value, kind)}). "
                           f"{NUMBER_HINT[kind]}")
    value = value.quantize(Decimal("0.01"))
    return int(value) if value == value.to_integral_value() else float(value)


def format_number(value, kind="price"):
    """72900 -> 'R$ 72.900'; 65000 km -> '65.000 km'."""
    try:
        value = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if value == value.to_integral_value():
        text = f"{int(value):,}".replace(",", ".")
    else:
        text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} km" if kind == "km" else f"R$ {text}"


def parse_year(text, today=None):
    """'2019' or '2019/2020' (fabricação/modelo), kept as text."""
    today = today or date.today()
    s = str(text or "").strip().replace(" ", "")
    m = re.fullmatch(r"(\d{4})(?:/(\d{4}))?", s)
    if m and all(1950 <= int(y) <= today.year + 2 for y in m.groups() if y):
        return s
    raise CaptureError(f"Não entendi o ano \"{text}\". Use 2019 ou 2019/2020.")


def parse_date(text, today=None):
    """'2026-10-02', '02/10/2026', '02/10/26', '02/10', 'hoje' or 'ontem'; never in the future."""
    today = today or date.today()
    word = deals.normalize(text)
    if not word or word == "hoje":
        return today
    if word == "ontem":
        return today - timedelta(days=1)
    s = str(text).strip()
    parsed = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(s, fmt).date()
            break
        except ValueError:
            pass
    if parsed is None and re.fullmatch(r"\d{1,2}/\d{1,2}", s):
        try:
            parsed = datetime.strptime(f"{s}/{today.year}", "%d/%m/%Y").date()
        except ValueError:
            parsed = None
    if parsed is None:
        raise CaptureError(f"Não entendi a data \"{text}\". Use 02/10/2026, 02/10, hoje ou ontem.")
    if parsed > today:
        raise CaptureError(f"A data {parsed:%d/%m/%Y} está no futuro.")
    return parsed


def split_fields(args, names, optional=0, usage=""):
    """Split 'a ; b ; c' into a list of len(names). The last `optional` fields may
    be left out; extra ';' stay in the last field, so a note can contain them."""
    parts = [p.strip() for p in str(args or "").split(";")]
    if parts == [""]:
        parts = []
    if len(parts) > len(names):
        parts = parts[: len(names) - 1] + [" ; ".join(parts[len(names) - 1:])]
    required = len(names) - optional
    missing = [n for n, v in zip(names[:required], parts + [""] * required) if not v]
    if len(parts) < required or missing:
        raise CaptureError(f"Falta: {', '.join(missing or names[len(parts):required])}.\n\nUso:\n{usage}")
    return parts + [""] * (len(names) - len(parts))


def clean_cell(text):
    """One line of text that cannot break a Markdown table."""
    return " ".join(str(text).replace("|", "/").split())


# ---------- writing --------------------------------------------------------------

def atomic_write(path, data):
    """Write bytes so readers (Obsidian, Syncthing, the agent) see the old file or the
    new one, never half of it. The temp file sits next to the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _section_start(lines, headings):
    wanted = {deals.normalize(h) for h in headings}
    for i, line in enumerate(lines):
        if line.startswith("## ") and deals.normalize(line[3:].strip().strip("#")) in wanted:
            return i
    return None


def _section_end(lines, start):
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("# ") or lines[j].startswith("## "):
            return j
    return len(lines)


def add_table_row(text, table, cells):
    """Append a row to `table` in a deal note. Adds the table header when the
    section has none, and the whole section at the template position when it is
    missing. Everything else in the note stays byte-identical."""
    nl = "\r\n" if "\r\n" in text else "\n"
    row = "| " + " | ".join(clean_cell(c) for c in cells) + " |"
    lines = text.splitlines(keepends=True)
    bare = [l.rstrip("\r\n") for l in lines]

    start = _section_start(bare, table.headings)
    if start is not None:
        end = _section_end(bare, start)
        rows = [j for j in range(start + 1, end) if bare[j].lstrip().startswith("|")]
        if rows:
            last = rows[0]
            while last + 1 < end and bare[last + 1].lstrip().startswith("|"):
                last += 1
            at, block = last + 1, [row]
        else:
            at, block = start + 1, table.header + [row]
        if not lines[at - 1].endswith(("\n", "\r")):
            lines[at - 1] += nl
        lines[at:at] = [b + nl for b in block]
        return "".join(lines)

    block = [f"## {table.headings[0]}"] + table.header + [row]
    for heading in table.before:
        at = _section_start(bare, (heading,))
        if at is not None:
            lines[at:at] = [b + nl for b in block] + [nl]
            return "".join(lines)
    if text and not text.endswith(("\n", "\r")):
        text += nl
    return text + (nl if text.strip() else "") + nl.join(block) + nl


def sha256(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def commit_vault(vault, work, message):
    """Commit the agent folder in the vault's local git repo, like run_agent.sh does.
    Arguments go straight to git (no shell) and messages are fixed strings."""
    if not vault:
        return False
    vault, work = Path(vault), Path(work)
    try:
        rel = work.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        return False

    def git(*args):
        return subprocess.run(["git", "-C", str(vault), *args], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60).returncode

    try:
        if git("rev-parse", "--is-inside-work-tree") != 0:
            return False
        git("add", "--", rel, f":(exclude){rel}/.last_*")
        return git("commit", "-qm", message) == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------- the captures ---------------------------------------------------------

class Capture:
    def __init__(self, work, journal_path, vault=None, today=None):
        self.work = Path(work)
        self.journal_path = Path(journal_path)
        self.vault = vault
        self._today = today

    def today(self):
        return self._today or date.today()

    # -- journal

    def load_journal(self):
        try:
            entries = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []

    def save_journal(self, entries):
        data = json.dumps(entries[-JOURNAL_MAX:], ensure_ascii=False, indent=1) + "\n"
        atomic_write(self.journal_path, data.encode("utf-8"))

    def write(self, kind, rel, new_text, summary):
        """Write one file atomically, record it for undo and commit the vault."""
        path = self.work / rel
        before = path.read_bytes() if path.exists() else None
        if before is not None:
            try:
                before.decode("utf-8")
            except UnicodeDecodeError:
                raise CaptureError(f"{rel} não está em UTF-8; não alterei nada.")
        after = new_text.encode("utf-8")
        atomic_write(path, after)
        entries = self.load_journal()
        entries.append({
            "kind": kind, "path": Path(rel).as_posix(), "at": datetime.now().isoformat(timespec="seconds"),
            "existed_before": before is not None,
            "before": before.decode("utf-8") if before is not None else None,
            "after_sha256": sha256(after), "summary": summary,
        })
        self.save_journal(entries)
        commit_vault(self.vault, self.work, f"capture: {kind}")

    def read(self, rel):
        path = self.work / rel
        try:
            return path.read_text(encoding="utf-8") if path.exists() else None
        except UnicodeDecodeError:
            raise CaptureError(f"{Path(rel).as_posix()} não está em UTF-8; não alterei nada.")

    def deal(self, query):
        path, candidates = deals.find_deal(self.work / "Deals", query)
        if path is None:
            names = [p.stem for p in candidates][:20]
            if not names:
                raise CaptureError("Não há nenhuma nota de negócio em Deals/.")
            head = (f"Mais de um negócio combina com \"{query}\"" if any(
                deals.normalize(query) in deals.normalize(n) for n in names)
                else f"Nenhum negócio combina com \"{query}\"")
            raise CaptureError(f"{head}. Nada foi gravado. Negócios:\n" + "\n".join(f"- {n}" for n in names))
        return path

    @staticmethod
    def reply(title, rows):
        lines = [title] + [f"{k}: {v}" for k, v in rows if v not in ("", None)]
        return "\n".join(lines) + f"\n\n{UNDO_HINT}"

    # -- commands

    def comp(self, args):
        model, year, km, price, channel, note = split_fields(
            args, ["modelo", "ano", "km", "preço", "canal", "observação"], 1, USAGE["comp"])
        year, km, price = parse_year(year, self.today()), parse_number(km, "km"), parse_number(price, "price")
        today = self.today().isoformat()
        old = self.read(COMPARABLES)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        if not old:
            w.writerow(COMPARABLES_HEADER)
        w.writerow([today, clean_cell(model), year, km, price, clean_cell(channel), clean_cell(note)])
        prefix = old or ""
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        self.write("comp", COMPARABLES, prefix + buf.getvalue(), f"comparável {clean_cell(model)} {year}")
        return self.reply("Comparável registrado em Market/comparables.csv", [
            ("Data", today), ("Modelo", clean_cell(model)), ("Ano", year), ("Km", format_number(km, "km")),
            ("Preço", format_number(price)), ("Canal", clean_cell(channel)), ("Obs", clean_cell(note))])

    def lead(self, args):
        query, channel, kind, offer, note = split_fields(
            args, ["negócio", "canal", "tipo", "valor da oferta", "observação"], 2, USAGE["lead"])
        kind = deals.normalize(kind)
        if kind not in LEAD_TYPES:
            raise CaptureError(f"O tipo precisa ser pergunta, visita ou oferta (veio \"{kind}\").\n\nUso:\n{USAGE['lead']}")
        offer = parse_number(offer, "price") if offer else ""
        path = self.deal(query)
        today = self.today().isoformat()
        rel = path.relative_to(self.work)
        text = add_table_row(self.read(rel), LEADS_LOG, [today, channel, kind, offer, note])
        self.write("lead", rel, text, f"contato em {path.stem}")
        return self.reply(f"Contato registrado em {rel.as_posix()}", [
            ("Data", today), ("Canal", clean_cell(channel)), ("Tipo", kind),
            ("Oferta", format_number(offer) if offer != "" else ""), ("Obs", clean_cell(note))])

    def preco(self, args):
        query, price, reason = split_fields(args, ["negócio", "novo preço", "motivo"], 0, USAGE["preco"])
        price = parse_number(price, "price")
        path = self.deal(query)
        today = self.today().isoformat()
        rel = path.relative_to(self.work)
        text = add_table_row(self.read(rel), PRICE_LOG, [today, price, reason])
        self.write("preco", rel, text, f"preço de {path.stem}")
        return self.reply(f"Novo preço registrado em {rel.as_posix()}", [
            ("Data", today), ("Preço", format_number(price)), ("Motivo", clean_cell(reason))])

    def venda(self, args):
        query, price, channel, when = split_fields(
            args, ["negócio", "preço final", "canal", "data"], 1, USAGE["venda"])
        price = parse_number(price, "price")
        sold_on = parse_date(when, self.today())
        path = self.deal(query)
        rel = path.relative_to(self.work)
        text = self.read(rel)
        if deals.read_frontmatter(text).get("status", "").strip().lower() == "sold":
            raise CaptureError(f"{path.stem} já está como vendido. Nada foi gravado. "
                               "Se a venda anterior estava errada, use /desfazer logo depois dela ou corrija a nota.")
        try:
            text = deals.set_frontmatter(text, {
                "status": "sold", "sold_price": deals.yaml_value(price),
                "sold_on": deals.yaml_value(sold_on), "sale_channel": deals.yaml_value(clean_cell(channel))})
        except ValueError:
            raise CaptureError(f"{rel.as_posix()} não tem frontmatter (o bloco entre ---). Nada foi gravado.")
        self.write("venda", rel, text, f"venda de {path.stem}")
        return path.stem, self.reply(f"Venda registrada em {rel.as_posix()}", [
            ("Preço final", format_number(price)), ("Canal", clean_cell(channel)),
            ("Data", sold_on.strftime("%d/%m/%Y")), ("Status", "sold")])

    def nota(self, args):
        note = clean_cell(args)
        if not note:
            raise CaptureError(f"Escreva o texto depois do comando.\n\nUso:\n{USAGE['nota']}")
        old = self.read(FIELD_NOTES)
        today = self.today().isoformat()
        prefix = old if old else FIELD_NOTES_HEAD
        if not prefix.endswith("\n"):
            prefix += "\n"
        self.write("nota", FIELD_NOTES, f"{prefix}- {today}: {note}\n", "nota de campo")
        return self.reply("Nota registrada em Market/field-notes.md", [("Data", today), ("Nota", note)])

    def undo(self):
        """Revert the newest capture. Returns the reply text."""
        entries = self.load_journal()
        if not entries:
            return "Não há nenhuma captura para desfazer."
        entry = entries.pop()
        self.save_journal(entries)
        rel = entry.get("path", "")
        path = self.work / rel
        if not rel or ".." in Path(rel).parts or Path(rel).is_absolute():
            return "O registro da última captura está danificado; não desfiz nada."
        current = path.read_bytes() if path.exists() else None
        what = entry.get("summary") or entry.get("kind")
        if sha256(current) != entry.get("after_sha256"):
            return (f"Não desfiz \"{what}\": {rel} mudou depois da captura "
                    "(editado no Obsidian ou por outra captura). Corrija à mão.")
        if entry.get("existed_before"):
            atomic_write(path, (entry.get("before") or "").encode("utf-8"))
        else:
            path.unlink()
        commit_vault(self.vault, self.work, f"capture: desfazer {entry.get('kind')}")
        reply = f"Desfeito: {what} ({rel})."
        if entry.get("kind") == "venda":
            review = self.work / "Reviews" / f"{Path(rel).stem}.md"
            if review.exists():
                reply += f"\nA revisão Reviews/{review.name} já tinha sido escrita; apague-a à mão se quiser."
        return reply
