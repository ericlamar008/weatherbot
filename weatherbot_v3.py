#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weatherbot_v3.py -- Main orchestrator for WeatherBet v3
=====================================================================================
V5 CHANGES (this version, per explicit user directives):

1. REMOVED the global shared-capital-pool / two-pass allocation entirely.
Each city/market is now sized as its OWN independent 100%-notional pool --
there is NO competition between simultaneously-signaling cities for one
shared balance. `compute_global_capital_weights` in strategy.py is no
longer called.

2. STRATEGY_VERSION stamping: every market file now records which engine
version computed it. On every scan, if a market's stored version doesn't
match the current one, it is FORCE-recomputed at least once, even if it's
about to expire (hours_left < MIN_HOURS).

Everything else (fetch_outcomes/get_clob_book_bid sell-value fetching,
parse_temp_range, resolution grading, the manual commit/lock workflow) is
UNCHANGED from the previous version.

--- WEATHERBET INTERACTIVE-LOCK FEATURE (added, per user directive) ---------
get_clob_book_bid() moved out of this file into clob_utils.py so that the
new, independent 2-hour lock-price scanner (price_monitor.py) can share the
exact same implementation instead of duplicating it. Nothing else about this
file's behavior changed -- same function, same signature, same retry-free
best-effort semantics, just imported instead of defined locally.
-----------------------------------------------------------------------------

--- PHASE 3 FIX LOG (WEATHERBOT_ROADMAP.md, یافته F3) -------------------------
`discover_new_signals()` used to build each city's "today/tomorrow/..."
date list from a single shared `datetime.now(timezone.utc)`. Near UTC
midnight this is WRONG for any city far from UTC: e.g. at 22:30 UTC, Seoul
(UTC+9) and Tel Aviv (UTC+2/+3) already consider it a NEW local calendar
day, but the bot still labeled that new day as "tomorrow" (horizon_days=1)
instead of "today" (horizon_days=0) -- confirmed directly with a reproduction
at 2026-08-26 22:30 UTC: old logic said "today" = 2026-08-26 for Seoul, but
Seoul's real local date was already 2026-08-27. Since `horizon_days` is fed
straight into forecasting.get_sigma()/get_bias(), this silently applied the
WRONG per-horizon calibration to the market.

FIX: a new helper `_local_dates_for_city(now, loc, count)` converts the
single shared UTC `now` into each city's own local calendar via its `tz`
field before building the date list. Falls back to the old UTC-based date
list (with a one-time printed warning per timezone) if the local `zoneinfo`
database isn't available on this machine (e.g. Windows without the
`tzdata` pip package) -- this NEVER crashes the bot. If you see a
`[TZ-WARN]` line in the console, run:
pip install tzdata

