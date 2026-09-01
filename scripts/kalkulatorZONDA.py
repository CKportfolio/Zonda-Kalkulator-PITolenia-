# kalkulatorZONDA.py
# Reconstructed pre-Airtable source from the later 2026-03-08 source and historical ZondaPIT.exe evidence.
# Calculation logic preserved; Airtable telemetry/signature helpers removed.
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
import re
import glob
import math
import datetime as dt
from typing import Dict, Tuple, Optional, List, Any, DefaultDict, Set
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

import pandas as pd
import requests

from colorama import init as colorama_init
from colorama import Fore, Style


# =========================
# USTAWIENIA
# =========================

colorama_init(autoreset=True)

def get_root_dir() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller EXE
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(__file__))  # zakładamy: scripts/..

ROOT_DIR = get_root_dir()
INPUT_DIR_NAME = "_wrzuc_raport"
INPUT_DIR = os.path.join(ROOT_DIR, INPUT_DIR_NAME)

FORCE_REBUILD = False

FIAT = {"PLN", "USD", "EUR", "GBP", "CHF"}
STABLE_USD = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD"}

MAX_BACK_DAYS = 14

# requests timeouty + hard-timeout
REQ_CONNECT_TIMEOUT = 2.5
REQ_READ_TIMEOUT = 4.0
NBP_HARD_TIMEOUT = 4.5

NBP_URL_A = "https://api.nbp.pl/api/exchangerates/rates/A/{ccy}/{date}/?format=json"
NBP_URL_B = "https://api.nbp.pl/api/exchangerates/rates/B/{ccy}/{date}/?format=json"

SPINNER = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]


# =========================
# KOLORY / PRINT
# =========================

BUDYN = Fore.YELLOW
BLUE = Fore.CYAN
RED = Fore.RED
GREEN = Fore.GREEN
WHITE = Fore.WHITE

def c(text: str, color: str) -> str:
    return f"{color}{text}{Style.RESET_ALL}"

def warn(msg: str):
    print(c("[WARN]", RED) + " " + c(msg, BUDYN))

def info(msg: str):
    print(c("[INFO]", BLUE) + " " + msg)

def ok(msg: str):
    print(c("[OK]", GREEN) + " " + msg)

def title_blue(msg: str):
    print(c(msg, BLUE))

def pause_exit():
    try:
        print("\n(OK) Zakończono. Zamknij okno krzyżykiem albo naciśnij Enter…")
        input()
    except Exception:
        pass

# --- pasek postępu: throttling po % (żeby Windows terminal nie zabijał wydajności)
_LAST_PCT = -1
def print_progress(i: int, total: int, msg: str, spin: int):
    global _LAST_PCT
    total = max(1, int(total))
    i = min(int(i), total)
    pct = int((i / total) * 100)
    # odśwież tylko gdy zmienił się procent, albo jesteśmy na końcu
    if pct == _LAST_PCT and i != total:
        return
    _LAST_PCT = pct
    s = f"{SPINNER[spin % len(SPINNER)]} {pct:3d}% | {i}/{total} {msg}"
    print("\r" + s + " " * 12, end="", flush=True)

def done_progress(tail_hint: str = ""):
    global _LAST_PCT
    _LAST_PCT = -1
    suffix = f" {tail_hint}" if tail_hint else ""
    print("\r✅ 100% | gotowe" + " " * 20 + suffix + " " * 10)
    print("")

def spaced_rainbow(text: str):
    colors = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.MAGENTA, Fore.WHITE]
    out = []
    idx = 0
    for ch in text:
        if ch == " ":
            out.append("  ")
            continue
        col = colors[idx % len(colors)]
        out.append(col + ch + Style.RESET_ALL + " ")
        idx += 1
    print("".join(out).rstrip())

def fmt_money(x: float) -> str:
    return f"{x:.2f} PLN"

def tax_color(tax_full: int) -> str:
    return GREEN if tax_full <= 0 else RED


# =========================
# UTIL
# =========================

def _safe_float(x) -> float:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return 0.0
    s = s.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0

def parse_dt(x) -> Optional[dt.datetime]:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    if isinstance(x, dt.datetime):
        return x
    s = str(x).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt)
        except Exception:
            pass
    t = pd.to_datetime(s, errors="coerce")
    if pd.isna(t):
        t = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.isna(t):
            return None
    return t.to_pydatetime()

def prev_workday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1)

def round_pln_full(x: float) -> int:
    if x >= 0:
        return int(x + 0.5)
    return -int(abs(x) + 0.5)

def round_grosz(x: float) -> float:
    return round(float(x) + 1e-12, 2)

def floor_minute_ts(t: dt.datetime) -> int:
    tt = t.replace(second=0, microsecond=0)
    return int(tt.timestamp())

