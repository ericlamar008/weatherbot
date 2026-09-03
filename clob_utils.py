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
"""
import json
import requests

TIMEOUT = (5, 8)


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

    Returns {} on any failure -- never raises, never crashes the caller.
    """
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=TIMEOUT)
        data = r.json()
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
    except Exception:
        return {}
