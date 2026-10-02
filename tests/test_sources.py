"""Offline tests for the data sources: Banco Central SGS, the official FIPE API,
the FIPE basket and backfill, and the Senatran fleet spreadsheet. Every network
answer comes from tests/fixtures/, saved from the real services on 2026-10-02."""
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "scripts"))

import backfill_fipe  # noqa: E402
import fetch_fleet  # noqa: E402
import fetch_market  # noqa: E402
import fipe  # noqa: E402
import notify_telegram  # noqa: E402


def fixture(name):
    return (FIXTURES / name).read_bytes()


def fixture_json(name):
    return json.loads(fixture(name).decode("utf-8"))


class SgsTests(unittest.TestCase):
    def test_new_series_are_configured_as_monthly(self):
        for code in (20673, 21121):
            self.assertIn(code, fetch_market.SGS_SERIES)
            self.assertIn(code, fetch_market.MONTHLY)

    def test_source_url_comment_next_to_each_new_code(self):
        source = (REPO / "scripts" / "fetch_market.py").read_text(encoding="utf-8")
        for code in (20673, 21121):
            self.assertIn(f"https://dadosabertos.bcb.gov.br/dataset/{code}-", source)

    def test_parse_loans_and_default_rate(self):
        loans = fetch_market.parse_sgs(fixture_json("sgs_20673.json"))
        default = fetch_market.parse_sgs(fixture_json("sgs_21121.json"))
        self.assertEqual(len(loans), 13)
        self.assertEqual(loans[-1], ("01/08/2026", 22551.0))
        self.assertEqual(default[-1], ("01/08/2026", 6.57))
        self.assertEqual(default[0], ("01/08/2025", 5.5))

    def test_trend(self):
        period, latest, m3, m12 = fetch_market.trend(fetch_market.parse_sgs(fixture_json("sgs_21121.json")))
        self.assertEqual((period, latest, m12), ("01/08/2026", 6.57, 5.5))
        self.assertIsNotNone(m3)
        self.assertEqual(fetch_market.change(6.57, 5.5), "+1.07")

    def test_trend_with_gaps_and_short_series(self):
        self.assertEqual(fetch_market.trend([("01/01/2026", 1.0), ("01/03/2026", 2.0)]),
                         ("01/03/2026", 2.0, None, None))
        self.assertEqual(fetch_market.trend([("01/01/2026", 1.0), ("01/04/2026", 2.0)])[2], 1.0)
        self.assertIsNone(fetch_market.trend([]))
        self.assertEqual(fetch_market.change(None, 1.0), "-")


class FakeFipe:
    """Answers FIPE API calls from fixtures; records the calls."""

    def __init__(self, fail_first=0, prices=None):
        self.calls = []
        self.fail_first = fail_first
        self.prices = prices or {}

    def __call__(self, url, data, headers, timeout):
        method = url.rsplit("/", 1)[-1]
        params = dict(p.split("=", 1) for p in data.decode().split("&") if p)
        self.calls.append((method, params, headers))
        if self.fail_first:
            self.fail_first -= 1
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b""))
        if method == "ConsultarTabelaDeReferencia":
            return fixture("fipe_tabelas.json")
        if method == "ConsultarAnoModeloPeloCodigoFipe":
            return fixture("fipe_anos_001527-0.json")
        if method == "ConsultarValorComTodosParametros":
            key = (params["modeloCodigoExterno"].replace("%2D", "-"), params["anoModelo"],
                   params["codigoTabelaReferencia"])
            if key in self.prices:
                value, month = self.prices[key]
                return json.dumps({"Valor": value, "MesReferencia": month, "Modelo": "X",
                                   "CodigoFipe": key[0], "AnoModelo": int(key[1])}).encode()
            if params["codigoTipoCombustivel"] != "5":
                return fixture("fipe_erro.json")
            return fixture("fipe_valor_001527-0_2022_t314.json")
        raise AssertionError(method)


def client(post, **kw):
    sleeps = []
    c = fipe.Client(post=post, sleep=sleeps.append, clock=lambda: 0.0, **kw)
    return c, sleeps


class FipeParsingTests(unittest.TestCase):
    def test_months(self):
        self.assertEqual(fipe.parse_month("outubro/2026 "), "2026-10")
        self.assertEqual(fipe.parse_month("março de 2025 "), "2025-03")
        with self.assertRaises(fipe.FipeError):
            fipe.parse_month("2026-10")

    def test_tables(self):
        tables = fipe.parse_tables(fixture_json("fipe_tabelas.json"))
        self.assertEqual(tables[0], (338, "2026-10"))
        self.assertEqual(tables[24], (314, "2024-10"))
        self.assertEqual(len(tables), 30)

    def test_years_skip_new_cars(self):
        years = fipe.parse_years(fixture_json("fipe_anos_001527-0.json"))
        self.assertIn((2022, 5), years)
        self.assertNotIn(32000, [y for y, _ in years])

    def test_price(self):
        p = fipe.parse_price(fixture_json("fipe_valor_001527-0_2022_t314.json"))
        self.assertEqual((p["value"], p["month"], p["fipe_code"], p["model_year"]),
                         (81080.0, "2024-10", "001527-0", 2022))
        self.assertEqual(p["model"], "Strada Freedom 1.3 Flex 8V CS Plus")

    def test_not_found(self):
        with self.assertRaises(fipe.FipeError):
            fipe.parse_price(fixture_json("fipe_erro.json"))


