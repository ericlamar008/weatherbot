"""
history_manager.py -- بستن خودکار قفل‌های resolve‌شده + ساخت تاریخچه و CSV.
=====================================================================================
کاملاً جدید و مستقل. resolution.py فعلی را فقط می‌خواند، هیچ تغییری در آن
نمی‌دهد و weatherbot_v3.py یا strategy.py را صدا نمی‌زند.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from locations import LOCATIONS, MONTHS
except ImportError:
    LOCATIONS, MONTHS = {}, [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]

import resolution as res

MARKETS_DIR = Path("data/markets")
CSV_FILE = Path("data/lock_history.csv")

CSV_HEADERS = [
    "تاریخ", "شهر", "دمای سیگنال", "دمای نهایی",
    "قیمت خرید (سنت)", "قیمت فروش (سنت)", "برآیند (%)",
]


def _load_market(city, date):
    p = MARKETS_DIR / f"{city}_{date}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_range(market, market_id):
    if not market:
        return None
    for b in market.get("full_distribution", []):
        if str(b.get("market_id")) == str(market_id):
            return b.get("range")
    return None


def _label_for_range(rng, unit_sym):
    if not rng:
        return "?"
    low, high = rng
    if low <= -998 and high >= 998:
        return f"?{unit_sym}"
    if low <= -998:
        return f"\u2264{high}{unit_sym}"
    if high >= 998:
        return f"\u2265{low}{unit_sym}"
    if low == high:
        return f"{low}{unit_sym}"
    return f"{low}-{high}{unit_sym}"


def check_and_close_resolved_locks(locks, now=None):
    """
    قفل‌های بازی که بازارشان قبلاً resolve شده را می‌بندد. اگر پلی‌مارکت در
    دسترس نبود یا آن باکت خاص هنوز settle نشده، همان‌طور باز رها می‌شود.
    برمی‌گرداند تعداد قفل‌هایی که در همین دور بسته شدند.
    """
    closed_count = 0
    for l in locks:
        if l.get("status") != "open":
            continue

        city, date, market_id = l["city"], l["date"], str(l["market_id"])
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            continue

        try:
            settlement = res.get_polymarket_settlement(city, MONTHS[dt.month - 1], dt.day, dt.year)
        except Exception:
            settlement = None

        if not settlement:
            continue
        if market_id not in settlement:
            continue

        won = settlement[market_id]
        market = _load_market(city, date)
        actual_temp = None
        if market:
            try:
                actual_temp = res.get_actual_temp_from_settlement(market, settlement)
            except Exception:
                actual_temp = None

        l["status"] = "closed_resolved"
        l["exit_price"] = 1.0 if won else 0.0
        l["closed_at"] = (now or datetime.now(timezone.utc)).isoformat()
        l["close_reason"] = "auto_resolved"
        l["actual_temp"] = actual_temp
        closed_count += 1

    return closed_count


def write_history_csv(locks):
    """از همهٔ قفل‌های بسته‌شده (دستی یا خودکار) فایل CSV می‌سازد."""
    rows = []
    for l in locks:
        if l.get("status") not in ("closed_manual", "closed_resolved"):
            continue

        city = l["city"]
        city_name = LOCATIONS.get(city, {}).get("name", city)
        unit_sym = LOCATIONS.get(city, {}).get("unit", "")
        market = _load_market(city, l["date"])
        rng = _find_range(market, l["market_id"])
        signal_label = _label_for_range(rng, unit_sym)

        final_temp = l.get("actual_temp")
        final_temp_str = f"{final_temp}{unit_sym}" if final_temp is not None else ""

        buy_cents = round(l["entry_price"] * 100, 1)
        sell_cents = round(l["exit_price"] * 100, 1) if l.get("exit_price") is not None else ""

        outcome_pct = ""
        if l.get("exit_price") is not None and l.get("entry_price"):
            outcome_pct = round((l["exit_price"] - l["entry_price"]) / l["entry_price"] * 100, 1)

        rows.append([l["date"], city_name, signal_label, final_temp_str, buy_cents, sell_cents, outcome_pct])

    rows.sort(key=lambda r: r[0], reverse=True)

    CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CSV_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        writer.writerows(rows)
    return len(rows)


def get_history_rows(locks):
    """همان دادهٔ CSV را به‌شکل لیست دیکشنری برمی‌گرداند -- برای رندر در simple.html."""
    out = []
    for l in locks:
        if l.get("status") not in ("closed_manual", "closed_resolved"):
            continue
        city = l["city"]
        city_name = LOCATIONS.get(city, {}).get("name", city)
        unit_sym = LOCATIONS.get(city, {}).get("unit", "")
        market = _load_market(city, l["date"])
        rng = _find_range(market, l["market_id"])
        signal_label = _label_for_range(rng, unit_sym)
        final_temp = l.get("actual_temp")
        outcome_pct = None
        if l.get("exit_price") is not None and l.get("entry_price"):
            outcome_pct = round((l["exit_price"] - l["entry_price"]) / l["entry_price"] * 100, 1)
        out.append({
            "date": l["date"],
            "city_name": city_name,
            "signal_label": signal_label,
            "final_temp": f"{final_temp}{unit_sym}" if final_temp is not None else "-",
            "buy_cents": round(l["entry_price"] * 100),
            "sell_cents": round(l["exit_price"] * 100) if l.get("exit_price") is not None else None,
            "outcome_pct": outcome_pct,
        })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out
