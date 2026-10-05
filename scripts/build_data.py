"""Build docs/data.json for the US volatility watchlist.

Universe : every non-test security listed on NYSE / Nasdaq / NYSE American / Arca / Cboe
           (Nasdaq Trader symbol directory), minus warrants, rights, units and preferreds.
Prices   : Yahoo Finance daily bars via yfinance (split/dividend adjusted).
Metrics  : for each window N in WINDOWS trading days, for the latest window and the one before it
           amp = (highest high - lowest low) / close before the window
           hv  = stdev of daily log returns * sqrt(252)   (needs N >= 2)
           ret = close / close before the window - 1

Usage:  python scripts/build_data.py            # real data
        python scripts/build_data.py --demo     # synthetic data, no network (for previewing the page)
        python scripts/build_data.py --limit 300   # quick real-data test
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data.json"
WEEKLY_DIR = ROOT / "docs" / "weekly"      # one file per first letter, loaded when a row is expanded
N_WEEKS = 52
BROKER_DIR = ROOT / "brokers"
BROKERS = [("fubon", "富邦"), ("cathay", "國泰"), ("yuanta", "元大")]

WINDOWS = [1, 3, 5, 21]          # day / 3 days / week / month, in trading days
METRICS = ["amp", "hv", "ret"]
SYMDIR = "https://www.nasdaqtrader.com/dynamic/SymDir/"
EXCHANGES = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}
SKIP_NAME = re.compile(r"\b(?:Warrants?|Rights?)\b", re.I)
UNIT_NAME = re.compile(r"\bUnits?\b", re.I)
KEEP_UNIT = re.compile(r"Common Units?|Limited Partner|Depositary Units?", re.I)


# ---------------------------------------------------------------- universe
def _read_symdir(name: str) -> pd.DataFrame:
    import requests

    r = requests.get(SYMDIR + name, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="|", dtype=str, keep_default_na=False)
    return df[~df.iloc[:, 0].str.startswith("File Creation Time")]


def load_universe() -> pd.DataFrame:
    nq = _read_symdir("nasdaqlisted.txt")
    nq = pd.DataFrame({"sym": nq["Symbol"], "name": nq["Security Name"], "exch": "Nasdaq",
                       "etf": nq["ETF"] == "Y", "test": nq["Test Issue"] == "Y"})
    ot = _read_symdir("otherlisted.txt")
    ot = pd.DataFrame({"sym": ot["ACT Symbol"], "name": ot["Security Name"],
                       "exch": ot["Exchange"].map(EXCHANGES).fillna(ot["Exchange"]),
                       "etf": ot["ETF"] == "Y", "test": ot["Test Issue"] == "Y"})
    u = pd.concat([nq, ot], ignore_index=True)
    u = u[~u["test"] & (u["sym"] != "")]
    u = u[~u["sym"].str.contains(r"[$^]", regex=True)]                      # preferreds etc.
    u = u[~u["name"].str.contains(SKIP_NAME)]
    u = u[~(u["name"].str.contains(UNIT_NAME) & ~u["name"].str.contains(KEEP_UNIT))]
    u["sym"] = u["sym"].str.strip()
    u["yahoo"] = u["sym"].str.replace(".", "-", regex=False)                # BRK.B -> BRK-B
    u["name"] = u["name"].str.replace(r"\s+-\s+.*$", "", regex=True).str.slice(0, 60)
    return u.drop_duplicates("sym").drop(columns="test").reset_index(drop=True)


def load_brokers() -> tuple[list[str], dict[str, int]]:
    """brokers/<key>.txt: one ticker per line. Returns (labels of brokers with data, ticker -> bitmask)."""
    labels, mask = [], {}
    for key, label in BROKERS:
        f = BROKER_DIR / f"{key}.txt"
        if not f.exists():
            continue
        syms = {s.strip().upper().replace("-", ".") for s in f.read_text(encoding="utf-8").splitlines()
                if s.strip() and not s.startswith("#")}
        if not syms:
            continue
        bit = 1 << len(labels)
        labels.append(label)
        for s in syms:
            mask[s] = mask.get(s, 0) | bit
    return labels, mask


# ---------------------------------------------------------------- metrics
def _r(x: float) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), 4)


def metrics_for(df: pd.DataFrame) -> dict | None:
    """df: daily bars with High/Low/Close/Volume, oldest first."""
    df = df.dropna(subset=["Close", "High", "Low"])
    df = df[df["Close"] > 0]
    n = len(df)
    if n < 2:
        return None
    c, h, l = df["Close"].to_numpy(float), df["High"].to_numpy(float), df["Low"].to_numpy(float)
    lr = np.diff(np.log(c))                      # lr[i] = return of day i+1

    def span(start: int, end: int) -> tuple:
        """amp / hv / ret over bars start..end, relative to the close of bar start-1."""
        if start - 1 < 0:
            return None, None, None
        base = c[start - 1]
        amp = (h[start:end + 1].max() - l[start:end + 1].min()) / base
        ret = c[end] / base - 1
        hv = float(np.std(lr[start - 1:end], ddof=1) * math.sqrt(252)) if end > start else None
        return _r(amp), _r(hv), _r(ret)

    def one(end: int, N: int) -> tuple:
        return span(end - N + 1, end)

    # calendar (ISO) weeks: key = year*100 + week
    iso = df.index.isocalendar()
    wk = (iso["year"].to_numpy(int) * 100 + iso["week"].to_numpy(int))
    cuts = np.flatnonzero(np.diff(wk)) + 1
    starts, ends = np.r_[0, cuts], np.r_[cuts - 1, n - 1]
    weekly = {int(wk[s]): span(int(s), int(e)) for s, e in zip(starts, ends)}

    cur = [one(n - 1, N) for N in WINDOWS]
    prev = [one(n - 1 - N, N) for N in WINDOWS]
    tail = df.tail(20)
    return {
        "date": df.index[-1],
        "close": round(float(c[-1]), 4),
        "dvol": round(float((tail["Close"] * tail["Volume"]).mean()), 0),
        "cur": cur, "prev": prev, "weekly": weekly,
    }


# ---------------------------------------------------------------- prices
def fetch_prices(tickers: list[str], chunk: int = 150):
    import yfinance as yf

    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        data = None
        for attempt in range(3):
            try:
                data = yf.download(part, period="13mo", interval="1d", group_by="ticker",
                                   auto_adjust=True, threads=True, progress=False)
                if data is not None and not data.empty:
                    break
            except Exception as e:  # noqa: BLE001
                print(f"  chunk {i}: {e!r}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
        print(f"fetched {min(i + chunk, len(tickers))}/{len(tickers)}", flush=True)
        if data is None or data.empty or not isinstance(data.columns, pd.MultiIndex):
            continue
        have = set(data.columns.get_level_values(0))
        for t in part:
            if t in have:
                yield t, data[t]
        time.sleep(1)


def demo_prices(tickers: list[str]):
    rng = np.random.default_rng(7)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=290)
    for t in tickers:
        vol = rng.uniform(0.005, 0.08)
        c = rng.uniform(2, 400) * np.exp(np.cumsum(rng.normal(0, vol, len(idx))))
        spread = np.abs(rng.normal(0, vol, len(idx))) * c
        yield t, pd.DataFrame({"High": c + spread, "Low": np.maximum(c - spread, 0.01), "Close": c,
                               "Volume": rng.integers(1e4, 5e7, len(idx))}, index=idx)


def demo_universe() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    syms = sorted({"".join(rng.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), rng.integers(2, 5))) for _ in range(1500)})
    return pd.DataFrame({"sym": syms, "yahoo": syms, "name": [f"Demo {s} Inc. Common Stock" for s in syms],
                         "exch": rng.choice(["Nasdaq", "NYSE", "NYSE Arca"], len(syms)),
                         "etf": rng.random(len(syms)) < 0.2})


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    uni = demo_universe() if a.demo else load_universe()
    if a.limit:
        uni = uni.head(a.limit)
    print(f"universe: {len(uni)} symbols")
    labels, bmask = load_brokers()
    meta = uni.set_index("yahoo")
    source = demo_prices if a.demo else fetch_prices

    results = {}
    for t, df in source(list(meta.index)):
        m = metrics_for(df)
        if m:
            results[t] = m
    if not results:
        sys.exit("no price data fetched - keeping the previous data.json")
    if not a.demo and len(results) < 0.5 * len(uni):
        sys.exit(f"only {len(results)}/{len(uni)} symbols fetched - keeping the previous data.json")

    asof = max(m["date"] for m in results.values())
    week_keys, week_labels = [], []
    for i in range(N_WEEKS - 1, -1, -1):
        y, w, _ = (pd.Timestamp(asof) - timedelta(weeks=i)).isocalendar()
        week_keys.append(y * 100 + w)
        week_labels.append(f"{y}-W{w:02d}")
    bp = lambda v: None if v is None else int(round(v * 1e4))      # basis points keep the files small
    buckets: dict[str, dict] = {}
    rows = []
    for t, m in results.items():
        if m["date"] < asof - timedelta(days=5):         # stale: halted / delisted
            continue
        u = meta.loc[t]
        row = [u["sym"], u["name"], u["exch"], int(bool(u["etf"])), m["close"], m["dvol"], bmask.get(u["sym"], 0)]
        for k in range(len(METRICS)):                    # amp x4, ampPrev x4, hv x4, hvPrev x4, ret x4, retPrev x4
            row += [w[k] for w in m["cur"]] + [w[k] for w in m["prev"]]
        rows.append(row)
        wk = [m["weekly"].get(k, (None, None, None)) for k in week_keys]
        b = u["sym"][0] if u["sym"][0].isalpha() else "_"
        buckets.setdefault(b, {})[u["sym"]] = [[bp(x[k]) for x in wk] for k in range(len(METRICS))]
    rows.sort(key=lambda r: r[0])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asof": pd.Timestamp(asof).strftime("%Y-%m-%d"),
        "demo": a.demo, "weeks": week_labels, "windows": WINDOWS, "metrics": METRICS, "brokers": labels, "rows": rows,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    for b, d in buckets.items():
        (WEEKLY_DIR / f"{b}.json").write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} : {len(rows)} rows, as of {pd.Timestamp(asof).date()}")


if __name__ == "__main__":
    main()