class FipeClientTests(unittest.TestCase):
    def test_lookup_by_code_sends_what_the_site_sends(self):
        fake = FakeFipe()
        c, _ = client(fake)
        p = c.price_for_year("001527-0", 2022, 314)
        self.assertEqual(p["value"], 81080.0)
        method, params, headers = fake.calls[-1]
        self.assertEqual(method, "ConsultarValorComTodosParametros")
        self.assertEqual(params["tipoConsulta"], "codigo")
        self.assertEqual(params["codigoTipoCombustivel"], "5")  # taken from the years list
        self.assertEqual(headers["Referer"], "https://veiculos.fipe.org.br/")

    def test_unknown_year(self):
        c, _ = client(FakeFipe())
        with self.assertRaises(fipe.FipeError):
            c.price_for_year("001527-0", 2015, 338)

    def test_retries_after_too_many_requests(self):
        c, sleeps = client(FakeFipe(fail_first=2), backoff=(30, 60, 120))
        self.assertEqual(c.tables()[0], (338, "2026-10"))
        self.assertEqual(sleeps[:1] + [s for s in sleeps if s >= 30], [30, 30, 60])

    def test_gives_up(self):
        c, _ = client(FakeFipe(fail_first=9), backoff=(1, 1))
        with self.assertRaises(fipe.FipeError):
            c.tables()

    def test_calls_are_spaced(self):
        c, sleeps = client(FakeFipe(), min_interval=1.5)
        c.tables()
        c.tables()
        self.assertEqual(sleeps, [1.5])


