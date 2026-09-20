"""
price_monitor.py -- اسکنر نیم‌ساعتهٔ مستقل برای قفل‌های باز + پیام تلگرام.
=====================================================================================
تغییرات پیشین:
۱) زمان باقی‌مانده تا resolve حالا کنار همهٔ بازارها نشان داده می‌شود
   (نه فقط وقتی کمتر از ۳ ساعت مانده -- آن حالت فقط ⭐/⏰ را کنترل می‌کند).
۲) متن زمان باقی‌مانده به انگلیسی نوشته می‌شود ("2h 15m to resolve")
   تا با فارسی قاطی نشود و به‌هم‌ریختگی راست‌به‌چپ/چپ‌به‌راست پیش نیاید.
۳) (فاز ۲ نقشه‌راه قدیمی) آستانهٔ هشدار سود/ضرر از سنت مطلق به درصد تغییر
   کرد -- TAKE_PROFIT_PCT=20.0 / STOP_LOSS_PCT=10.0.
۴) هر بار که قیمت یک قفل با موفقیت گرفته می‌شود، فیلد "last_checked_at"
   (زمان ISO این بررسی) هم روی همان قفل ثبت می‌شود.

--- روادراه: فاز C -- زمان محلی به‌جای event_end_date خام --------
مشکل قبلی: hours_left از تفاضل event_end_date منهای now محاسبه می‌شد که
بعد از گذشتنش صفر می‌ماند و باعث هشدار تکراری "0h 0m to resolve" می‌شد.
اصلاح: از ماژول مشترک market_time.py استفاده می‌شود تا hours_left از
«پایان روز هدف در timezone شهر» محاسبه شود، هشدار ⏰ فقط در بازهٔ باز فعال
شود، و بعد از پایان روز محلی عبارت واقعی "awaiting official settlement"
نمایش داده شود.

--- FIX: لینک شهر در پیام تلگرام گم شده بود ----------------------
اسم شهر حالا با تگ HTML لنگر <a href="...">...</a> واقعاً به صفحهٔ Polymarket
همان بازار لینک می‌شود.

--- PATCH (فاز ۳ نقشه‌راه قدیم، زیرگام ۳-ب) ----------------------------------
build_message() دیگر فقط زمانی None برمی‌گرداند که اصلاً هیچ قفل بازی وجود
نداشته باشد. check_all() دیگر مستقیماً send_telegram() را صدا نمی‌زند -- متن
وضعیت در data/last_price_status.txt نوشته می‌شود؛ Workflow آن را با لینک
یکتای تازه ترکیب و یک‌جا می‌فرستد.

--- PATCH (فاز ۶ نقشه‌راه -- بازار دمای کمینه) -------------------------------
تصمیم تأییدشده با کاربر: یک پیام واحد، دو سکشن مجزا با اموجی گرم/سرد.
build_message() حالا قفل‌های باز را بر اساس market_type به دو گروه
(max/min) تقسیم می‌کند و هرکدام را با _build_city_blocks() (تابع داخلی
جدید که منطق قبلی حلقهٔ ساخت بلوک شهر را کپسوله می‌کند، بدون تغییر آن
منطق) جداگانه می‌سازد -- سپس دو سکشن را زیر یک هدر مشترک کنار هم
می‌گذارد. برای سکشن کمینه، از _load_market_min و
get_gamma_event_prices_min (به‌جای نسخهٔ حداکثر) استفاده می‌شود تا قیمت و
برچسب باکت از منبع درست خوانده شوند. منطق محاسبهٔ pct، TAKE_PROFIT_PCT،
STOP_LOSS_PCT، last_price/last_checked_at، و بستن قفل‌های resolve‌شده
(history_manager) هیچ‌کدام تغییر نکرده‌اند.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from clob_utils import get_gamma_event_prices, get_gamma_event_prices_min
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
STATUS_FILE = Path("data/last_price_status.txt")

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

def _load_market_min(city, date):
    """(فاز ۶) معادل _load_market ولی برای فایل بازار کمینه."""
    p = MARKETS_DIR / f"{city}_{date}_min.json"
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

def _build_polymarket_url_min(city, date_str):
    """(فاز ۶) معادل _build_polymarket_url ولی lowest- به‌جای highest-."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        month = MONTHS[dt.month - 1]
        return f"https://polymarket.com/event/lowest-temperature-in-{city}-on-{month}-{dt.day}-{dt.year}"
    except Exception:
        return "https://polymarket.com"

