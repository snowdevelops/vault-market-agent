"""Offline tests for scripts/capture.py and scripts/deals.py: parsing, deal matching,
table and frontmatter edits, atomic writes and undo."""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import capture  # noqa: E402
import deals  # noqa: E402

TODAY = date(2026, 10, 2)
TEMPLATE = (REPO / "vault-template" / "agent" / "Templates" / "vehicle-deal.md").read_text(encoding="utf-8")


def deal_note(status="listed", listed_on="2026-09-20"):
    return (TEMPLATE.replace("status: acquiring ", f"status: {status}    ")
            .replace("listed_on:\n", f"listed_on: {listed_on}\n"))


class NumberParsingTests(unittest.TestCase):
    def test_price_forms(self):
        for text in ("72900", "72.900", "72,9k", "72,9 mil", "R$ 72.900,00", "R$72.900", "72.9k", "72,9K", " 72900 "):
            self.assertEqual(capture.parse_number(text, "price"), 72900, text)

    def test_price_with_cents(self):
        self.assertEqual(capture.parse_number("R$ 72.900,50"), 72900.5)

    def test_km_forms(self):
        for text in ("65000", "65.000", "65mil", "65 mil", "65k", "65.000 km", "65 mil km"):
            self.assertEqual(capture.parse_number(text, "km"), 65000, text)
        self.assertEqual(capture.parse_number("0", "km"), 0)

    def test_invalid_input(self):
        for text in ("", "abc", "72,900", "7.29.00", "72 900", "-5000", "1.2.3k", "R$", "72,9"):
            with self.assertRaises(capture.CaptureError, msg=text):
                capture.parse_number(text, "price")

    def test_out_of_range(self):
        with self.assertRaises(capture.CaptureError):
            capture.parse_number("999", "price")
        with self.assertRaises(capture.CaptureError):
            capture.parse_number("9.000 mil", "km")

    def test_error_message_shows_accepted_forms(self):
        with self.assertRaises(capture.CaptureError) as ctx:
            capture.parse_number("setenta mil")
        self.assertIn("72,9k", str(ctx.exception))

    def test_format(self):
        self.assertEqual(capture.format_number(72900), "R$ 72.900")
        self.assertEqual(capture.format_number(72900.5), "R$ 72.900,50")
        self.assertEqual(capture.format_number(65000, "km"), "65.000 km")


class OtherParsingTests(unittest.TestCase):
    def test_year(self):
        self.assertEqual(capture.parse_year("2019", TODAY), "2019")
        self.assertEqual(capture.parse_year("2019/2020", TODAY), "2019/2020")
        for bad in ("19", "1890", "2040", "abc"):
            with self.assertRaises(capture.CaptureError):
                capture.parse_year(bad, TODAY)

    def test_date(self):
        self.assertEqual(capture.parse_date("", TODAY), TODAY)
        self.assertEqual(capture.parse_date("Hoje", TODAY), TODAY)
        self.assertEqual(capture.parse_date("ontem", TODAY), date(2026, 10, 1))
        self.assertEqual(capture.parse_date("2026-09-30", TODAY), date(2026, 9, 30))
        self.assertEqual(capture.parse_date("30/09/2026", TODAY), date(2026, 9, 30))
        self.assertEqual(capture.parse_date("30/09", TODAY), date(2026, 9, 30))
        for bad in ("amanhã", "31/02/2026", "03/10/2026"):
            with self.assertRaises(capture.CaptureError):
                capture.parse_date(bad, TODAY)