class BasketTests(unittest.TestCase):
    def test_basket_file(self):
        basket = fipe.load_basket()
        self.assertTrue(15 <= len(basket) <= 20)
        codes = [v["fipe_code"] for v in basket]
        self.assertEqual(len(codes), len(set(codes)))
        for v in basket:
            self.assertRegex(v["fipe_code"], r"^\d{6}-\d$")
            self.assertTrue(2 <= len(v["years"]) <= 3, v)
            self.assertEqual(v["years"], sorted(set(v["years"])))
            self.assertIsInstance(v["fuel"], int)
            self.assertIn(v["segment"], ("hatch", "sedan", "pickup"))
        self.assertTrue(any("Strada" in v["label"] for v in basket))

    def test_fetch_month_skips_known_values_and_checks_the_month(self):
        basket = [{"label": "Strada", "fipe_code": "001527-0", "fuel": 5, "years": [2021, 2022]}]
        c, _ = client(FakeFipe())
        keys = {("2024-10", "001527-0", "2021")}
        rows, errors = fipe.fetch_month(c, basket, 314, "2024-10", keys, "2026-10-02")
        self.assertEqual(errors, [])
        self.assertEqual([(r["model_year"], r["value"]) for r in rows], [(2022, "81080.00")])
        rows, errors = fipe.fetch_month(c, basket, 314, "2024-11", set(), "2026-10-02")
        self.assertEqual(rows, [])
        self.assertIn("asked for 2024-11, got 2024-10", errors[0])

    def test_history_round_trip_and_summary(self):
        basket = [{"label": "Strada", "segment": "pickup", "fipe_code": "001527-0", "fuel": 5, "years": [2022]}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fipe_history.csv"
            base = {"table_code": 0, "fipe_code": "001527-0", "model_year": 2022, "fuel": 5,
                    "label": "Strada", "fetched_on": "2026-10-02"}
            fipe.append_history(path, [dict(base, reference_month="2025-10", value="80000.00")])
            fipe.append_history(path, [dict(base, reference_month="2026-10", value="75227.00")])
            rows = fipe.read_history(path)
            self.assertEqual(path.read_text().count("reference_month"), 1)
            self.assertEqual(fipe.history_keys(rows), {("2025-10", "001527-0", "2022"),
                                                       ("2026-10", "001527-0", "2022")})
            summary = fipe.basket_summary(rows, basket, "2026-10")
        self.assertEqual(summary[0]["now"], 75227.0)
        self.assertAlmostEqual(summary[0]["change_pct"], -5.966, places=2)
        self.assertEqual(fipe.month_minus("2026-01", 12), "2025-01")
        self.assertEqual(fipe.month_minus("2026-01", 1), "2025-12")

    def test_backfill_is_resumable(self):
        basket = [{"label": "Strada", "fipe_code": "001527-0", "fuel": 5, "years": [2022]}]
        prices = {("001527-0", "2022", str(t)): (f"R$ 7{i}.000,00", m)
                  for i, (t, m) in enumerate([(338, "outubro/2026"), (337, "setembro/2026"), (336, "agosto/2026")])}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fipe_history.csv"
            fake = FakeFipe(prices=prices)
            c, _ = client(fake)
            errors = backfill_fipe.backfill(c, basket, path, 3, "2026-10-02", out=lambda line: None)
            self.assertEqual(errors, [])
            self.assertEqual([r["reference_month"] for r in fipe.read_history(path)],
                             ["2026-10", "2026-09", "2026-08"])
            before = len(fake.calls)
            backfill_fipe.backfill(c, basket, path, 3, "2026-10-02", out=lambda line: None)
            self.assertEqual(len(fake.calls) - before, 1)  # only the table list
            self.assertEqual(len(fipe.read_history(path)), 3)

    def test_backfill_arguments(self):
        self.assertEqual(backfill_fipe.parse_months([]), 24)
        self.assertEqual(backfill_fipe.parse_months(["--months", "6"]), 6)
        with self.assertRaises(SystemExit):
            backfill_fipe.parse_months(["--months", "0"])


class WatchlistFipeTests(unittest.TestCase):
    def test_official_first(self):
        c, _ = client(FakeFipe())
        source, month, value, _ = fetch_market.fetch_fipe("001527-0", 2022, c, 314)
        self.assertEqual((source, month, value), ("FIPE", "2024-10", 81080.0))

    def test_brasilapi_fallback(self):
        c, _ = client(FakeFipe())
        answer = [{"anoModelo": 2015, "mesReferencia": "outubro de 2026 ", "valor": "R$ 30.000,00", "modelo": "X"}]
        with mock.patch.object(fetch_market, "get_json", return_value=answer):
            self.assertEqual(fetch_market.fetch_fipe("001527-0", 2015, c, 338),
                             ("FIPE-BrasilAPI", "2026-10", 30000.0, "X"))

    def test_both_fail(self):
        with mock.patch.object(fetch_market, "get_json", side_effect=OSError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_market.fetch_fipe("001527-0", 2022)
        self.assertIn("BrasilAPI: down", str(ctx.exception))


class FleetTests(unittest.TestCase):
    def links(self, year):
        url = fetch_fleet.PAGE.format(year=year)
        return fetch_fleet.find_links((FIXTURES / f"senatran_frota_{year}.html").read_text(encoding="utf-8"), url)

    def test_links_in_both_naming_styles(self):
        links_2026, links_2025 = self.links(2026), self.links(2025)
        self.assertEqual(sorted(links_2026), [f"2026-{m:02d}" for m in range(1, 9)])
        self.assertEqual(sorted(links_2025), [f"2025-{m:02d}" for m in range(1, 13)])
        self.assertTrue(links_2026["2026-08"].endswith("/Frota_por_municipio_e_tipo_Agosto_2026.xlsx"))
        self.assertTrue(links_2025["2025-12"].endswith("/FrotaporMunicipioetipoDEZEMBRO2025.xlsx"))
        self.assertTrue(links_2025["2025-07"].endswith(".xlsx"))  # not the CSV copy
        for url in list(links_2026.values()) + list(links_2025.values()):
            self.assertNotRegex(url, re.compile("UF_Municipio|uf_e_tipo", re.I))

    def test_odd_names(self):
        html = ('<a href="/x/copy2_of_Frota_por_municipio_tipo_Maro_2025.xlsx">a</a>'
                '<a href="x/FrotaporMunicpioeTipoMaio2025.xlsx">b</a>'
                '<a href="x/frota-por-municipio-e-tipo-janeiro-2026.xlsx">c</a>'
                '<a href="x/D_Frota_por_UF_Municipio_COMBUSTIVEL_Agosto_2026.xlsx">d</a>')
        links = fetch_fleet.find_links(html, "https://www.gov.br/a/page")
        self.assertEqual(sorted(links), ["2025-03", "2025-05", "2026-01"])
        self.assertEqual(links["2025-03"], "https://www.gov.br/x/copy2_of_Frota_por_municipio_tipo_Maro_2025.xlsx")

    def test_reads_the_spreadsheet(self):
        rows = fetch_fleet.read_xlsx_rows(fixture("senatran_frota_municipio_tipo_sample.xlsx"))
        self.assertEqual(rows[2][:4], ["UF", "MUNICIPIO", "TOTAL", "AUTOMOVEL"])
        fleet = fetch_fleet.city_fleet(rows)
        self.assertEqual(fleet["TOTAL"], 567215)
        self.assertEqual(fleet["AUTOMOVEL"], 285025)
        self.assertEqual(fleet["MOTOCICLETA"], 117351)
        self.assertEqual(len(fleet), 22)
        self.assertEqual(fetch_fleet.city_fleet(rows, "MG", "Uberaba")["TOTAL"], 271348)

    def test_missing_city_or_header(self):
        rows = fetch_fleet.read_xlsx_rows(fixture("senatran_frota_municipio_tipo_sample.xlsx"))
        with self.assertRaises(RuntimeError):
            fetch_fleet.city_fleet(rows, "GO", "UBERLANDIA")
        with self.assertRaises(RuntimeError):
            fetch_fleet.city_fleet([["a", "b"], ["1", "2"]])

    def test_ibge_code(self):
        self.assertEqual(fetch_fleet.IBGE_CODE, "3170206")

    def fake_get(self, calls):
        def get(url, limit=None, timeout=60):
            calls.append(url)
            if url.endswith(".xlsx"):
                return fixture("senatran_frota_municipio_tipo_sample.xlsx")
            return (FIXTURES / f"senatran_frota_{url[-4:]}.html").read_bytes()
        return get

    def test_first_run_records_newest_month_and_a_year_before(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            msg = fetch_fleet.update(d, date(2026, 10, 2), self.fake_get(calls))
            self.assertEqual(msg, "fleet: recorded 2025-08, 2026-08")
            rows = fetch_fleet.read_fleet(Path(d) / "fleet.csv")
            self.assertEqual({r["month"] for r in rows}, {"2025-08", "2026-08"})
            self.assertEqual({r["ibge_code"] for r in rows}, {"3170206"})
            month, now, before = fetch_fleet.summary(rows)
            self.assertEqual((month, now["TOTAL"], before["TOTAL"]), ("2026-08", 567215, 567215))
            # Next day: August is still the newest, so nothing is downloaded again.
            calls.clear()
            self.assertIn("already recorded", fetch_fleet.update(d, date(2026, 10, 3), self.fake_get(calls)))
            self.assertFalse([u for u in calls if u.endswith(".xlsx")])

    def test_up_to_date_makes_no_request(self):
        with tempfile.TemporaryDirectory() as d:
            fetch_fleet.append_fleet(Path(d) / "fleet.csv", "2026-09", {"TOTAL": 1})
            calls = []
            self.assertIn("already recorded", fetch_fleet.update(d, date(2026, 10, 2), self.fake_get(calls)))
            self.assertEqual(calls, [])


class LatestMarkdownTests(unittest.TestCase):
    def test_all_sections(self):
        loans = fetch_market.parse_sgs(fixture_json("sgs_20673.json"))
        basket_rows = [{"label": "Fiat Strada", "segment": "pickup", "year": 2022, "now": 75227.0,
                        "year_ago": 80000.0, "change_pct": -5.97},
                       {"label": "Fiat Mobi", "segment": "hatch", "year": 2019, "now": 43216.0,
                        "year_ago": None, "change_pct": None}]
        fleet_rows = [{"month": "2026-08", "vehicle_type": "TOTAL", "count": "567215"},
                      {"month": "2025-08", "vehicle_type": "TOTAL", "count": "550000"}]
        text = fetch_market.render_latest(
            [("BCB-SGS", "20673", "New auto loans", loans[-1][0], loans[-1][1])],
            [("New auto loans", fetch_market.trend(loans))], "2026-10", basket_rows, fleet_rows,
            ["SGS 1: timeout"])
        self.assertIn("| New auto loans | 01/08/2026 | 22,551.00 | BCB-SGS |", text)
        self.assertIn("## Trends (monthly series)", text)
        self.assertIn("| New auto loans | 01/08/2026 | 22,551.00 |", text)
        self.assertIn("## FIPE basket of popular used models (2026-10)", text)
        self.assertIn("| Fiat Strada | pickup | 2022 | 75,227 | 80,000 | -6.0% |", text)
        self.assertIn("| Fiat Mobi | hatch | 2019 | 43,216 | - | - |", text)
        self.assertIn("## Vehicle fleet of Uberlândia (Senatran, 2026-08)", text)
        self.assertIn("| TOTAL | 567,215 | 550,000 | +3.1% |", text)
        self.assertEqual(notify_telegram.count_fetch_errors(text), 1)

    def test_without_new_data(self):
        text = fetch_market.render_latest([], [], None, [], [], [])
        self.assertNotIn("## Trends", text)
        self.assertNotIn("## FIPE basket", text)
        self.assertNotIn("## Vehicle fleet", text)
        self.assertNotIn("## Fetch errors", text)


if __name__ == "__main__":
    unittest.main()
