"""
price_monitor.py -- اسکنر نیم‌ساعتهٔ مستقل برای قفل‌های باز + پیام تلگرام.
=====================================================================================
تغییر این نسخه:
  ۱) زمان باقی‌مانده تا resolve حالا کنار همهٔ بازارها نشان داده می‌شود
     (نه فقط وقتی کمتر از ۳ ساعت مانده -- آن حالت فقط ⭐/⏰ را کنترل می‌کند).
  ۲) متن زمان باقی‌مانده به انگلیسی نوشته می‌شود ("2h 15m to resolve")
     تا با فارسی قاطی نشود و به‌هم‌ریختگی راست‌به‌چپ/چپ‌به‌راست پیش نیاید.
  ۳) (فاز ۲ نقشه‌راه) آستانهٔ هشدار سود/ضرر از سنت مطلق به درصد تغییر کرد --
     TAKE_PROFIT_PCT=20.0 / STOP_LOSS_PCT=10.0.
  ۴) (اصلاح جدید) هر بار که قیمت یک قفل با موفقیت گرفته می‌شود، فیلد
     "last_checked_at" (زمان ISO این بررسی) هم روی همان قفل ثبت می‌شود --
     این برای نمایش ستون «آخرین به‌روزرسانی» در سکشن قفل‌های داشبورد
     تعاملی لازم است (dashboard_simple.py آن را می‌خواند).

--- روادراه: فاز C -- زمان محلی به‌جای event_end_date خام (این نسخه) --------
مشکل قبلی: hours_left از تفاضل event_end_date منهای now محاسبه می‌شد که
بعد از گذشتنش صفر می‌ماند و باعث هشدار تکراری "0h 0m to resolve" می‌شد،
حتی وقتی بازار هنوز واقعاً باز بود (مثل Ankara/Dallas).

اصلاح: از ماژول مشترک market_time.py استفاده می‌شود تا:
  - hours_left از «پایان روز هدف در timezone شهر» محاسبه شود، نه از
    event_end_date خام.
  - هشدار ⏰ فقط در بازهٔ باز (0 < remaining <= NEAR_RESOLVE_HOURS) فعال شود.
  - بعد از پایان روز محلی، به‌جای "0h 0m to resolve"، عبارت واقعی
    "awaiting official settlement" نمایش داده شود.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from clob_utils import get_gamma_event_prices
import history_manager
from market_time import local_day_status, is_near_local_day_end

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

TAKE_PROFIT_PCT = 20.0
STOP_LOSS_PCT = 10.0
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
    """به انگلیسی، تا با متن فارسی قاطی نشود و به‌هم‌ریختگی RTL پیش نیاید."""
    total_minutes = int(round(hours * 60))
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m}m to resolve"


def _local_day_label(hours_left, awaiting_settlement):
    """پس از پایان روز محلی هرگز 0h 0m نمایش نمی‌دهد؛ به‌جایش وضعیت واقعی."""
    if awaiting_settlement:
        return "awaiting official settlement"
    return _hours_left_str(hours_left)


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
        loc = LOCATIONS.get(city, {})

        timing = local_day_status(date, loc, now)
        awaiting_settlement = timing["kind"] == "awaiting_settlement"
        hours_left = timing["remaining_seconds"] / 3600.0

        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            gamma_prices = get_gamma_event_prices(city, MONTHS[dt.month - 1], dt.day, dt.year)
        except Exception:
            gamma_prices = {}

        near_resolve = is_near_local_day_end(date, loc, now, hours=NEAR_RESOLVE_HOURS)
        star = False
        lines = []

        for l in group:
            current = gamma_prices.get(str(l["market_id"]))
            l["last_price"] = current
            if current is None:
                lines.append("   \u26AA قیمت فعلی در دسترس نیست")
                continue

            l["last_checked_at"] = now.isoformat()

            entry = l["entry_price"]
            diff_cents = round((current - entry) * 100, 1)
            pct = round((current - entry) / entry * 100, 1) if entry else 0.0
            l["last_pct"] = pct

            triggered = pct >= TAKE_PROFIT_PCT or pct <= -STOP_LOSS_PCT
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
        title += f"  ({_local_day_label(hours_left, awaiting_settlement)})"

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