class SplitFieldsTests(unittest.TestCase):
    NAMES = ["modelo", "ano", "km", "preço", "canal", "observação"]

    def test_all_fields(self):
        self.assertEqual(capture.split_fields(" Onix LT 1.0 ;2019; 65k ; 72,9k ; OLX ; dono ", self.NAMES, 1),
                         ["Onix LT 1.0", "2019", "65k", "72,9k", "OLX", "dono"])

    def test_optional_missing(self):
        self.assertEqual(capture.split_fields("a;b;c;d;e", self.NAMES, 1), ["a", "b", "c", "d", "e", ""])

    def test_extra_separators_stay_in_last_field(self):
        self.assertEqual(capture.split_fields("a;b;c;d;e;nota; com ; pontos", self.NAMES, 1)[-1],
                         "nota ; com ; pontos")

    def test_missing_required(self):
        with self.assertRaises(capture.CaptureError) as ctx:
            capture.split_fields("a;b;c", self.NAMES, 1, usage="USO")
        self.assertIn("preço", str(ctx.exception))
        self.assertIn("USO", str(ctx.exception))

    def test_empty_required(self):
        with self.assertRaises(capture.CaptureError) as ctx:
            capture.split_fields("a; ;c;d;e", self.NAMES, 1)
        self.assertIn("ano", str(ctx.exception))

    def test_nothing(self):
        with self.assertRaises(capture.CaptureError):
            capture.split_fields("", self.NAMES, 1)

    def test_clean_cell(self):
        self.assertEqual(capture.clean_cell(" a | b\nc  "), "a / b c")


class DealMatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.deals = Path(self.tmp.name)
        for name in ("Ônix-2019", "onix-2020", "gol-g5", "Strada Freedom"):
            (self.deals / f"{name}.md").write_text("x")
        (self.deals / ".hidden.md").write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_accents_and_case(self):
        path, _ = deals.find_deal(self.deals, "ONIX 2019")
        self.assertEqual(path.name, "Ônix-2019.md")

    def test_separators(self):
        self.assertEqual(deals.find_deal(self.deals, "strada-freedom")[0].name, "Strada Freedom.md")
        self.assertEqual(deals.find_deal(self.deals, "gol")[0].name, "gol-g5.md")

    def test_ambiguous(self):
        path, candidates = deals.find_deal(self.deals, "onix")
        self.assertIsNone(path)
        self.assertEqual(sorted(p.name for p in candidates), ["onix-2020.md", "Ônix-2019.md"])

    def test_exact_beats_partial(self):
        (self.deals / "gol.md").write_text("x")
        self.assertEqual(deals.find_deal(self.deals, "Gol")[0].name, "gol.md")

    def test_no_match_lists_all(self):
        path, candidates = deals.find_deal(self.deals, "hb20")
        self.assertIsNone(path)
        self.assertEqual(len(candidates), 4)

    def test_missing_folder(self):
        self.assertEqual(deals.find_deal(self.deals / "nope", "x"), (None, []))