def is_fiat_like(ccy: str) -> bool:
    c0 = (ccy or "").upper().strip()
    return (c0 in FIAT) or (c0 in STABLE_USD)

def stable_to_ref(ccy: str) -> str:
    c0 = (ccy or "").upper().strip()
    if c0 in STABLE_USD:
        return "USD"
    return c0




# =========================
# NBP (HARD TIMEOUT)
# =========================

class NbpClient:
    """
    requests potrafi wisieć (DNS/TLS/proxy) mimo timeout.
    Dlatego fetch robimy w wątku i future.result(timeout=...) daje twardy limit.
    """
    def __init__(self):
        self.cache: Dict[Tuple[str, str], float] = {}
        self.miss: Set[Tuple[str, str]] = set()
        self.sess = requests.Session()
        self.sess.headers.update({"Accept": "application/json"})
        self.pool = ThreadPoolExecutor(max_workers=4)

    def rate(self, ccy: str, d: dt.date) -> float:
        ccy = (ccy or "").upper().strip()
        if ccy in ("PLN", ""):
            return 1.0

        ccy = stable_to_ref(ccy)  # stable -> USD

        for back in range(0, MAX_BACK_DAYS + 1):
            dd = d - dt.timedelta(days=back)
            key = (ccy, dd.isoformat())
            if key in self.cache:
                return self.cache[key]
            if key in self.miss:
                continue

            r = self._fetch(ccy, dd)
            if r is not None:
                self.cache[key] = r
                return r
            else:
                self.miss.add(key)

        raise RuntimeError(f"NBP: brak kursu dla {ccy} <= {d.isoformat()}")

    def _fetch(self, ccy: str, d: dt.date) -> Optional[float]:
        fut = self.pool.submit(self._fetch_sync, ccy, d)
        try:
            return fut.result(timeout=NBP_HARD_TIMEOUT)
        except FutTimeout:
            return None
        except Exception:
            return None

    def _fetch_sync(self, ccy: str, d: dt.date) -> Optional[float]:
        timeout = (REQ_CONNECT_TIMEOUT, REQ_READ_TIMEOUT)

        urlA = NBP_URL_A.format(ccy=ccy, date=d.isoformat())
        try:
            resp = self.sess.get(urlA, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                return float(data["rates"][0]["mid"])
            if resp.status_code != 404:
                return None
        except Exception:
            return None

        urlB = NBP_URL_B.format(ccy=ccy, date=d.isoformat())
        try:
            resp = self.sess.get(urlB, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                return float(data["rates"][0]["mid"])
            return None
        except Exception:
            return None


# =========================
# CSV READ / DETECTION
# =========================

def read_csv_any(path: str) -> pd.DataFrame:
    last_err: Optional[Exception] = None
    for enc in ("utf-8-sig", "utf-8", None):
        for sep in (",", "\t", ";"):
            try:
                df = pd.read_csv(path, sep=sep, engine="python", encoding=enc)
                if df.shape[1] >= 3:
                    return df
            except Exception as e:
                last_err = e
                continue
    try:
        return pd.read_csv(path, engine="python")
    except Exception as e:
        raise last_err or e

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    cols = []
    for c0 in df.columns:
        c1 = str(c0).strip().strip('"').strip().replace("\ufeff", "")
        cols.append(c1)
    df.columns = cols
    return df

def classify_file(path: str) -> Optional[str]:
    try:
        df = normalize_cols(read_csv_any(path))
    except Exception:
        return None
    cols = set(df.columns)
    if {"Data operacji", "Rodzaj", "Wartość", "Waluta"}.issubset(cols):
        return "OPS"
    if {"Rynek", "Data operacji", "Rodzaj", "Typ", "Kurs", "Ilość", "Wartość", "ID"}.issubset(cols):
        return "TRD"
    return None


# =========================
# MATCHING PO ZAWARTOŚCI
# =========================

def _norm_colname(x: str) -> str:
    s = (x or "").strip().lower().replace("\ufeff", "")
    s = re.sub(r"[^a-z0-9ąćęłńóśźż]", "", s, flags=re.IGNORECASE)
    return s

def find_data_operacji_col(df: pd.DataFrame) -> Optional[str]:
    target = _norm_colname("Data operacji")
    best: Optional[str] = None
    for col in df.columns:
        n = _norm_colname(str(col))
        if not n:
            continue
        if n == target:
            return str(col)
        if ("data" in n) and ("oper" in n):
            best = str(col)
    return best

def extract_ts_sample(path: str, kind: str, max_sample: int = 2000) -> Tuple[Optional[int], List[int], int]:
    try:
        df = normalize_cols(read_csv_any(path))
    except Exception:
        return None, [], 0

    date_col = find_data_operacji_col(df)
    if not date_col:
        return None, [], 0

    parsed = []
    for x in df[date_col].head(20000):
        t = parse_dt(x)
        if t:
            parsed.append(t)
    if not parsed:
        return None, [], 0

    years = pd.Series([t.year for t in parsed]).value_counts()
    year_guess = int(years.index[0])

    ts_unique = sorted({int(t.timestamp()) for t in parsed})
    if len(ts_unique) > max_sample:
        step = max(1, len(ts_unique) // max_sample)
        ts_unique = ts_unique[::step]
        ts_unique = ts_unique[:max_sample]

    total_dt = len(df[date_col])
    return year_guess, ts_unique, total_dt

def overlap_score(a: List[int], b: List[int]) -> int:
    if not a or not b:
        return 0
    return len(set(a).intersection(set(b)))

def detect_pairs(input_dir: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    files = sorted(glob.glob(os.path.join(input_dir, "*.csv*")))
    existing = [os.path.basename(p) for p in files]

    info(f"ROOT = {ROOT_DIR}")
    info(f"INPUT_DIR = {INPUT_DIR}")
    info(f"znalezione pliki: {len(existing)}")
    for n in existing:
        print(f"  - {n}")
    print()

    items: List[Dict[str, Any]] = []
    for p in files:
        name = os.path.basename(p)
        kind = classify_file(p)
        if kind is None:
            warnings.append(f"Nie rozpoznano typu OPS/TRD: {name}")
            continue

        year_guess, ts_sample, total_dt = extract_ts_sample(p, kind)

        if year_guess is None:
            warnings.append(f"Nie wykryto dat 'Data operacji' w pliku: {name} (kind={kind})")
            continue
        if not ts_sample:
            warnings.append(f"Brak próbek czasu do overlap dla pliku: {name} (kind={kind})")

        items.append({"path": p, "name": name, "kind": kind, "year": year_guess, "ts": ts_sample, "dt_rows": total_dt})

    by_year: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
    for it in items:
        y = int(it["year"])
        by_year.setdefault(y, {"OPS": [], "TRD": []})
        by_year[y][it["kind"]].append(it)

    pairs: List[Dict[str, Any]] = []
    for year in sorted(by_year.keys()):
        ops_list = by_year[year]["OPS"]
        trd_list = by_year[year]["TRD"]
        if not ops_list or not trd_list:
            continue

        if len(ops_list) == 1 and len(trd_list) == 1:
            o = ops_list[0]
            t = trd_list[0]
            sc = overlap_score(o.get("ts", []), t.get("ts", []))
            pairs.append({"year": year, "ops_path": o["path"], "trd_path": t["path"], "score": sc})
            continue

        ops_remaining = ops_list[:]
        trd_remaining = trd_list[:]

        while ops_remaining and trd_remaining:
            best_score = -1
            best_i = -1
            best_j = -1
            for i, o in enumerate(ops_remaining):
                for j, t in enumerate(trd_remaining):
                    sc = overlap_score(o["ts"], t["ts"])
                    if sc > best_score:
                        best_score = sc
                        best_i, best_j = i, j
            if best_score <= 0:
                break
            o = ops_remaining.pop(best_i)
            t = trd_remaining.pop(best_j)
            pairs.append({"year": year, "ops_path": o["path"], "trd_path": t["path"], "score": best_score})

    info(f"Wykryte pary: {len(pairs)}")
    for pr in pairs:
        print(f"  - rok {pr['year']} | score={pr.get('score', 0)} | OPS={os.path.basename(pr['ops_path'])} | TRD={os.path.basename(pr['trd_path'])}")
    print()
    return pairs, warnings


# =========================
# PIT LOGIKA
# =========================

def split_market(market: str) -> Tuple[str, str]:
    s = (market or "").strip().upper()
    for sep in ("-", "/", "_"):
        if sep in s:
            a, b = s.split(sep, 1)
            return a.strip(), b.strip()
    known_suffix = sorted(FIAT.union(STABLE_USD), key=lambda x: -len(x))
    for q in known_suffix:
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)], q
    return s, ""

def is_fee_row_ops(kind: str) -> bool:
    k = (kind or "").lower()
    return ("prowiz" in k) or ("fee" in k)

def build_ops_fee_events(ops_df: pd.DataFrame) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for _, r in ops_df.iterrows():
        kind = str(r.get("Rodzaj", ""))
        if not is_fee_row_ops(kind):
            continue
        t = parse_dt(r.get("Data operacji"))
        if not t:
            continue
        ccy = str(r.get("Waluta", "")).upper().strip()
        amt = _safe_float(r.get("Wartość"))
        if not ccy or amt == 0:
            continue
        events.append({"dt": t, "minute_ts": floor_minute_ts(t), "ccy": ccy, "amt": abs(amt)})
    events.sort(key=lambda x: (x["minute_ts"], x["dt"]))
    return events

def build_trade_rows_all(trd_df: pd.DataFrame) -> List[Dict[str, Any]]:
    trades: List[Dict[str, Any]] = []
    for idx, r in trd_df.iterrows():
        market = str(r.get("Rynek", "")).strip()
        base, quote = split_market(market)
        t = parse_dt(r.get("Data operacji"))
        if not t or not base or not quote:
            continue
        base = base.upper().strip()
        quote = quote.upper().strip()

        side = str(r.get("Rodzaj", "")).strip().lower()
        value_quote = _safe_float(r.get("Wartość"))
        qty_base = _safe_float(r.get("Ilość"))
        trade_id = str(r.get("ID", "")).strip()

        rate_day = prev_workday(t.date())
        trades.append({
            "idx": int(idx),
            "dt": t,
            "minute_ts": floor_minute_ts(t),
            "market": market,
            "base": base,
            "quote": quote,
            "side": side,
            "value_quote": value_quote,
            "qty_base": qty_base,
            "id": trade_id,
            "rate_day": rate_day,
        })
    trades.sort(key=lambda x: (x["minute_ts"], x["dt"], x["idx"]))
    return trades

def build_trade_rows_fiat_only(trades_all: List[Dict[str, Any]], nbp: NbpClient) -> List[Dict[str, Any]]:
    out = []
    for tr in trades_all:
        q = tr["quote"]
        if q not in FIAT:
            continue
        rate = nbp.rate(q, tr["rate_day"])
        tr2 = dict(tr)
        tr2["rate_quote_to_pln"] = rate
        out.append(tr2)
    return out

def assign_fees_minute_queued(
    trades_fiat: List[Dict[str, Any]],
    fee_events: List[Dict[str, Any]],
) -> Tuple[Dict[int, Dict[str, Any]], set, int, int]:
    q_quote: DefaultDict[Tuple[int, str], deque] = defaultdict(deque)
    q_base: DefaultDict[Tuple[int, str], deque] = defaultdict(deque)

    for tr in trades_fiat:
        q_quote[(tr["minute_ts"], tr["quote"])].append(tr["idx"])
        q_base[(tr["minute_ts"], tr["base"])].append(tr["idx"])

    assigned: Dict[int, Dict[str, Any]] = {}
    used_fee = set()
    assigned_count = 0
    unassigned_count = 0

    def add_ass(trade_idx: int, kind: str, ccy: str, amt: float, ev_i: int):
        nonlocal assigned_count
        if trade_idx not in assigned:
            assigned[trade_idx] = {"fee_quote": [], "fee_base": []}
        assigned[trade_idx][kind].append((ccy, amt))
        assigned_count += 1
        used_fee.add(ev_i)

    for ev_i, ev in enumerate(fee_events):
        m = int(ev["minute_ts"])
        ccy = str(ev["ccy"])
        amt = float(ev["amt"])

        if ccy in FIAT:
            for mm in (m, m - 60, m + 60):
                key = (mm, ccy)
                if key in q_quote and len(q_quote[key]) > 0:
                    trade_idx = q_quote[key].popleft()
                    add_ass(trade_idx, "fee_quote", ccy, amt, ev_i)
                    break
            else:
                unassigned_count += 1
        else:
            for mm in (m, m - 60, m + 60):
                key = (mm, ccy)
                if key in q_base and len(q_base[key]) > 0:
                    trade_idx = q_base[key].popleft()
                    add_ass(trade_idx, "fee_base", ccy, amt, ev_i)
                    break
            else:
                unassigned_count += 1

    return assigned, used_fee, assigned_count, unassigned_count

def compute_fee_pln_for_trade(
    tr: Dict[str, Any],
    fee_quote_list: List[Tuple[str, float]],
    fee_base_list: List[Tuple[str, float]],
) -> Tuple[float, List[str]]:
    rate = float(tr["rate_quote_to_pln"])
    value_quote = float(tr["value_quote"])
    qty_base = float(tr["qty_base"]) if float(tr["qty_base"]) != 0 else 0.0

    details: List[str] = []
    fee_pln = 0.0

    for ccy, amt in fee_quote_list:
        part = abs(float(amt)) * rate if ccy in FIAT else 0.0
        part = round_grosz(part)
        fee_pln += part
        details.append(f"{ccy}:-{amt:g} (~{part:.2f}PLN)")

    unit_price_quote = (value_quote / qty_base) if (qty_base > 0 and value_quote > 0) else 0.0
    for ccy, amt in fee_base_list:
        part = abs(float(amt)) * unit_price_quote * rate if unit_price_quote > 0 else 0.0
        part = round_grosz(part)
        fee_pln += part
        details.append(f"{ccy}:-{amt:g} (~{part:.2f}PLN)")

    fee_pln = round_grosz(fee_pln)
    return fee_pln, details


# =========================
# WYCENA WSZYSTKICH FEE Z OPS (NBP + stables + graf TRD)
# =========================

# adjacency: graph[minute][u][v] = rate(u->v)
MinuteGraph = Dict[int, Dict[str, Dict[str, float]]]

def build_minute_graph(trades_all: List[Dict[str, Any]]) -> MinuteGraph:
    """
    Budujemy graf minutowy, ale DEDUPUJEMY krawędzie.
    Jeśli w tej samej minucie mamy wiele transakcji tej samej pary,
    uśredniamy kurs (prosta średnia).
    """
    # tmp: sumy i liczniki
    sums: Dict[int, Dict[str, Dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    cnts: Dict[int, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(dict))

    for tr in trades_all:
        m = tr["minute_ts"]
        a = tr["base"]
        b = tr["quote"]
        qty = float(tr["qty_base"])
        val = float(tr["value_quote"])
        if qty <= 0 or val <= 0:
            continue

        r_a_to_b = val / qty
        r_b_to_a = qty / val

        if r_a_to_b > 0:
            prev = sums[m][a].get(b, 0.0)
            sums[m][a][b] = prev + r_a_to_b
            cnts[m][a][b] = cnts[m][a].get(b, 0) + 1

        if r_b_to_a > 0:
            prev = sums[m][b].get(a, 0.0)
            sums[m][b][a] = prev + r_b_to_a
            cnts[m][b][a] = cnts[m][b].get(a, 0) + 1

    g: MinuteGraph = defaultdict(lambda: defaultdict(dict))
    for m, adj in sums.items():
        for u, mp in adj.items():
            for v, s in mp.items():
                c = cnts[m][u].get(v, 1)
                g[m][u][v] = s / max(1, c)

    return g

def seed_rates_for_day(nbp: NbpClient, rate_day: dt.date) -> Dict[str, float]:
    seeds: Dict[str, float] = {"PLN": 1.0}
    try:
        usd_pln = float(nbp.rate("USD", rate_day))
        seeds["USD"] = usd_pln
        for s in STABLE_USD:
            seeds[s] = usd_pln
    except Exception:
        pass
    return seeds

def compute_minute_rates_pln(
    minute_ts: int,
    graph: MinuteGraph,
    seeds: Dict[str, float],
) -> Dict[str, float]:
    """
    BFS bez relaksacji/„uśredniania” => zero pętli nieskończonych.
    Wyznaczamy kursy w PLN tylko raz na węzeł.
    """
    adj = graph.get(minute_ts)
    rates = dict(seeds)
    if not adj:
        return rates

    q = deque(seeds.keys())
    seen = set(seeds.keys())

    while q:
        u = q.popleft()
        ru = rates.get(u)
        if ru is None:
            continue

        nbrs = adj.get(u)
        if not nbrs:
            continue

        for v, r_u_to_v in nbrs.items():
            if r_u_to_v <= 0:
                continue
            if v in seen:
                continue
            rv = ru / r_u_to_v
            if not math.isfinite(rv) or rv <= 0:
                continue
            rates[v] = rv
            seen.add(v)
            q.append(v)

    return rates

def precompute_minute_rate_maps(
    fee_events: List[Dict[str, Any]],
    nbp: NbpClient,
    graph: MinuteGraph,
) -> Tuple[Dict[Tuple[int, dt.date], Dict[str, float]], Dict[dt.date, Dict[str, float]]]:
    """
    Precompute: dla wszystkich (minute_ts±0/60, rate_day) które występują w OPS,
    licz mapę waluta->PLN tylko raz.
    """
    keys: Set[Tuple[int, dt.date]] = set()
    for ev in fee_events:
        fee_dt: dt.datetime = ev["dt"]
        rate_day = prev_workday(fee_dt.date())
        m0 = floor_minute_ts(fee_dt)
        keys.add((m0, rate_day))
        keys.add((m0 - 60, rate_day))
        keys.add((m0 + 60, rate_day))

    seeds_cache: Dict[dt.date, Dict[str, float]] = {}
    minute_rates_cache: Dict[Tuple[int, dt.date], Dict[str, float]] = {}

    total = len(keys)
    spin = 0
    i = 0

    info(f"Precompute kursów (minuta x dzień): {total} map...")

    # sort, żeby postęp wyglądał sensownie
    for (m, rate_day) in sorted(keys, key=lambda x: (x[1], x[0])):
        i += 1
        spin += 1
        print_progress(i, total, "buduję mapy kursów...", spin)

        if rate_day not in seeds_cache:
            seeds_cache[rate_day] = seed_rates_for_day(nbp, rate_day)
        seeds = seeds_cache[rate_day]

        minute_rates_cache[(m, rate_day)] = compute_minute_rates_pln(m, graph, seeds)

    done_progress("done precompute")
    return minute_rates_cache, seeds_cache

def get_pln_rate_for_ccy_from_cache(
    ccy: str,
    fee_dt: dt.datetime,
    nbp: NbpClient,
    minute_rates_cache: Dict[Tuple[int, dt.date], Dict[str, float]],
) -> Optional[float]:
    c0 = (ccy or "").upper().strip()
    rate_day = prev_workday(fee_dt.date())

    # fiat / stable: NBP bez grafu
    if is_fiat_like(c0):
        try:
            return float(nbp.rate(c0, rate_day))
        except Exception:
            return None

    m0 = floor_minute_ts(fee_dt)
    for m in (m0, m0 - 60, m0 + 60):
        mp = minute_rates_cache.get((m, rate_day))
        if not mp:
            continue
        r = mp.get(c0)
        if r and r > 0:
            return float(r)
    return None


# =========================
# GŁÓWNE LICZENIE ROKU
# =========================

def compute_one_year(
    ops_path: str,
    trd_path: str,
    year: int,
    nbp: NbpClient
):
    ops_df = normalize_cols(read_csv_any(ops_path))
    trd_df = normalize_cols(read_csv_any(trd_path))

    trades_all = build_trade_rows_all(trd_df)
    trades_fiat = build_trade_rows_fiat_only(trades_all, nbp)

    fee_events = build_ops_fee_events(ops_df)
    fee_total_ops = len(fee_events)

    assigned_map, used_fee_idx, fee_assigned, fee_unassigned_matcher = assign_fees_minute_queued(trades_fiat, fee_events)

    # graf TRD (dedupe)
    graph = build_minute_graph(trades_all)

    # PRECOMPUTE map kursów (to usuwa "stoi na 3/1592" bo najcięższe minuty liczą się tu,
    # z sensownym postępem, a nie podczas pierwszych fee)
    minute_rates_cache, _ = precompute_minute_rate_maps(fee_events, nbp, graph)

    revenue_pln = 0.0
    cost_pln = 0.0
    fee_pln_matched = 0.0

    month_stats: Dict[str, Dict[str, float]] = {}
    out_rows: List[Dict[str, Any]] = []

    total = len(trades_fiat)
    spin = 0
    for i, tr in enumerate(trades_fiat, start=1):
        spin += 1
        print_progress(i, total, "przetwarzam transakcje FIAT...", spin)

        t: dt.datetime = tr["dt"]
        market = tr["market"]
        quote = tr["quote"]
        side = tr["side"]
        value_quote = float(tr["value_quote"])
        trade_id = tr["id"]
        rate = float(tr["rate_quote_to_pln"])
        value_pln = value_quote * rate

        assign = assigned_map.get(tr["idx"], {"fee_quote": [], "fee_base": []})
        fee_quote_list = assign.get("fee_quote", [])
        fee_base_list = assign.get("fee_base", [])

        fee_pln, fee_details = compute_fee_pln_for_trade(tr, fee_quote_list, fee_base_list)
        fee_pln_matched += fee_pln

        ym = t.strftime("%Y-%m")
        month_stats.setdefault(ym, {"rev": 0.0, "cost": 0.0, "fee_fiat_match": 0.0, "fee_ops_extra": 0.0})

        if side.startswith("kup"):
            cost_pln += value_pln + fee_pln
            month_stats[ym]["cost"] += value_pln + fee_pln
            month_stats[ym]["fee_fiat_match"] += fee_pln
            kind_label = "KOSZT"
        elif side.startswith("sprz"):
            revenue_pln += value_pln
            cost_pln += fee_pln
            month_stats[ym]["rev"] += value_pln
            month_stats[ym]["cost"] += fee_pln
            month_stats[ym]["fee_fiat_match"] += fee_pln
            kind_label = "PRZYCHÓD"
        else:
            continue

        out_rows.append({
            "Data operacji": t.strftime("%d.%m.%Y %H:%M"),
            "Rynek": market,
            "Rodzaj": "Kupno" if side.startswith("kup") else "Sprzedaż" if side.startswith("sprz") else trd_df.iloc[tr["idx"]].get("Rodzaj"),
            "ID": trade_id,
            "Quote": quote,
            "NBP_rate": rate,
            "Wartość (quote)": value_quote,
            "PLN_value_raw": round(value_pln, 6),
            "Fee_PLN_included": fee_pln,
            "Fee_match_detail": " | ".join(fee_details) if fee_details else "",
            "koszt/przychód": kind_label,
        })

    done_progress("done FIAT")

    info("Wyceniam WSZYSTKIE prowizje z OPS (NBP + stable=USD + graf TRD)...")

    fee_ops_valued = 0
    fee_ops_unvalued = 0
    fee_ops_total_pln = 0.0
    fee_ops_extra_pln = 0.0

    spin = 0
    for i, ev in enumerate(fee_events, start=1):
        spin += 1
        print_progress(i, fee_total_ops, "wyceniam FEE z OPS...", spin)

        r = get_pln_rate_for_ccy_from_cache(
            ev["ccy"], ev["dt"], nbp, minute_rates_cache
        )
        if r is None:
            fee_ops_unvalued += 1
            continue

        pln = round_grosz(float(ev["amt"]) * float(r))
        fee_ops_total_pln += pln
        fee_ops_valued += 1

        ev_i = i - 1
        if ev_i not in used_fee_idx:
            fee_ops_extra_pln += pln
            ym = ev["dt"].strftime("%Y-%m")
            month_stats.setdefault(ym, {"rev": 0.0, "cost": 0.0, "fee_fiat_match": 0.0, "fee_ops_extra": 0.0})
            month_stats[ym]["fee_ops_extra"] += pln

    done_progress("done OPS fee")

    fee_ops_total_pln = round_grosz(fee_ops_total_pln)
    fee_ops_extra_pln = round_grosz(fee_ops_extra_pln)

    # doliczamy tylko "extra" (żeby nie podwajać tego co już weszło via FIAT-matcher)
    cost_pln += fee_ops_extra_pln

    revenue_pln = round_grosz(revenue_pln)
    cost_pln = round_grosz(cost_pln)
    profit = round_grosz(revenue_pln - cost_pln)

    income = max(0.0, profit)
    base_full = round_pln_full(income)
    tax_full = round_pln_full(base_full * 0.19) if base_full > 0 else 0

    out_path = os.path.join(ROOT_DIR, f"Zonda PIT38 {year}.csv")
    out_df = pd.DataFrame(out_rows)

    footer = []
    footer.append({})
    footer.append({"Data operacji": "PIT38 (krypto) – PODSUMOWANIE"})
    footer.append({"Data operacji": "Przychód (PLN)", "Rynek": f"{revenue_pln:.2f}"})
    footer.append({"Data operacji": "Koszt (PLN)", "Rynek": f"{cost_pln:.2f}"})
    footer.append({"Data operacji": "Zysk/Strata (PLN)", "Rynek": f"{profit:.2f}"})
    footer.append({"Data operacji": "Podatek 19% (pełne zł)", "Rynek": f"{tax_full:d}"})
    footer.append({})
    footer.append({"Data operacji": "FEE dopasowane do FIAT TRD (PLN)", "Rynek": f"{fee_pln_matched:.2f}"})
    footer.append({"Data operacji": "FEE WSZYSTKIE z OPS wycenione (PLN)", "Rynek": f"{fee_ops_total_pln:.2f}"})
    footer.append({"Data operacji": "FEE z OPS dodatkowo doliczone (PLN)", "Rynek": f"{fee_ops_extra_pln:.2f}"})
    footer.append({"Data operacji": "FEE OPS: wycenione (zdarzeń)", "Rynek": f"{fee_ops_valued:d}"})
    footer.append({"Data operacji": "FEE OPS: niewycenione (zdarzeń)", "Rynek": f"{fee_ops_unvalued:d}"})
    footer.append({"Data operacji": "FEE OPS: total (zdarzeń)", "Rynek": f"{fee_total_ops:d}"})

    out_df2 = pd.concat([out_df, pd.DataFrame(footer)], ignore_index=True)
    out_df2.to_csv(out_path, index=False, encoding="utf-8-sig")

    return (
        out_path,
        revenue_pln, cost_pln, profit, tax_full,
        fee_pln_matched, fee_ops_total_pln,
        fee_ops_valued, fee_ops_unvalued, fee_total_ops,
        month_stats
    )


# =========================
# PRINTING
# =========================

def print_year_result(
    year: int,
    month_stats: Dict[str, Dict[str, float]],
    revenue: float,
    cost: float,
    profit: float,
    tax_full: int,
    fee_fiat_matched: float,
    fee_ops_total_pln: float,
    fee_ops_valued: int,
    fee_ops_unvalued: int,
    fee_ops_total_cnt: int,
):
    print("\n--- WYNIK ZONDA PIT-38 (krypto) ---")
    print(f"Rok: {year}\n")

    if month_stats:
        print("Miesiące:")
        for ym in sorted(month_stats.keys()):
            m = month_stats[ym]
            rev = m.get("rev", 0.0)
            cc = m.get("cost", 0.0)
            ff = m.get("fee_fiat_match", 0.0)
            ex = m.get("fee_ops_extra", 0.0)
            if abs(rev) < 0.005 and abs(cc) < 0.005 and abs(ff) < 0.005 and abs(ex) < 0.005:
                continue
            print(
                f"  {ym} | przychód {rev:.2f} PLN | koszt {cc:.2f} PLN "
                f"| fee(match) {ff:.2f} PLN | fee(extra OPS) {ex:.2f} PLN"
            )
        print()

    print(f"Przychód:     {c(fmt_money(revenue), RED)}")
    print(f"Koszt:        {c(fmt_money(cost), GREEN)}")

    zs_col = RED if profit > 0 else GREEN
    print(f"Zysk/Strata:  {c(fmt_money(profit), zs_col)}")

    tax_col = tax_color(tax_full)
    print(f"Podatek 19%:  {c(str(tax_full) + ' PLN', tax_col)} (pełne zł)\n")

    print(f"FEE dopasowane do FIAT TRD: {fee_fiat_matched:.2f} PLN")
    print(f"FEE WSZYSTKIE z OPS (wycenione): {fee_ops_total_pln:.2f} PLN")
    print(f"FEE OPS: wycenione: {fee_ops_valued} | niewycenione: {fee_ops_unvalued} | total OPS: {fee_ops_total_cnt}\n")

def print_success_or_loss(year: int, profit: float):
    if profit <= 0:
        print(c(f"Gratuluję straty w {year} - uniknąłeś kary za sukces 😇", GREEN) + "\n")
    else:
        print(c(f"Przykro mi... Osiągnąłeś sukces w {year} - twoja kara czeka 😈", RED) + "\n")


# =========================
# MAIN
# =========================

def main():
    os.makedirs(INPUT_DIR, exist_ok=True)

    pairs, warnings = detect_pairs(INPUT_DIR)
    if not pairs:
        raise RuntimeError("Nie znalazłem żadnych poprawnych par OPS/TRD po dopasowaniu datami w _wrzuc_raport.")

    if warnings:
        for w in warnings:
            warn(w)
        print()

    nbp = NbpClient()
    out_files: List[str] = []
    total_pairs = len(pairs)

    for run_idx, pr in enumerate(pairs, start=1):
        year = int(pr["year"])
        ops_path = pr["ops_path"]
        trd_path = pr["trd_path"]

        out_path = os.path.join(ROOT_DIR, f"Zonda PIT38 {year}.csv")
        title_blue(f"===== ({run_idx}/{total_pairs}) START =====\n")

        print(f"[PAIR] Rok {year}:")
        print(f"[OK] Operacje:   {os.path.basename(ops_path)}")
        print(f"[OK] Transakcje: {os.path.basename(trd_path)}\n")

        try:
            if (not FORCE_REBUILD) and os.path.exists(out_path):
                warn(f"Wynik już istnieje: {os.path.basename(out_path)} (ustaw FORCE_REBUILD=True aby przeliczyć)")
                out_files.append(out_path)
                print()
                continue

            produced_path, revenue, cost, profit, tax_full, fee_fiat_matched, fee_ops_total_pln, fee_ops_valued, fee_ops_unvalued, fee_ops_total_cnt, month_stats = compute_one_year(
                ops_path=ops_path,
                trd_path=trd_path,
                year=year,
                nbp=nbp
            )

            print_year_result(
                year=year,
                month_stats=month_stats,
                revenue=revenue,
                cost=cost,
                profit=profit,
                tax_full=tax_full,
                fee_fiat_matched=fee_fiat_matched,
                fee_ops_total_pln=fee_ops_total_pln,
                fee_ops_valued=fee_ops_valued,
                fee_ops_unvalued=fee_ops_unvalued,
                fee_ops_total_cnt=fee_ops_total_cnt,
            )
            print_success_or_loss(year, profit)

            out_files.append(produced_path)
        except Exception as pair_err:
            warn(f"Rok {year}: nie udało się policzyć pary ({pair_err})")
            print()

    spaced_rainbow("Życzę miłego dnia")
    print("\n\nPliki wynikowe:")
    for p in out_files:
        print(f"  - {p}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n" + c("❌ Błąd: ", RED) + str(e))
        print("\n[ERROR] Kalkulator zakonczyl sie bledem.")
    finally:
        pause_exit()