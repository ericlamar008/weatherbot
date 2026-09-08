"""
clob_utils.py -- Shared price-fetching helpers for WeatherBet's new lock feature.
=====================================================================================
UPDATE (bug fix, real-world tested): the 2-hour price monitor used to track
price movement via get_clob_book_bid() (best order-book bid on Polymarket's
CLOB). Real usage showed this can diverge sharply from the price shown on
Polymarket's own website close to resolution -- order-book depth thins out
near settlement (order books can hold stale/low bids), so "best bid" looked
like it dropped 8.6% on a bucket that actually WON (should have trended
toward 100). Confirmed directly with real locked_signals.json data: entry
0.58 -> tracked 0.53 (looked like a loss) -> actual resolution exit 1.0 (won).

FIX: added get_gamma_event_prices(), which reads the SAME field
(`outcomePrices` from gamma-api.polymarket.com) that the main bot's own
fetch_outcomes() uses to display "yes_price" -- this matches what you
actually see on the Polymarket website. price_monitor.py now uses this
for its 2-hour drift check instead of the CLOB order-book bid.

get_clob_book_bid() is kept (unchanged, still used by weatherbot_v3.py's
existing sell-value display) -- nothing about the ORIGINAL bot's behavior
changed.

--- HARDENING PATCH (this version, per explicit user directive) -------------
get_gamma_event_prices() used to make exactly ONE attempt to reach
gamma-api.polymarket.com. Any transient DNS hiccup or brief connection drop
caused an immediate empty-dict return for that entire city/date, which
price_monitor.py then displays as "price unavailable" for that 30-minute
cycle only (self-corrects next cycle -- never a wrong price, just a missed
one). To reduce how often this happens, the same retry pattern already used
by resolution.py's get_polymarket_settlement() (3 attempts, 3s delay) has
been added here too, kept consistent rather than inventing a new style.
Nothing about the returned data shape or the caller's behavior changed.
-----------------------------------------------------------------------------
"""
import json
import time
import requests

TIMEOUT = (5, 8)
MAX_RETRIES = 3
RETRY_DELAY_S = 3

def get_clob_book_bid(token_id):
    """
    UNCHANGED from before. Best (highest) current bid price for a Polymarket
    CLOB token_id, or None on any failure/missing data. Still used by
    weatherbot_v3.py's existing sell-value display -- do not remove.
    """
    if not token_id:
        return None
    try:
        r = requests.get(
            f"https://clob.polymarket.com/book?token_id={token_id}",
            timeout=TIMEOUT,
        )
        data = r.json()
        bids = data.get("bids", [])
        if not bids:
            return None
        return round(max(float(b["price"]) for b in bids), 4)
    except Exception:
        return None

def get_gamma_event_prices(city_slug, month, day, year):
    """
    Fetches the live event from gamma-api.polymarket.com and returns
    { market_id (str): yes_price (float) } for every bucket in that event --
    the SAME price field shown on the Polymarket website. One HTTP call
    covers the whole event (all buckets for that city/date), not one call
    per bucket.

    Retries up to MAX_RETRIES times on any request/connection error (DNS
    hiccup, transient timeout, etc.) before giving up -- a single transient
    network error should not needlessly blank out an entire city's prices
    for a 30-minute cycle.

    Returns {} on any failure (including after retries are exhausted) --
    never raises, never crashes the caller.
    """
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    data = None
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=TIMEOUT)
            data = r.json()
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_S)

    if last_err is not None:
        return {}

    if not data or not isinstance(data, list) or len(data) == 0:
        return {}

    event = data[0]
    prices = {}
    for market in event.get("markets", []):
        mid = str(market.get("id", ""))
        try:
            outcome_prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
            prices[mid] = float(outcome_prices[0])
        except Exception:
            continue
    return prices