class TableTests(unittest.TestCase):
    def test_appends_to_existing_tables_and_keeps_the_rest(self):
        text = capture.add_table_row(TEMPLATE, capture.LEADS_LOG, ["2026-10-02", "OLX", "oferta", 68000, ""])
        text = capture.add_table_row(text, capture.LEADS_LOG, ["2026-10-03", "OLX", "visita", "", "test drive"])
        text = capture.add_table_row(text, capture.PRICE_LOG, ["2026-10-03", 71500, "dia 15"])
        added = [l for l in text.splitlines() if l not in TEMPLATE.splitlines()]
        self.assertEqual(added, ["| 2026-10-03 | 71500 | dia 15 |",
                                 "| 2026-10-02 | OLX | oferta | 68000 |  |",
                                 "| 2026-10-03 | OLX | visita |  | test drive |"])
        self.assertEqual([l for l in text.splitlines() if l not in added], TEMPLATE.splitlines())
        lines = text.splitlines()
        self.assertEqual(lines.index("| 2026-10-03 | 71500 | dia 15 |") - 3, lines.index("## Histórico de preço"))

    def test_section_without_table_gets_header(self):
        note = "---\nstatus: listed\n---\n# Carro\n\n## Histórico de preço\n\n## Resultado e lições\n- x\n"
        text = capture.add_table_row(note, capture.PRICE_LOG, ["2026-10-02", 70000, "início"])
        self.assertIn("## Histórico de preço\n| Data | Preço | Motivo |\n|---|---|---|\n"
                      "| 2026-10-02 | 70000 | início |\n\n## Resultado e lições", text)

    def test_missing_section_is_added_before_the_next_template_section(self):
        note = TEMPLATE.replace("## Histórico de preço\n| Data | Preço | Motivo |\n|---|---|---|\n\n", "")
        text = capture.add_table_row(note, capture.PRICE_LOG, ["2026-10-02", 70000, "início"])
        self.assertIn("## Histórico de preço\n| Data | Preço | Motivo |\n|---|---|---|\n"
                      "| 2026-10-02 | 70000 | início |\n\n## Registro de contatos", text)
        self.assertEqual(text.replace("## Histórico de preço\n| Data | Preço | Motivo |\n|---|---|---|\n"
                                      "| 2026-10-02 | 70000 | início |\n\n", ""), note)

    def test_missing_leads_section_in_minimal_note_goes_to_the_end(self):
        note = "---\nstatus: listed\n---\n# Carro\nTexto sem quebra final"
        text = capture.add_table_row(note, capture.LEADS_LOG, ["2026-10-02", "OLX", "pergunta", "", ""])
        self.assertTrue(text.startswith(note + "\n\n## Registro de contatos\n| Data | Canal |"))
        self.assertTrue(text.endswith("| 2026-10-02 | OLX | pergunta |  |  |\n"))

    def test_english_headings_of_older_notes(self):
        note = (REPO / "examples" / "example-deal.md").read_text(encoding="utf-8")
        text = capture.add_table_row(note, capture.PRICE_LOG, ["2026-02-01", 50500, "x"])
        self.assertIn("| 2026-01-25 | 50900 | day 15 review: views ok, no visits |\n| 2026-02-01 | 50500 | x |\n", text)
        self.assertNotIn("Histórico de preço", text)

    def test_crlf_note_stays_crlf(self):
        note = TEMPLATE.replace("\n", "\r\n")
        text = capture.add_table_row(note, capture.PRICE_LOG, ["2026-10-02", 70000, "início"])
        self.assertIn("|---|---|---|\r\n| 2026-10-02 | 70000 | início |\r\n", text)
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_pipes_cannot_break_the_table(self):
        text = capture.add_table_row(TEMPLATE, capture.PRICE_LOG, ["2026-10-02", 70000, "a | b"])
        self.assertIn("| 2026-10-02 | 70000 | a / b |", text)


class FrontmatterTests(unittest.TestCase):
    def test_read(self):
        info = deals.read_frontmatter(deal_note())
        self.assertEqual(info["status"], "listed")
        self.assertEqual(info["docs_cost"], "")
        self.assertEqual(info["listed_on"], "2026-09-20")
        self.assertEqual(info["sale_channel"], "")

    def test_quoted_values_with_hash(self):
        info = deals.read_frontmatter('---\nvehicle: "Onix #1"   # nota\nx: \'a\'\n---\n')
        self.assertEqual(info, {"vehicle": "Onix #1", "x": "a"})

    def test_sale_update_changes_only_those_lines(self):
        note = deal_note()
        text = deals.set_frontmatter(note, {"status": "sold", "sold_price": "70000",
                                            "sold_on": "2026-10-02", "sale_channel": '"OLX"'})
        changed = [(a, b) for a, b in zip(note.splitlines(), text.splitlines()) if a != b]
        self.assertEqual(changed, [
            ("status: listed           # acquiring | reconditioning | listed | sold | dropped (mantenha em inglês)",
             "status: sold             # acquiring | reconditioning | listed | sold | dropped (mantenha em inglês)"),
            ("sold_on:", "sold_on: 2026-10-02"),
            ("sold_price:", "sold_price: 70000"),
            ('sale_channel: ""', 'sale_channel: "OLX"'),
        ])
        self.assertEqual(len(text.splitlines()), len(note.splitlines()))

    def test_missing_keys_are_added_before_closing_line(self):
        text = deals.set_frontmatter("---\nstatus: listed\n---\n# Carro\n", {"status": "sold", "sold_on": "2026-10-02"})
        self.assertEqual(text, "---\nstatus: sold\nsold_on: 2026-10-02\n---\n# Carro\n")

    def test_empty_value_with_comment(self):
        text = deals.set_frontmatter("---\ndocs_cost:               # débitos\n---\n", {"docs_cost": "1800"})
        self.assertEqual(text, "---\ndocs_cost: 1800          # débitos\n---\n")

    def test_no_frontmatter(self):
        with self.assertRaises(ValueError):
            deals.set_frontmatter("# Carro\n", {"status": "sold"})

    def test_yaml_value(self):
        self.assertEqual(deals.yaml_value(70000), "70000")
        self.assertEqual(deals.yaml_value(date(2026, 10, 2)), "2026-10-02")
        self.assertEqual(deals.yaml_value('OLX "loja"'), '"OLX \\"loja\\""')
        info = deals.read_frontmatter(f"---\nsale_channel: {deals.yaml_value('OLX \"loja\"')}\n---\n")
        self.assertEqual(info["sale_channel"], 'OLX "loja"')