Only `discover_new_signals()` changed (both places it built a `dates` list).
No other function in this file changed.
=====================================================================================
Usage:
python weatherbot_v3.py backfill   # one-time: calibrate sigma+bias from history
python weatherbot_v3.py serve      # runs the scan loop AND serves dashboard.html
python weatherbot_v3.py once       # single scan cycle, then exit (no server)
python weatherbot_v3.py run        # main loop, no server
=====================================================================================
"""

import re
import sys
import json
import time
import hashlib
import threading
import functools
import http.server
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from locations import LOCATIONS, MONTHS
import forecasting as fc
import strategy as strat
import resolution as res
import dashboard as dash
from clob_utils import get_clob_book_bid

# =============================================================================
# CONFIG
# =============================================================================

with open("config.json", encoding="utf-8") as f:
    CFG = json.load(f)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
MARKETS_DIR = DATA_DIR / "markets"
MARKETS_DIR.mkdir(exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
ENTRIES_FILE = DATA_DIR / "my_entries.txt"
LOCK_REQUESTS_FILE = DATA_DIR / "lock_requests.txt"
LOCK_PROCESSED_FILE = DATA_DIR / "lock_requests_processed.json"


def _compute_strategy_version():
    """Derive market-refresh version automatically from strategy.py content."""
    try:
        strategy_path = Path(__file__).with_name("strategy.py")
        return "auto-" + hashlib.sha256(strategy_path.read_bytes()).hexdigest()[:16]
    except Exception:
        return "fallback-v10"


STRATEGY_VERSION = _compute_strategy_version()

MAX_RETRIES = 3
RETRY_DELAY_S = 3
SERVE_PORT = 8765
EVENT_FETCH_WORKERS = CFG.get("event_fetch_workers", 8)

ENTRIES_FILE_HEADER = (
    "# ============================================================================\n"
    "# my_entries.txt -- WeatherBet v3 manual entry log\n"
    "# ============================================================================\n"
    "#\n"
    "# Every time the bot locks in a new signal, it adds a line like:\n"
    "#   nyc_2026-07-10_1=no\n"
    "# If you ACTUALLY placed that trade for real, change \"no\" to \"yes\".\n"
    "# ============================================================================\n\n"
)

LOCK_REQUESTS_HEADER = (
    "# ============================================================================\n"
    "# lock_requests.txt -- WeatherBet v3 manual COMMIT log\n"
    "# ============================================================================\n"
    "# Written automatically by the dashboard's \"Lock this moment\" button.\n"
    "# ============================================================================\n\n"
)

STRATEGY_PARAMS = {
    "balance_units": CFG.get("balance_units", 100),
    "main_signal_min_units": CFG.get("main_signal_min_units", 5),
    "ladder_max_buckets": CFG.get("ladder_max_buckets", 3),
    "kelly_fraction": CFG.get("kelly_fraction", 0.25),
    "hedge_pool_size": CFG.get("hedge_pool_size", 12),
    "tail_df": CFG.get("tail_df", 5.0),
    "scenario_step": CFG.get("scenario_step", 0.5),
    "scenario_n_sigma": CFG.get("scenario_n_sigma", 4.0),
    "hedge_min_plausibility": CFG.get("hedge_min_plausibility", 0.08),
    "worst_case_prob_mass": CFG.get("worst_case_prob_mass", 0.90),
    "worst_case_max_loss_frac": CFG.get("worst_case_max_loss_frac", 0.10),
    "scenario_market_weight": CFG.get("scenario_market_weight", 0.5),
    "min_distinction_ratio": CFG.get("min_distinction_ratio", 1.5),
    "main_signal_max_units": CFG.get("main_signal_max_units", 0.30),
    "belief_prob_full_confidence": CFG.get("belief_prob_full_confidence", 0.4),
}

VC_KEY = CFG.get("vc_key", "")
MIN_VOLUME = CFG.get("min_volume", 500)
MIN_HOURS = CFG.get("min_hours", 2.0)
MAX_HOURS = CFG.get("max_hours", 72.0)
SCAN_INTERVAL = CFG.get("scan_interval", 3600)

# =============================================================================
# STATE / MARKET PERSISTENCE
# =============================================================================

def load_state():
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state.setdefault("my_balance_units", STRATEGY_PARAMS["balance_units"])
        state.setdefault("my_total_pnl_units", 0.0)
        state.setdefault("my_wins", 0)
        state.setdefault("my_losses", 0)
        return state
    return {
        "balance_units": STRATEGY_PARAMS["balance_units"], "total_pnl_units": 0.0,
        "wins": 0, "losses": 0, "my_balance_units": STRATEGY_PARAMS["balance_units"],
        "my_total_pnl_units": 0.0, "my_wins": 0, "my_losses": 0,
    }


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def market_path(city_slug, date_str):
    return MARKETS_DIR / f"{city_slug}_{date_str}.json"


def load_market(city_slug, date_str):
    p = market_path(city_slug, date_str)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def save_market(m):
    market_path(m["city"], m["date"]).write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_all_markets():
    out = []
    for f in MARKETS_DIR.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def market_key(city_slug, date_str):
    return f"{city_slug}_{date_str}"


# =============================================================================
# MANUAL ENTRY LOG
# =============================================================================

def load_entries():
    if not ENTRIES_FILE.exists():
        ENTRIES_FILE.write_text(ENTRIES_FILE_HEADER, encoding="utf-8")
        return {}
    entries = {}
    for line in ENTRIES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        entries[key.strip()] = val.strip().lower() == "yes"
    return entries


def append_new_entry_lines(new_keys):
    if not new_keys:
        return
    if not ENTRIES_FILE.exists():
        ENTRIES_FILE.write_text(ENTRIES_FILE_HEADER, encoding="utf-8")
    existing = ENTRIES_FILE.read_text(encoding="utf-8")
    lines_to_add = [f"{k}=no" for k in new_keys if f"{k}=" not in existing]
    if lines_to_add:
        with ENTRIES_FILE.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines_to_add) + "\n")


# =============================================================================
# MANUAL LOCK/COMMIT LOG
# =============================================================================

def load_lock_requests():
    if not LOCK_REQUESTS_FILE.exists():
        LOCK_REQUESTS_FILE.write_text(LOCK_REQUESTS_HEADER, encoding="utf-8")
        return {}
    requests_map = {}
    for line in LOCK_REQUESTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, ts = line.split("=", 1)
        requests_map[key.strip()] = ts.strip()
    return requests_map


def load_processed_lock_requests():
    if LOCK_PROCESSED_FILE.exists():
        try:
            return set(json.loads(LOCK_PROCESSED_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_processed_lock_requests(processed_set):
    LOCK_PROCESSED_FILE.write_text(
        json.dumps(sorted(processed_set), ensure_ascii=False), encoding="utf-8"
    )


def apply_lock_requests(now):
    requests_map = load_lock_requests()
    if not requests_map:
        return 0
    processed = load_processed_lock_requests()
    new_keys = [k for k in requests_map if k not in processed]
    if not new_keys:
        return 0
    committed_count = 0
    for key in new_keys:
        try:
            city_slug, date_str = key.rsplit("_", 1)
        except ValueError:
            processed.add(key)
            continue
        mkt = load_market(city_slug, date_str)
        if not mkt or mkt.get("status") == "resolved":
            processed.add(key)
            continue
        live_alloc = mkt.get("live_allocation")
        if not live_alloc:
            processed.add(key)
            continue
        committed = []
        for idx, a in enumerate(live_alloc, start=1):
            a2 = dict(a)
            a2["idx"] = idx
            a2["entry_key"] = f"{city_slug}_{date_str}_{idx}"
            committed.append(a2)
        mkt["committed_allocation"] = committed
        mkt["committed_at"] = now.isoformat()
        mkt["committed_full_distribution"] = mkt.get("full_distribution")
        mkt["committed_worst_case_loss_frac"] = mkt.get("worst_case_loss_frac")
        mkt["committed_best_case_pnl_units"] = mkt.get("best_case_pnl_units")
        mkt["committed_worst_case_pnl_units"] = mkt.get("worst_case_pnl_units")
        mkt["committed_success_probability"] = mkt.get("success_probability")
        mkt["committed_confidence"] = mkt.get("confidence")
        mkt["committed_balance_units"] = mkt.get("balance_units", STRATEGY_PARAMS["balance_units"])
        save_market(mkt)
        append_new_entry_lines([a["entry_key"] for a in committed])
        processed.add(key)
        committed_count += 1
        print(f"  [COMMITTED @ live snapshot] {mkt['city_name']} {date_str} x{len(committed)} positions")
    save_processed_lock_requests(processed)
    return committed_count

# =============================================================================
# POLYMARKET HELPERS
# =============================================================================

def get_polymarket_event(city_slug, month, day, year):
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=(5, 8))
            data = r.json()
            if data and isinstance(data, list) and len(data) > 0:
                return data[0]
            return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_S)
    return None


def get_polymarket_event_for_market_date(city_slug, date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year)


# NOTE: get_clob_book_bid() used to be defined here. It now lives in
# clob_utils.py (imported above) so that price_monitor.py -- the new,
# independent 2-hour lock-price scanner -- can reuse the exact same
# implementation instead of duplicating it. Behavior is 100% unchanged.


def parse_temp_range(question):
    if not question:
        return None
    q = question.replace("\u2013", "-").replace("\u2014", "-")
    num = r"(-?\d+(?:\.\d+)?)"
    if re.search(r"or below|or less|below " + num, q, re.IGNORECASE):
        m = re.search(num + r"\s*[\u00b0]?\s*[FC]\s*(?:or below|or less)", q, re.IGNORECASE)
        if m:
            return (-999.0, float(m.group(1)))
        m = re.search(r"below\s*" + num + r"\s*[\u00b0]?\s*[FC]", q, re.IGNORECASE)
        if m:
            return (-999.0, float(m.group(1)))
    if re.search(r"or higher|or more|above " + num, q, re.IGNORECASE):
        m = re.search(num + r"\s*[\u00b0]?\s*[FC]\s*(?:or higher|or more)", q, re.IGNORECASE)
        if m:
            return (float(m.group(1)), 999.0)
        m = re.search(r"above\s*" + num + r"\s*[\u00b0]?\s*[FC]", q, re.IGNORECASE)
        if m:
            return (float(m.group(1)), 999.0)
    m = re.search(r"between\s*" + num + r"\s*-\s*" + num + r"\s*[\u00b0]?\s*[FC]", q, re.IGNORECASE)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(num + r"\s*(?:-|to)\s*" + num + r"\s*[\u00b0]?\s*[FC]", q, re.IGNORECASE)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r"be\s*" + num + r"\s*[\u00b0]?\s*[FC]\s*on", q, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    m = re.search(num + r"\s*[\u00b0]\s*[FC]\b", q, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None


def hours_to_resolution(end_date_str):
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999.0


def fetch_outcomes(event):
    """Fast market extraction.

    V10: do NOT fetch CLOB bids here. The old eager implementation made two
    sequential CLOB HTTP calls for every bucket in every market. We retain
    token IDs and fetch bids later, only for legs actually allocated.
    """
    outcomes = []
    for market in event.get("markets", []):
        question = market.get("question", "")
        mid = str(market.get("id", ""))
        volume = float(market.get("volume", 0))
        rng = parse_temp_range(question)
        if not rng:
            continue
        try:
            prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
            yes_price = float(prices[0])
            no_price = float(prices[1]) if len(prices) > 1 else round(1.0 - yes_price, 4)
        except Exception:
            continue

        try:
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            yes_token_id = token_ids[0] if len(token_ids) >= 1 else None
            no_token_id = token_ids[1] if len(token_ids) >= 2 else None
        except Exception:
            yes_token_id, no_token_id = None, None

        outcomes.append({
            "question": question, "market_id": mid, "range": rng,
            "yes_price": round(yes_price, 4),
            "no_price": round(no_price, 4),
            "volume": round(volume, 0),
            "spread": round(abs((yes_price + no_price) - 1.0), 4),
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
        })
    outcomes.sort(key=lambda x: x["range"][0])
    return outcomes


def _fetch_sell_values_for_allocation(allocation, tradable_dist, pool=None):
    """Fetch bid prices only for actual allocation legs, in parallel."""
    by_market_id = {d["market_id"]: d for d in tradable_dist}
    jobs = []
    for index, leg in enumerate(allocation):
        bucket = by_market_id.get(leg["market_id"])
        if not bucket:
            continue
        token_id = bucket.get("yes_token_id") if leg["side"] == "YES" else bucket.get("no_token_id")
        if token_id:
            jobs.append((index, token_id))

    if not jobs:
        for leg in allocation:
            leg["sell_value"] = None
        return

    def collect(pool_obj):
        futures = {pool_obj.submit(get_clob_book_bid, token_id): index for index, token_id in jobs}
        values = {}
        for future in as_completed(futures):
            index = futures[future]
            try:
                values[index] = future.result()
            except Exception:
                values[index] = None
        return values

    if pool is None:
        with ThreadPoolExecutor(max_workers=EVENT_FETCH_WORKERS) as local_pool:
            values = collect(local_pool)
    else:
        values = collect(pool)

    for index, leg in enumerate(allocation):
        leg["sell_value"] = values.get(index)

# =============================================================================
# PHASE 1 -- DISCOVERY + LIVE REFRESH
# V5: single-pass, no shared-pool competition. Every market is sized
# independently against its OWN fixed 100-unit notional budget.
# =============================================================================

_tz_warned = set()


def _local_dates_for_city(now, loc, count=4):
    """Returns `count` date strings (YYYY-MM-DD) starting from "today" in
    THIS CITY'S OWN local timezone (loc["tz"]), not global UTC.

    FIX (Phase 3 / F3, WEATHERBOT_ROADMAP.md): see module docstring for the
    full before/after reproduction. Falls back to the old UTC-based date
    list (printing a one-time warning per timezone) if `zoneinfo` can't
    resolve the timezone on this machine (e.g. Windows without the
    `tzdata` package) -- this must never crash the bot.
    """
    if ZoneInfo is not None:
        try:
            local_now = now.astimezone(ZoneInfo(loc["tz"]))
            return [(local_now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count)]
        except Exception:
            if loc["tz"] not in _tz_warned:
                print(f"  [TZ-WARN] could not resolve timezone {loc['tz']} for {loc['name']} "
                      f"(run: pip install tzdata) -- falling back to UTC date for now")
            _tz_warned.add(loc["tz"])
    return [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count)]


def discover_new_signals(now, state):
    new_positions = 0

    fetch_jobs = []
    for city_slug in LOCATIONS:
        loc = LOCATIONS[city_slug]
        # FIX (Phase 3): was dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
        dates = _local_dates_for_city(now, loc, 4)
        for i, date in enumerate(dates):
            dt = datetime.strptime(date, "%Y-%m-%d")
            fetch_jobs.append((city_slug, date, i, dt))

    events_by_job = {}
    with ThreadPoolExecutor(max_workers=EVENT_FETCH_WORKERS) as pool:
        future_to_job = {
            pool.submit(get_polymarket_event, city_slug, MONTHS[dt.month - 1], dt.day, dt.year):
                (city_slug, date, i, dt)
            for (city_slug, date, i, dt) in fetch_jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                events_by_job[job] = future.result()
            except Exception:
                events_by_job[job] = None

    for city_slug, loc in LOCATIONS.items():
        print(f"  -> {loc['name']}...", end=" ", flush=True)
        # FIX (Phase 3): was dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
        dates = _local_dates_for_city(now, loc, 4)

        for i, date in enumerate(dates):
            dt = datetime.strptime(date, "%Y-%m-%d")
            event = events_by_job.get((city_slug, date, i, dt))
            if not event:
                continue

            end_date = event.get("endDate", "")
            hours = hours_to_resolution(end_date) if end_date else 0

            mkt = load_market(city_slug, date) or {
                "city": city_slug, "city_name": loc["name"], "date": date,
                "unit": loc["unit"], "status": "open",
                "created_at": now.isoformat(), "committed_allocation": None,
            }

            if mkt["status"] == "resolved":
                continue

            mkt["hours_left"] = round(hours, 1)
            mkt["event_end_date"] = end_date
            mkt["last_scan"] = now.isoformat()

            needs_version_refresh = mkt.get("strategy_version") != STRATEGY_VERSION

            if (hours < MIN_HOURS or hours > MAX_HOURS) and not needs_version_refresh:
                save_market(mkt)
                continue

            outcomes = fetch_outcomes(event)
            if not outcomes:
                save_market(mkt)
                continue

            event_volume = float(event.get("volume", 0) or 0)
            if event_volume < MIN_VOLUME:
                mkt["skipped_reason"] = f"event volume {event_volume:.0f} < min_volume {MIN_VOLUME}"
                save_market(mkt)
                continue

            dist_precheck = [d for d in outcomes if d["volume"] > 0]
            if not dist_precheck:
                save_market(mkt)
                continue

            members = fc.build_combined_distribution(city_slug, loc, date)
            mean, sigma = fc.build_calibrated_distribution(members, city_slug, i, loc["unit"])

            if mean is None:
                mkt["skipped_reason"] = "ensemble forecast unavailable (mean is None)"
                save_market(mkt)
                continue

            dist = fc.full_bucket_distribution(mean, sigma, outcomes)

            tradable_dist = [d for d in dist if d["volume"] > 0]
            if not tradable_dist:
                save_market(mkt)
                continue

            portfolio = strat.build_portfolio(
                city_slug, tradable_dist, STRATEGY_PARAMS, mean=mean, sigma=sigma
            )

            if portfolio["allocation"]:
                _fetch_sell_values_for_allocation(portfolio["allocation"], tradable_dist)

            was_new_signal = bool(portfolio["allocation"]) and not mkt.get("live_allocation")

            mkt.update({
                "horizon_days": i, "forecast_mean": mean, "sigma": sigma,
                "full_distribution": portfolio["full_distribution"],
                "live_allocation": portfolio["allocation"],
                "total_allocated_units": portfolio.get("total_allocated_units"),
                "portfolio_score": portfolio.get("portfolio_score"),
                "worst_case_loss_frac": portfolio.get("worst_case_loss_frac"),
                "best_case_pnl_units": portfolio.get("best_case_pnl_units"),
                "worst_case_pnl_units": portfolio.get("worst_case_pnl_units"),
                "success_probability": portfolio.get("success_probability"),
                "confidence": portfolio.get("confidence"),
                "balance_units": STRATEGY_PARAMS["balance_units"],
                "strategy_version": STRATEGY_VERSION,
            })
            mkt.pop("skipped_reason", None)

            if was_new_signal:
                new_positions += 1
                print(f"  [NEW SIGNAL x{len(portfolio['allocation'])}] {mkt['city_name']} {date}")

            save_market(mkt)

        print("ok")

    return new_positions


# =============================================================================
# PHASE 2 -- COUNTDOWN REFRESH
# =============================================================================

def refresh_all_locked_markets(now):
    open_markets = [m for m in load_all_markets() if m.get("status") != "resolved"]
    if not open_markets:
        return
    with ThreadPoolExecutor(max_workers=EVENT_FETCH_WORKERS) as pool:
        future_to_mkt = {
            pool.submit(get_polymarket_event_for_market_date, m["city"], m["date"]): m
            for m in open_markets
        }
        events_by_mkt_key = {}
        for future in as_completed(future_to_mkt):
            mkt = future_to_mkt[future]
            key = (mkt["city"], mkt["date"])
            try:
                events_by_mkt_key[key] = future.result()
            except Exception:
                events_by_mkt_key[key] = None
    for mkt in open_markets:
        event = events_by_mkt_key.get((mkt["city"], mkt["date"]))
        if event:
            end_date = event.get("endDate", mkt.get("event_end_date", ""))
            hours = hours_to_resolution(end_date) if end_date else mkt.get("hours_left", 999)
            mkt["hours_left"] = round(hours, 1)
            mkt["event_end_date"] = end_date
        elif mkt.get("event_end_date"):
            hours = hours_to_resolution(mkt["event_end_date"])
            mkt["hours_left"] = round(hours, 1)
        else:
            continue
        mkt["last_scan"] = now.isoformat()
        save_market(mkt)


# =============================================================================
# PHASE 3 -- RESOLUTION (unchanged)
# =============================================================================

def resolve_expired_markets(state):
    resolved_count = 0
    entries = load_entries()
    for mkt in load_all_markets():
        if mkt.get("status") in ("resolved", "resolved_no_signal"):
            continue
        grading_allocation = mkt.get("committed_allocation") or mkt.get("live_allocation")
        hours_left = mkt.get("hours_left", 999)
        if not grading_allocation:
            if hours_left > 0.5:
                continue
            loc = LOCATIONS[mkt["city"]]
            dt = datetime.strptime(mkt["date"], "%Y-%m-%d")
            settlement = res.get_polymarket_settlement(mkt["city"], MONTHS[dt.month - 1], dt.day, dt.year)
            if not settlement:
                mkt["status"] = "expired_no_signal"
                save_market(mkt)
                continue
            settlement_temp = res.get_actual_temp_from_settlement(mkt, settlement)
            mkt["actual_temp"] = settlement_temp if settlement_temp is not None else res.get_display_temp(loc, mkt["date"], VC_KEY)
            mkt["status"] = "resolved_no_signal"
            mkt["pnl_units"] = None
            mkt["my_pnl_units"] = None
            save_market(mkt)
            continue
        if hours_left > 0.5:
            continue
        loc = LOCATIONS[mkt["city"]]
        dt = datetime.strptime(mkt["date"], "%Y-%m-%d")
        settlement = res.get_polymarket_settlement(mkt["city"], MONTHS[dt.month - 1], dt.day, dt.year)
        if settlement is None:
            continue
        all_settled = all(a["market_id"] in settlement for a in grading_allocation)
        if not all_settled:
            continue
        settlement_temp = res.get_actual_temp_from_settlement(mkt, settlement)
        mkt["actual_temp"] = settlement_temp if settlement_temp is not None else res.get_display_temp(loc, mkt["date"], VC_KEY)
        raw_pnl_units = 0.0
        raw_my_pnl_units = 0.0
        any_user_entry = False
        for a in grading_allocation:
            yes_won = settlement[a["market_id"]]
            won = yes_won if a["side"] == "YES" else (not yes_won)
            trade_pnl = (a["units"] * (1.0 / a["price"] - 1.0)) if won else -a["units"]
            a["won"] = won
            a["pnl_units"] = round(trade_pnl, 2)
            raw_pnl_units += trade_pnl
            user_entered = entries.get(a.get("entry_key", ""), False)
            a["user_entered"] = user_entered
            if user_entered:
                any_user_entry = True
                raw_my_pnl_units += trade_pnl
        pnl_units = round(raw_pnl_units, 2)
        my_pnl_units = round(raw_my_pnl_units, 2)
        mkt["status"] = "resolved"
        mkt["graded_against"] = "committed" if mkt.get("committed_allocation") else "live_fallback"
        mkt["resolved_outcome"] = "win" if pnl_units >= 0 else "loss"
        mkt["pnl_units"] = pnl_units
        mkt["my_pnl_units"] = my_pnl_units if any_user_entry else None
        mkt["graded_allocation"] = grading_allocation
        state["balance_units"] = round(state["balance_units"] + pnl_units, 2)
        state["total_pnl_units"] = round(state["total_pnl_units"] + pnl_units, 2)
        if pnl_units >= 0:
            state["wins"] += 1
        else:
            state["losses"] += 1
        if any_user_entry:
            state["my_balance_units"] = round(state["my_balance_units"] + my_pnl_units, 2)
            state["my_total_pnl_units"] = round(state["my_total_pnl_units"] + my_pnl_units, 2)
            if my_pnl_units >= 0:
                state["my_wins"] += 1
            else:
                state["my_losses"] += 1
        save_market(mkt)
        resolved_count += 1
    return resolved_count


def scan_and_update():
    state = load_state()
    now = datetime.now(timezone.utc)
    new_positions = discover_new_signals(now, state)
    refresh_all_locked_markets(now)
    committed = apply_lock_requests(now)
    resolved_count = resolve_expired_markets(state)
    save_state(state)
    fc.update_calibration_from_live(load_all_markets(), LOCATIONS)
    return new_positions, resolved_count, committed


def _start_local_server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(Path.cwd()))
    httpd = http.server.ThreadingHTTPServer(("localhost", SERVE_PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def run_once():
    t_start = time.perf_counter()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] scanning {len(LOCATIONS)} cities...")
    new_pos, resolved, committed = scan_and_update()
    state = load_state()
    dash_path = dash.build_dashboard(state, load_all_markets(), LOCATIONS)
    print(f"  new signals: {new_pos} | committed: {committed} | resolved: {resolved}")
    print(f"  dashboard updated: {dash_path}")
    print(f"  mark your real trades in: {ENTRIES_FILE}")


def run_loop():
    print(f"WeatherBet v3 -- {len(LOCATIONS)} cities | scan every {SCAN_INTERVAL // 60} min")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\nStopped by user.")
            break
        except Exception as e:
            print(f"  Error: {e} -- retrying in 60s")
            time.sleep(60)
            continue
        time.sleep(SCAN_INTERVAL)


def run_serve():
    httpd = _start_local_server()
    print(f"WeatherBet v3 -- local server running at http://localhost:{SERVE_PORT}/dashboard.html")
    print("Open that URL (not the file directly) so the 'Lock this moment' button "
          "can attach to data/lock_requests.txt once.")
    try:
        run_loop()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "backfill":
        print("Running one-time historical calibration backfill...")
        fc.backfill_calibration(LOCATIONS, lookback_days=CFG.get("calibration_lookback_days", 180))
        print("Backfill complete. You can now run: python weatherbot_v3.py serve")
    elif cmd == "once":
        run_once()
    elif cmd == "serve":
        run_serve()
    elif cmd == "run":
        run_loop()
    else:
        print("Usage: python weatherbot_v3.py [backfill|once|serve|run]")
