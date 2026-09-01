from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

if "colorama" not in sys.modules:
    colorama = types.ModuleType("colorama")
    class _DummyColor:
        def __getattr__(self, name):
            return ""
    colorama.init = lambda *args, **kwargs: None
    colorama.Fore = _DummyColor()
    colorama.Style = _DummyColor()
    colorama.Style.RESET_ALL = ""
    sys.modules["colorama"] = colorama

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "kalkulatorZONDA.py"
SPEC = importlib.util.spec_from_file_location("kalkulatorZONDA", MODULE_PATH)
z = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(z)


class FakeNbp:
    def __init__(self, rates=None):
        self.rates = rates or {"PLN": 1.0, "USD": 4.0, "EUR": 4.3}
        self.calls = []

    def rate(self, ccy, d):
        c = (ccy or "").upper()
        self.calls.append((c, d))
        return float(self.rates.get(c, 1.0))


class CalculatorTests(unittest.TestCase):
    def test_safe_float_polish_formats(self):
        self.assertEqual(z._safe_float("1 234,56"), 1234.56)
        self.assertEqual(z._safe_float(""), 0.0)
        self.assertEqual(z._safe_float(None), 0.0)

    def test_parse_dt_supported_formats(self):
        self.assertEqual(z.parse_dt("2024-01-02 03:04:05").year, 2024)
        self.assertEqual(z.parse_dt("02.01.2024 03:04").month, 1)
        self.assertIsNone(z.parse_dt(""))

    def test_rounding_rules(self):
        self.assertEqual(z.round_pln_full(10.49), 10)
        self.assertEqual(z.round_pln_full(10.50), 11)
        self.assertEqual(z.round_pln_full(-10.50), -11)
        self.assertEqual(z.round_grosz(1.005), 1.01)

    def test_split_market(self):
        self.assertEqual(z.split_market("BTC-PLN"), ("BTC", "PLN"))
        self.assertEqual(z.split_market("ETH/USDT"), ("ETH", "USDT"))
        self.assertEqual(z.split_market("BTCPLN"), ("BTC", "PLN"))

    def test_classify_ops_and_trd(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ops = td / "ops.csv"
            trd = td / "trd.csv"
            pd.DataFrame([{
                "Data operacji": "2024-01-01 10:00:00",
                "Rodzaj": "Prowizja", "Wartość": "-1", "Waluta": "PLN",
            }]).to_csv(ops, index=False)
            pd.DataFrame([{
                "Rynek": "BTC-PLN", "Data operacji": "2024-01-01 10:00:00",
                "Rodzaj": "Kupno", "Typ": "Market", "Kurs": "100000",
                "Ilość": "0.01", "Wartość": "1000", "ID": "1",
            }]).to_csv(trd, index=False)
            self.assertEqual(z.classify_file(str(ops)), "OPS")
            self.assertEqual(z.classify_file(str(trd)), "TRD")

    def test_detect_pairs_uses_dates_from_csv(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            pd.DataFrame([{
                "Data operacji": "2024-01-01 10:00:00", "Rodzaj": "Prowizja",
                "Wartość": "-1", "Waluta": "PLN",
            }]).to_csv(td / "operacje.csv", index=False)
            pd.DataFrame([{
                "Rynek": "BTC-PLN", "Data operacji": "2024-01-01 10:00:00",
                "Rodzaj": "Kupno", "Typ": "Market", "Kurs": "100000",
                "Ilość": "0.01", "Wartość": "1000", "ID": "1",
            }]).to_csv(td / "transakcje.csv", index=False)
            pairs, _ = z.detect_pairs(str(td))
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0]["year"], 2024)
            self.assertGreaterEqual(pairs[0]["score"], 1)

    def test_fee_event_detection(self):
        df = pd.DataFrame([
            {"Data operacji": "2024-01-01 10:00:00", "Rodzaj": "Prowizja", "Wartość": -2, "Waluta": "PLN"},
            {"Data operacji": "2024-01-01 10:01:00", "Rodzaj": "Wpłata", "Wartość": 10, "Waluta": "PLN"},
        ])
        events = z.build_ops_fee_events(df)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["amt"], 2.0)

    def test_assign_fee_to_quote_trade(self):
        t = z.parse_dt("2024-01-01 10:00:00")
        trades = [{"idx": 0, "minute_ts": z.floor_minute_ts(t), "quote": "PLN", "base": "BTC"}]
        events = [{"dt": t, "minute_ts": z.floor_minute_ts(t), "ccy": "PLN", "amt": 5.0}]
        assigned, used, count, unassigned = z.assign_fees_minute_queued(trades, events)
        self.assertEqual(count, 1)
        self.assertEqual(unassigned, 0)
        self.assertEqual(assigned[0]["fee_quote"], [("PLN", 5.0)])
        self.assertEqual(used, {0})

    def test_compute_fee_quote_and_base(self):
        trade = {"rate_quote_to_pln": 4.0, "value_quote": 100.0, "qty_base": 2.0}
        fee, details = z.compute_fee_pln_for_trade(trade, [("USD", 1.0)], [("BTC", 0.1)])
        self.assertEqual(fee, 24.0)
        self.assertEqual(len(details), 2)

    def test_minute_graph_averages_same_pair(self):
        t = z.parse_dt("2024-01-01 10:00:00")
        m = z.floor_minute_ts(t)
        trades = [
            {"minute_ts": m, "base": "BTC", "quote": "USD", "qty_base": 1.0, "value_quote": 100.0},
            {"minute_ts": m, "base": "BTC", "quote": "USD", "qty_base": 1.0, "value_quote": 120.0},
        ]
        g = z.build_minute_graph(trades)
        self.assertAlmostEqual(g[m]["BTC"]["USD"], 110.0)

    def test_compute_minute_rates_pln(self):
        t = z.parse_dt("2024-01-01 10:00:00")
        m = z.floor_minute_ts(t)
        graph = {m: {"USD": {"BTC": 0.01}, "BTC": {"USD": 100.0}}}
        rates = z.compute_minute_rates_pln(m, graph, {"PLN": 1.0, "USD": 4.0})
        self.assertAlmostEqual(rates["BTC"], 400.0)

    def test_nbp_pln_is_one_without_fetch(self):
        c = z.NbpClient()
        c._fetch = lambda *args, **kwargs: self.fail("_fetch should not be called for PLN")
        self.assertEqual(c.rate("PLN", z.dt.date(2024, 1, 2)), 1.0)
        c.pool.shutdown(wait=False, cancel_futures=True)

    def test_nbp_backtracks_until_rate_exists(self):
        c = z.NbpClient()
        seen = []
        def fake_fetch(ccy, d):
            seen.append(d)
            return 4.0 if len(seen) == 3 else None
        c._fetch = fake_fetch
        rate = c.rate("USD", z.dt.date(2024, 1, 10))
        self.assertEqual(rate, 4.0)
        self.assertEqual(len(seen), 3)
        c.pool.shutdown(wait=False, cancel_futures=True)

    def test_source_contains_no_airtable_or_embedded_tokens(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("api.airtable.com", text)
        self.assertNotIn("AIRTABLE_TOKEN", text)
        self.assertNotIn("patfb", text)

    def test_compute_one_year_golden_case(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ops = td / "OPS.csv"
            trd = td / "TRD.csv"

            pd.DataFrame([
                {"Data operacji": "2024-01-10 12:00:00", "Rodzaj": "Prowizja", "Wartość": -10, "Waluta": "PLN"},
                {"Data operacji": "2024-01-11 12:00:00", "Rodzaj": "Prowizja", "Wartość": -15, "Waluta": "PLN"},
            ]).to_csv(ops, index=False)
            pd.DataFrame([
                {"Rynek": "BTC-PLN", "Data operacji": "2024-01-10 12:00:00", "Rodzaj": "Kupno", "Typ": "Market", "Kurs": 100000, "Ilość": 0.01, "Wartość": 1000, "ID": "BUY1"},
                {"Rynek": "BTC-PLN", "Data operacji": "2024-01-11 12:00:00", "Rodzaj": "Sprzedaż", "Typ": "Market", "Kurs": 150000, "Ilość": 0.01, "Wartość": 1500, "ID": "SELL1"},
            ]).to_csv(trd, index=False)

            old_root = z.ROOT_DIR
            try:
                z.ROOT_DIR = str(td)
                result = z.compute_one_year(str(ops), str(trd), 2024, FakeNbp())
            finally:
                z.ROOT_DIR = old_root

            out_path, revenue, cost, profit, tax_full = result[:5]
            self.assertEqual(revenue, 1500.0)
            self.assertEqual(cost, 1025.0)
            self.assertEqual(profit, 475.0)
            self.assertEqual(tax_full, 90)
            self.assertTrue(Path(out_path).exists())
            output = Path(out_path).read_text(encoding="utf-8-sig")
            self.assertIn("PIT38 (krypto) – PODSUMOWANIE", output)


if __name__ == "__main__":
    unittest.main()
