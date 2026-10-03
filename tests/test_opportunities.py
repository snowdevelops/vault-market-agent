"""Offline tests for scripts/opportunities.py."""
import sys
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import opportunities as op  # noqa: E402

TODAY = date(2026, 10, 2)
HEAD = """---
type: listings
---
# Anúncios do mercado local

Exemplo (não conta como dado): `| 2026-10-02 | Fiat Strada | 2019 | 72.000 | 89.900 | Marketplace | x |`

| Data | Modelo | Ano | Km | Preço | Canal | Obs |
|---|---|---|---|---|---|---|
"""


def note(*rows):
    return HEAD + "".join(f"| {' | '.join(r)} |\n" for r in rows)


def row(model="Fiat Strada", year="2012", price="25.000", seen="2026-10-01", obs="Uberlândia, MG", km=""):
    return (seen, model, year, km, price, "Marketplace", obs)


class ParseTests(unittest.TestCase):
    def test_table_rows_and_brazilian_numbers(self):
        rows = op.parse_listings(note(row(price="R$ 25.900,00", km="120.000", year="2011/2012")))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["model"], r["year"], r["km"], r["price"]), ("strada", 2012, 120000, 25900))
        self.assertEqual(r["seen"], date(2026, 10, 1))

    def test_table_ends_at_first_non_table_line(self):
        text = note(row()) + "\nTexto depois\n| 2026-10-01 | Fiat Palio | 2002 |  | 10.000 | OLX | |\n"
        self.assertEqual(len(op.parse_listings(text)), 1)

    def test_unreadable_values_become_none(self):
        r = op.parse_listings(note(row(price="a combinar", year="?")))[0]
        self.assertIsNone(r["price"])
        self.assertIsNone(r["year"])

    def test_no_table(self):
        self.assertEqual(op.parse_listings("# nada aqui\n"), [])
        self.assertEqual(op.parse_listings(""), [])

    def test_comparables_csv(self):
        text = "date,model,year,km,price,channel,note\n2026-09-30,Onix LT 1.0,2019,65000,72900,OLX,único dono\n"
        r = op.parse_comparables(text)[0]
        self.assertEqual((r["model"], r["year"], r["km"], r["price"], r["Canal"]), ("onix", 2019, 65000, 72900, "OLX"))

    def test_merge_drops_the_same_car_in_both_sources(self):
        listings = op.parse_listings(note(row(km="65.000", price="72.900", year="2019", model="Chevrolet Onix")))
        comps = op.parse_comparables("date,model,year,km,price,channel,note\n"
                                     "2026-09-30,Onix LT,2019,65000,72900,OLX,\n"
                                     "2026-09-30,Onix LT,2019,80000,69900,OLX,\n")
        self.assertEqual(len(op.merge(listings, comps)), 2)


class ModelAndLinkTests(unittest.TestCase):
    def test_model_key(self):
        self.assertEqual(op.model_key("FIAT strada cs 1.4 flex"), "strada")
        self.assertEqual(op.model_key("Chevrolet Ônix Plus LTZ"), "onix plus")
        self.assertEqual(op.model_key("Volkswagen voyage total flex"), "voyage")
        self.assertEqual(op.model_key("Hyundai HB20S"), "hb20s")
        self.assertEqual(op.model_key("Fiat"), "")

    def test_link(self):
        self.assertEqual(op.listing_link("Uberlândia, MG; fb:1438553554906296"),
                         "https://www.facebook.com/marketplace/item/1438553554906296/")
        self.assertEqual(op.listing_link("ver https://olx.com.br/anuncio/123, ótimo"),
                         "https://olx.com.br/anuncio/123")
        self.assertIsNone(op.listing_link("Uberlândia, único dono"))

    def test_clean_obs_hides_the_link(self):
        self.assertEqual(op.clean_obs("Uberlândia, MG; fb:1438553554906296"), "Uberlândia, MG")