class AtomicWriteTests(unittest.TestCase):
    def test_writes_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "f.csv"
            capture.atomic_write(path, b"a\n")
            capture.atomic_write(path, b"b\n")
            self.assertEqual(path.read_bytes(), b"b\n")
            self.assertEqual(os.listdir(path.parent), ["f.csv"])

    def test_failure_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.md"
            path.write_bytes(b"old")
            with mock.patch("capture.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    capture.atomic_write(path, b"new")
            self.assertEqual(path.read_bytes(), b"old")
            self.assertEqual(os.listdir(d), ["f.md"])

    def test_keeps_file_mode(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.md"
            path.write_bytes(b"old")
            path.chmod(0o640)
            capture.atomic_write(path, b"new")
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)


class CaptureBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / "Business"
        (self.work / "Deals").mkdir(parents=True)
        self.note = self.work / "Deals" / "onix-2019.md"
        self.note.write_text(deal_note(), encoding="utf-8")
        (self.work / "Deals" / "gol-2015.md").write_text(deal_note(), encoding="utf-8")
        self.journal = Path(self.tmp.name) / "state" / "captures.json"
        self.cap = capture.Capture(self.work, self.journal, today=TODAY)

    def tearDown(self):
        self.tmp.cleanup()


class CaptureTests(CaptureBase):
    def test_comp_creates_csv_with_header_then_appends(self):
        reply = self.cap.comp("Onix LT 1.0 ; 2019 ; 65mil ; 72,9k ; OLX ; único dono")
        self.cap.comp("HB20 1.0 ; 2020/2021 ; 40.000 km ; R$ 69.500,00 ; Webmotors")
        with open(self.work / "Market" / "comparables.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows, [
            ["date", "model", "year", "km", "price", "channel", "note"],
            ["2026-10-02", "Onix LT 1.0", "2019", "65000", "72900", "OLX", "único dono"],
            ["2026-10-02", "HB20 1.0", "2020/2021", "40000", "69500", "Webmotors", ""],
        ])
        for part in ("Onix LT 1.0", "65.000 km", "R$ 72.900", "OLX", "/desfazer"):
            self.assertIn(part, reply)

    def test_comp_bad_number_writes_nothing(self):
        with self.assertRaises(capture.CaptureError):
            self.cap.comp("Onix ; 2019 ; 65mil ; setenta ; OLX")
        self.assertFalse((self.work / "Market").exists())
        self.assertEqual(self.cap.load_journal(), [])

    def test_lead_and_preco(self):
        reply = self.cap.lead("onix ; Marketplace ; Oferta ; 68 mil ; à vista")
        self.assertIn("R$ 68.000", reply)
        self.cap.preco("ONIX-2019 ; 71.500 ; dia 15 sem visitas")
        text = self.note.read_text(encoding="utf-8")
        self.assertIn("| 2026-10-02 | Marketplace | oferta | 68000 | à vista |", text)
        self.assertIn("| 2026-10-02 | 71500 | dia 15 sem visitas |", text)

    def test_lead_type_is_checked(self):
        with self.assertRaises(capture.CaptureError) as ctx:
            self.cap.lead("onix ; OLX ; ligação")
        self.assertIn("pergunta, visita ou oferta", str(ctx.exception))

    def test_ambiguous_or_unknown_deal_writes_nothing(self):
        (self.work / "Deals" / "onix-2020.md").write_text(deal_note(), encoding="utf-8")
        before = self.note.read_text(encoding="utf-8")
        with self.assertRaises(capture.CaptureError) as ctx:
            self.cap.preco("onix ; 70000 ; x")
        self.assertIn("Mais de um", str(ctx.exception))
        self.assertIn("onix-2020", str(ctx.exception))
        with self.assertRaises(capture.CaptureError) as ctx:
            self.cap.preco("hb20 ; 70000 ; x")
        self.assertIn("Nenhum", str(ctx.exception))
        self.assertIn("gol-2015", str(ctx.exception))
        self.assertEqual(self.note.read_text(encoding="utf-8"), before)
        self.assertEqual(self.cap.load_journal(), [])

    def test_venda_sets_frontmatter(self):
        deal, reply = self.cap.venda("onix ; R$ 70.000,00 ; OLX ; 30/09")
        self.assertEqual(deal, "onix-2019")
        info = deals.read_frontmatter(self.note.read_text(encoding="utf-8"))
        self.assertEqual((info["status"], info["sold_price"], info["sold_on"], info["sale_channel"]),
                         ("sold", "70000", "2026-09-30", "OLX"))
        self.assertIn("30/09/2026", reply)

    def test_venda_defaults_to_today_and_refuses_twice(self):
        self.cap.venda("onix ; 70000 ; OLX")
        self.assertEqual(deals.read_frontmatter(self.note.read_text(encoding="utf-8"))["sold_on"], "2026-10-02")
        with self.assertRaises(capture.CaptureError):
            self.cap.venda("onix ; 71000 ; OLX")

    def test_nota_creates_file_then_appends(self):
        self.cap.nota("Leilão de sexta\ncom muitos Onix")
        self.cap.nota("Loja X baixou preços")
        text = (self.work / "Market" / "field-notes.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(capture.FIELD_NOTES_HEAD))
        self.assertTrue(text.endswith("- 2026-10-02: Leilão de sexta com muitos Onix\n"
                                      "- 2026-10-02: Loja X baixou preços\n"))
        with self.assertRaises(capture.CaptureError):
            self.cap.nota("   ")

    def test_journal_keeps_only_recent_entries(self):
        for i in range(capture.JOURNAL_MAX + 5):
            self.cap.nota(f"nota {i}")
        entries = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(len(entries), capture.JOURNAL_MAX)
        self.assertEqual(entries[-1]["summary"], "nota de campo")


class UndoTests(CaptureBase):
    def test_undo_each_capture_type_in_reverse_order(self):
        original = self.note.read_bytes()
        self.cap.comp("Onix ; 2019 ; 65k ; 72k ; OLX")
        comps_after_first = (self.work / "Market" / "comparables.csv").read_bytes()
        self.cap.comp("Gol ; 2015 ; 120k ; 38k ; OLX")
        self.cap.nota("primeira")
        self.cap.lead("onix ; OLX ; pergunta")
        self.cap.preco("onix ; 71000 ; ajuste")
        self.cap.venda("onix ; 70000 ; OLX")

        self.assertIn("venda de onix-2019", self.cap.undo())
        self.assertEqual(deals.read_frontmatter(self.note.read_text(encoding="utf-8"))["status"], "listed")
        self.assertIn("preço de onix-2019", self.cap.undo())
        self.assertNotIn("| 71000 |", self.note.read_text(encoding="utf-8"))
        self.assertIn("contato em onix-2019", self.cap.undo())
        self.assertEqual(self.note.read_bytes(), original)
        self.assertIn("nota de campo", self.cap.undo())
        self.assertFalse((self.work / "Market" / "field-notes.md").exists())
        self.assertIn("comparável Gol", self.cap.undo())
        self.assertEqual((self.work / "Market" / "comparables.csv").read_bytes(), comps_after_first)
        self.cap.undo()
        self.assertFalse((self.work / "Market" / "comparables.csv").exists())
        self.assertIn("Não há nenhuma captura", self.cap.undo())

    def test_undo_refuses_when_file_changed_since(self):
        self.cap.preco("onix ; 71000 ; ajuste")
        edited = self.note.read_text(encoding="utf-8") + "\nNota do dono.\n"
        self.note.write_text(edited, encoding="utf-8")
        reply = self.cap.undo()
        self.assertIn("mudou depois", reply)
        self.assertEqual(self.note.read_text(encoding="utf-8"), edited)
        self.assertEqual(self.cap.load_journal(), [])

    def test_undo_venda_mentions_existing_review(self):
        self.cap.venda("onix ; 70000 ; OLX")
        (self.work / "Reviews").mkdir()
        (self.work / "Reviews" / "onix-2019.md").write_text("x")
        self.assertIn("Reviews/onix-2019.md", self.cap.undo())

    def test_damaged_journal(self):
        self.journal.parent.mkdir(parents=True)
        self.journal.write_text("{oops")
        self.assertIn("Não há nenhuma captura", self.cap.undo())
        self.journal.write_text(json.dumps([{"kind": "nota", "path": "../../etc/passwd", "after_sha256": "x"}]))
        self.assertIn("danificado", self.cap.undo())


class VaultCommitTests(unittest.TestCase):
    def git(self, vault, *args):
        return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, check=True).stdout

    def test_capture_commits_in_the_vault_repo(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d) / "vault"
            work = vault / "Business"
            (work / "Deals").mkdir(parents=True)
            (work / ".last_research.log").write_text("log")
            self.git(vault, "init", "-q")
            self.git(vault, "config", "user.email", "test@example.com")
            self.git(vault, "config", "user.name", "test")
            cap = capture.Capture(work, Path(d) / "captures.json", vault=vault, today=TODAY)
            cap.nota("algo")
            self.assertEqual(self.git(vault, "log", "--format=%s").split("\n")[0], "capture: nota")
            files = self.git(vault, "show", "--name-only", "--format=").split()
            self.assertEqual(files, ["Business/Market/field-notes.md"])
            cap.undo()
            self.assertEqual(self.git(vault, "log", "--format=%s").split("\n")[0], "capture: desfazer nota")

    def test_no_repo_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(capture.commit_vault(Path(d), Path(d) / "Business", "capture: x"))
            self.assertFalse(capture.commit_vault(None, Path(d), "capture: x"))


class OpenDealsTests(unittest.TestCase):
    def test_lists_open_deals_with_days(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "a.md").write_text(deal_note("listed", "2026-09-20"))
            (d / "b.md").write_text(deal_note("sold", "2026-09-01"))
            (d / "c.md").write_text(deal_note("dropped", "2026-09-01"))
            (d / "d.md").write_text(TEMPLATE)
            items = deals.open_deals(d, TODAY)
        self.assertEqual(items, [
            {"name": "a", "status": "listed", "days_listed": 12, "max_days_listed": "45"},
            {"name": "d", "status": "acquiring", "days_listed": None, "max_days_listed": "45"},
        ])


if __name__ == "__main__":
    unittest.main()
