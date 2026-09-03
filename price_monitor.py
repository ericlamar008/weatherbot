"""
price_monitor.py -- اسکنر دوساعتهٔ مستقل برای قفل‌های باز + پیام تلگرام.
=====================================================================================
نسخهٔ اصلاح‌شده (رفع باگ واقعی + تمیزکردن پیام):

باگ واقعی که رفع شد: قیمت قبلاً از "بهترین bid در CLOB order book" گرفته
می‌شد. نزدیک resolve، عمق order book خیلی کم می‌شود و می‌تواند قیمتی کاملاً
متفاوت از آن‌چه در خود سایت پلی‌مارکت می‌بینید نشان دهد -- دقیقاً همین باعث
شد یک باکت که در نهایت با ۱۰۰ سنت برنده شد، در گزارش‌های میانی به‌اشتباه رو
به افت نشان داده شود. الان قیمت از همان فیلدی گرفته می‌شود که خود سایت
پلی‌مارکت نشان می‌دهد (outcomePrices از gamma-api) -- همان منبعی که ربات
اصلی هم برای yes_price استفاده می‌کند.

تمیزکردن پیام: اسم شهر حالا خودش لینک است (با فرمت HTML تلگرام) -- دیگر
لینک جدا در پیام نیست. ساختار پیام هم مرتب‌تر و با فاصله‌گذاری واضح‌تر شد.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from clob_utils import get_gamma_event_prices
import history_manager

try:
    from locations import LOCATIONS, MONTHS
except ImportError:
    LOCATIONS, MONTHS = {}, [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]

LOCKS_FILE = Path("data/locked_signals.json")
MARKETS_DIR = Path("data/markets")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TAKE_PROFIT_CENTS = 20
STOP_LOSS_CENTS = 10
NEAR_RESOLVE_HOURS = 3.0


def _load_locks():
    if LOCKS_FILE.exists():
        try:
            return json.loads(LOCKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_locks(locks):
    LOCKS_FILE.write_text(json.dumps(locks, indent=2, ensure_ascii=False), encoding="utf-8")


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


def _hours_left_str(hours):
    total_minutes = int(round(hours * 60))
    h, m = divmod(total_minutes, 60)
    return f"{h} ساعت و {m} دقیقه"


def _build_polymarket_url(city, date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        month = MONTHS[dt.month - 1]
        return f"https://polymarket.com/event/highest-temperature-in-{city}-on-{month}-{dt.day}-{dt.year}"
    except Exception:
        return "https://polymarket.com"


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[price_monitor] توکن یا چت آیدی تلگرام تنظیم نشده -- پیام فقط چاپ می‌شود:\n", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        print(f"[price_monitor] هشدار: ارسال تلگرام ناموفق بود: {e}")


def build_message(now, locks):
    open_locks = [l for l in locks if l.get("status") == "open"]
    if not open_locks:
        return None

    by_city = {}
    for l in open_locks:
        by_city.setdefault((l["city"], l["date"]), []).append(l)

    any_trigger = False
    city_blocks = []

    for (city, date), group in sorted(by_city.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        market = _load_market(city, date)

        hours_left = None
        if market and market.get("event_end_date"):
            try:
                end = datetime.fromisoformat(market["event_end_date"])
                hours_left = max(0.0, (end - now).total_seconds() / 3600)
            except Exception:
                hours_left = None

        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            gamma_prices = get_gamma_event_prices(city, MONTHS[dt.month - 1], dt.day, dt.year)
        except Exception:
            gamma_prices = {}

        near_resolve = hours_left is not None and hours_left <= NEAR_RESOLVE_HOURS
        star = False
        lines = []

        for l in group:
            current = gamma_prices.get(str(l["market_id"]))
            l["last_price"] = current
            if current is None:
                lines.append("   \u26AA قیمت فعلی در دسترس نیست")
                continue

            entry = l["entry_price"]
            diff_cents = round((current - entry) * 100, 1)
            pct = (current - entry) / entry * 100 if entry else 0.0
            l["last_pct"] = round(pct, 1)

            triggered = diff_cents >= TAKE_PROFIT_CENTS or diff_cents <= -STOP_LOSS_CENTS
            if triggered:
                star = True
                any_trigger = True

            arrow = "\u2191" if diff_cents >= 0 else "\u2193"
            color = "\U0001F7E2" if diff_cents >= 0 else "\U0001F534"

            rng = _find_range(market, l["market_id"])
            unit_sym = market.get("unit", "") if market else ""
            label = _label_for_range(rng, unit_sym)

            lines.append(
                f"   {color} <b>{label}</b>: {int(round(entry * 100))}\u00a2 \u2192 "
                f"{int(round(current * 100))}\u00a2  ({pct:+.0f}% {arrow})"
            )

        if near_resolve:
            any_trigger = True

        marks = ("\u2B50" if star else "") + (" \u23F0" if near_resolve else "")
        name = LOCATIONS.get(city, {}).get("name", city)
        link = _build_polymarket_url(city, date)
        title = f"<a href=\"{link}\">{name}</a> \u2014 {date}"
        if marks:
            title = f"{marks} {title}"
        if near_resolve:
            title += f" ({_hours_left_str(hours_left)} تا resolve)"

        city_blocks.append(title + "\n" + "\n".join(lines))

    if not any_trigger:
        return None

    header_line = f"\U0001F4CA <b>وضعیت قفل‌ها</b> \u2014 {now.strftime('%Y-%m-%d %H:%M')} UTC"
    return header_line + "\n\n" + "\n\n".join(city_blocks)


def check_all():
    now = datetime.now(timezone.utc)
    locks = _load_locks()

    n_resolved = history_manager.check_and_close_resolved_locks(locks, now)
    if n_resolved:
        print(f"[price_monitor] {n_resolved} قفل به‌طور خودکار با resolve شدن بازار بسته شد")

    message = build_message(now, locks)

    _save_locks(locks)
    history_manager.write_history_csv(locks)

    if message:
        send_telegram(message)
    else:
        print("[price_monitor] هیچ محرکی فعال نشد -- طبق قانون، سکوت کامل.")


if __name__ == "__main__":
    check_all()