class OpportunityTests(unittest.TestCase):
    def find(self, *rows):
        return op.find_opportunities(op.parse_listings(note(*rows)), TODAY)

    def test_more_than_15_percent_below_two_peers(self):
        ops = self.find(row(price="20.000", obs="a"), row(price="26.000", obs="b"), row(year="2013", price="26.000", obs="c"))
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["row"]["price"], 20000)
        self.assertEqual(ops[0]["n_peers"], 2)
        self.assertAlmostEqual(ops[0]["discount"], 1 - 20000 / 26000)

    def test_needs_two_peers_within_two_years(self):
        self.assertEqual(self.find(row(price="20.000"), row(price="26.000", obs="b")), [])
        self.assertEqual(self.find(row(price="20.000"), row(year="2015", price="26.000", obs="b"),
                                   row(year="2016", price="26.000", obs="c")), [])

    def test_other_models_are_not_peers(self):
        self.assertEqual(self.find(row(price="20.000"), row(model="Fiat Palio", price="26.000"),
                                   row(model="Fiat Palio", price="27.000", obs="c")), [])

    def test_exactly_15_percent_is_not_enough(self):
        self.assertEqual(self.find(row(price="21.250"), row(price="25.000", obs="b"), row(price="25.000", obs="c")), [])

    def test_suspicious_prices_are_left_out(self):
        peers = (row(price="26.000", obs="b"), row(price="26.000", obs="c"))
        self.assertEqual(self.find(row(price="9.000"), *peers), [])   # under 40% of the average
        self.assertEqual(self.find(row(price="4.000"), *peers), [])   # under R$ 5.000

    def test_sold_gone_and_old_listings_are_left_out(self):
        peers = (row(price="26.000", obs="b"), row(price="26.000", obs="c"))
        self.assertEqual(self.find(row(price="20.000", obs="Vendido em 05/10"), *peers), [])
        self.assertEqual(self.find(row(price="20.000", obs="saiu do ar"), *peers), [])
        self.assertEqual(self.find(row(price="20.000", seen="2026-08-01"), *peers), [])

    def test_sorted_by_discount_and_capped(self):
        # candidates are peers of each other too, so many full-price peers keep them all below 85%
        rows = [row(price="26.000", obs=f"peer{i}") for i in range(30)]
        rows += [row(price=str(15000 + 100 * i), obs=f"cand{i}") for i in range(12)]
        ops = self.find(*rows)
        self.assertEqual(len(ops), op.MAX_LIST)
        discounts = [o["discount"] for o in ops]
        self.assertEqual(discounts, sorted(discounts, reverse=True))

    def test_keys_are_stable_and_distinct(self):
        a = op.parse_listings(note(row(obs="a"), row(obs="b")))
        b = op.parse_listings(note(row(obs="a"), row(obs="b")))
        self.assertEqual([r["key"] for r in a], [r["key"] for r in b])
        self.assertNotEqual(a[0]["key"], a[1]["key"])
        self.assertLessEqual(len("op:" + a[0]["key"]), 64)   # Telegram's callback_data limit


class FormatTests(unittest.TestCase):
    def test_list_and_detail(self):
        rows = op.parse_listings(note(row(price="20.000", obs="Uberlândia, MG; fb:123456789"),
                                      row(price="26.000", obs="b"), row(year="2013", price="26.000", obs="c")))
        ops = op.find_opportunities(rows, TODAY)
        text = op.format_list(ops)
        self.assertIn("1. Fiat Strada 2012 — R$ 20.000", text)
        self.assertIn("23% abaixo (2 anúncios)", text)
        detail = op.format_detail(ops[0]["row"], rows)
        self.assertIn("23% abaixo da média de R$ 26.000 (2 anúncios de Strada, anos 2012 a 2013)", detail)
        self.assertIn("Obs: Uberlândia, MG", detail)
        self.assertNotIn("fb:", detail)
        self.assertNotIn("Sem link", detail)

    def test_empty_list_explains_the_rule(self):
        self.assertIn("Nenhuma oportunidade", op.format_list([]))

    def test_detail_without_link(self):
        rows = op.parse_listings(note(row(obs="sem link")))
        self.assertIn("Sem link", op.format_detail(rows[0], rows))


if __name__ == "__main__":
    unittest.main()