def send_telegram(text):
    """نگه‌داشته شده برای استفادهٔ مستقل/تستی -- check_all() دیگر خودش این
    تابع را صدا نمی‌زند (طبق PATCH فاز ۳-ب)."""
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


def _build_city_blocks(locks_subset, now, is_min):
    """(فاز ۶) منطق ساخت بلوک شهر -- کپسوله‌شده از build_message قدیمی تا
    برای هر دو گروه (max/min) بدون کپی کدِ حلقه، دوباره‌استفاده شود.
    is_min تعیین می‌کند از کدام فایل مارکت/تابع قیمت/سازندهٔ URL استفاده
    شود."""
    by_city = {}
    for l in locks_subset:
        by_city.setdefault((l["city"], l["date"]), []).append(l)

    blocks = []
    for (city, date), group in sorted(by_city.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        market = _load_market_min(city, date) if is_min else _load_market(city, date)
        loc = LOCATIONS.get(city, {})

        timing = local_day_status(date, loc, now)
        awaiting_settlement = timing["kind"] == "awaiting_settlement"
        hours_left = timing["remaining_seconds"] / 3600.0

        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            fetch_fn = get_gamma_event_prices_min if is_min else get_gamma_event_prices
            gamma_prices = fetch_fn(city, MONTHS[dt.month - 1], dt.day, dt.year)
        except Exception:
            gamma_prices = {}

        near_resolve = is_near_local_day_end(date, loc, now, hours=NEAR_RESOLVE_HOURS)
        star = False
        lines = []

        for l in group:
            current = gamma_prices.get(str(l["market_id"]))
            if current is None:
                lines.append(" \u26AA قیمت فعلی در دسترس نیست")
                continue

            # فقط وقتی قیمت واقعاً معتبر گرفته شد به‌روزرسانی می‌شود -- آخرین
            # قیمت معتبر قبلی روی یک قطعی لحظه‌ای شبکه پاک نمی‌شود.
            l["last_price"] = current
            l["last_checked_at"] = now.isoformat()

            entry = l["entry_price"]
            diff_cents = round((current - entry) * 100, 1)
            pct = round((current - entry) / entry * 100, 1) if entry else 0.0
            l["last_pct"] = pct

            triggered = pct >= TAKE_PROFIT_PCT or pct <= -STOP_LOSS_PCT
            if triggered:
                star = True

            arrow = "\u2191" if diff_cents >= 0 else "\u2193"
            color = "\U0001F7E2" if diff_cents >= 0 else "\U0001F534"

            rng = _find_range(market, l["market_id"])
            unit_sym = market.get("unit", "") if market else ""
            label = _label_for_range(rng, unit_sym)

            lines.append(
                f" {color} {label}: {int(round(entry * 100))}\u00a2 \u2192 "
                f"{int(round(current * 100))}\u00a2 ({pct:+.0f}% {arrow})"
            )

            # (درخواست کاربر) دمای پیش‌بینی‌شدهٔ مدل، به‌عنوان یک سطر مجزا --
            # ساختار سطر بالا دست‌نخورده می‌ماند؛ فقط در پیام تلگرام
            # (dashboard_simple.py هیچ تغییری نکرده).
            forecast_mean = market.get("forecast_mean") if market else None
            if forecast_mean is not None:
                lines.append(f"    \u2022 دمای پیش‌بینی مدل: {forecast_mean:.1f}{unit_sym}")

        marks = ("\u2B50" if star else "") + (" \u23F0" if near_resolve else "")
        name = LOCATIONS.get(city, {}).get("name", city)
        link = _build_polymarket_url_min(city, date) if is_min else _build_polymarket_url(city, date)
        title = f'<a href="{link}">{name}</a> \u2014 {date}'
        if marks:
            title = f"{marks} {title}"
        title += f" ({_local_day_label(hours_left, awaiting_settlement)})"

        blocks.append(title + "\n" + "\n".join(lines))

    return blocks


def build_message(now, locks):
    """(فاز ۶) دیگر فقط زمانی None برمی‌گرداند که اصلاً هیچ قفل بازی وجود
    نداشته باشد. قفل‌های باز بر اساس market_type به دو گروه (max/min)
    تقسیم می‌شوند و هرکدام در سکشن جداگانهٔ خودش (🔥 حداکثر / ❄️ حداقل)
    نمایش داده می‌شوند. علامت‌های ⭐/⏰ همچنان فقط روی آیتم‌هایی که واقعاً
    واجد شرایطند نمایش داده می‌شوند."""
    open_locks = [l for l in locks if l.get("status") == "open"]
    if not open_locks:
        return None

    open_locks_max = [l for l in open_locks if l.get("market_type", "max") == "max"]
    open_locks_min = [l for l in open_locks if l.get("market_type") == "min"]

    city_blocks_max = _build_city_blocks(open_locks_max, now, is_min=False)
    city_blocks_min = _build_city_blocks(open_locks_min, now, is_min=True)

    header_line = f"\U0001F4CA وضعیت قفل‌ها \u2014 {now.strftime('%Y-%m-%d %H:%M')} UTC"

    sections = []
    if city_blocks_max:
        sections.append("\U0001F525 حداکثر دما\n\n" + "\n\n".join(city_blocks_max))
    if city_blocks_min:
        sections.append("\u2744\uFE0F حداقل دما\n\n" + "\n\n".join(city_blocks_min))

    return header_line + "\n\n" + "\n\n".join(sections)


def _closed_locks_footer(locks):
    """(درخواست کاربر) گزارش یک‌بارهٔ قفل‌هایی که از آخرین پیام تا الان
    بسته شده‌اند -- چه با حذف دستی (closed_manual)، چه با resolve خودکار
    (closed_resolved). با پرچم داخلی notified_close از تکرار در پیام‌های
    بعدی جلوگیری می‌شود. این فقط یک بخش زیرین و جداگانه است؛ ساختار
    فعلی سکشن‌های \U0001F525/\u2744\uFE0F بالای پیام دست‌نخورده می‌ماند."""
    lines = []
    for l in locks:
        if l.get("status") not in ("closed_manual", "closed_resolved"):
            continue
        if l.get("notified_close"):
            continue

        entry = l.get("entry_price")
        exit_price = l.get("exit_price")
        pct = (exit_price - entry) / entry * 100 if entry and exit_price is not None else None
        pct_str = f"{pct:+.0f}%" if pct is not None else "-"

        name = LOCATIONS.get(l.get("city"), {}).get("name", l.get("city"))
        reason = "resolve خودکار" if l.get("status") == "closed_resolved" else "حذف دستی"
        lines.append(f"  \u2705 {name} \u2014 {l.get('date')} ({reason}, {pct_str})")

        l["notified_close"] = True

    return lines


def check_all():
    """دیگر مستقیماً به تلگرام پیام نمی‌فرستد. متن وضعیت (در صورت وجود
    حداقل یک قفل باز) در data/last_price_status.txt نوشته می‌شود تا لایهٔ
    Workflow آن را با لینک یکتای تازهٔ داشبورد ترکیب و یک‌جا ارسال کند.
    اگر هیچ قفل بازی نباشد، این فایل (در صورت وجود از قبل) پاک می‌شود."""
    now = datetime.now(timezone.utc)
    locks = _load_locks()

    n_resolved = history_manager.check_and_close_resolved_locks(locks, now)
    if n_resolved:
        print(f"[price_monitor] {n_resolved} قفل به‌طور خودکار با resolve شدن بازار بسته شد")

    message = build_message(now, locks)

    # (درخواست کاربر) اعلان قفل‌های تازه‌بسته‌شده -- یک بخش زیرین و جدا،
    # مستقل از این‌که پیام وضعیت اصلی بالا None باشد یا نه، تا حتی وقتی
    # هیچ قفل بازی نمانده باشد ولی چیزی همین الان بسته شده، پیام حذف نشود.
    closed_lines = _closed_locks_footer(locks)
    if closed_lines:
        footer = "\U0001F514 قفل‌های بسته‌شده از آخرین بررسی:\n" + "\n".join(closed_lines)
        if message:
            message = message + "\n\n" + footer
        else:
            header_line = f"\U0001F4CA وضعیت قفل‌ها \u2014 {now.strftime('%Y-%m-%d %H:%M')} UTC"
            message = header_line + "\n\n" + footer

    _save_locks(locks)
    history_manager.write_history_csv(locks)

    if message:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(message, encoding="utf-8")
        print("[price_monitor] وضعیت قیمت‌ها در data/last_price_status.txt نوشته شد.")
    else:
        if STATUS_FILE.exists():
            STATUS_FILE.unlink()
        print("[price_monitor] هیچ قفل بازی وجود ندارد -- چیزی برای گزارش نیست.")

    return message

if __name__ == "__main__":
    check_all()
