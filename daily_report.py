"""
daily_report.py -- ارسال گزارش روزانهٔ سود/زیان به تلگرام (فقط با اجرای
دستی یا زمان‌بندی مستقل خودش، بدون هیچ وابستگی به سایر بخش‌های ربات).
=====================================================================================
کاملاً جدید و مستقل -- data/locked_signals.json را فقط می‌خواند، هیچ
تغییری در آن یا هیچ فایل دیگری نمی‌دهد، و هیچ ماژول دیگری از ربات را
import نمی‌کند (به‌جز خواندن مستقیم فایل JSON). ریسک تداخل با بقیهٔ ربات
صفر است.

منطق: همان محاسبه‌ای که در سکشن «گزارش روزانه»ی dashboard_simple.py هست
(میانگین درصد برآیند هر قفل بسته‌شده، گروه‌بندی‌شده بر اساس روز واقعی
بسته‌شدن closed_at به وقت ایران -- نه تاریخ هدف بازار)، اینجا هم عیناً و
مستقل تکرار شده تا این اسکریپت به‌تنهایی و بدون وابستگی به سایر فایل‌های
پایتون ربات قابل اجرا باشد. فقط آخرین روزی که واقعاً معامله‌ای در آن
بسته شده را در پیام تلگرام گزارش می‌کند (بدون اطلاعات اضافی -- فقط
تاریخ و درصد، طبق درخواست کاربر).
"""
import json
import os
from datetime import datetime
from pathlib import Path

import requests

try:
    from zoneinfo import ZoneInfo
    IRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    IRAN_TZ = None

try:
    from locations import LOCATIONS
except ImportError:
    LOCATIONS = {}

LOCKS_FILE = Path("data/locked_signals.json")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _load_locks():
    if not LOCKS_FILE.exists():
        return []
    try:
        return json.loads(LOCKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _daily_pnl():
    """همان منطق سکشن داشبورد -- برای هر روز تقویمی (ایران) که در آن
    حداقل یک قفل واقعاً بسته شده، هم لیست تک‌تک قفل‌ها (l) و هم درصدشان
    را برمی‌گرداند -- تا هم برآیند کل، هم جزئیات هر بازار قابل‌گزارش
    باشد."""
    locks = _load_locks()
    daily = {}
    for l in locks:
        if l.get("status") not in ("closed_manual", "closed_resolved"):
            continue
        closed_at = l.get("closed_at")
        entry = l.get("entry_price")
        exit_price = l.get("exit_price")
        if not closed_at or not entry or exit_price is None:
            continue
        try:
            dt_utc = datetime.fromisoformat(closed_at)
        except Exception:
            continue
        if IRAN_TZ is not None:
            try:
                local_date = dt_utc.astimezone(IRAN_TZ).strftime("%Y-%m-%d")
            except Exception:
                local_date = dt_utc.strftime("%Y-%m-%d")
        else:
            local_date = dt_utc.strftime("%Y-%m-%d")
        daily.setdefault(local_date, []).append(l)
    return daily


def _load_market_for_lock(l):
    """(درخواست کاربر) بارگذاری فایل بازار مربوط به یک قفل بسته‌شده --
    برای استخراج برچسب دقیق باکت، دقیقاً مشابه چیزی که در پیام تلگرام
    نیم‌ساعته (price_monitor.py) استفاده می‌شود."""
    city, date = l.get("city"), l.get("date")
    suffix = "_min" if l.get("market_type") == "min" else ""
    p = Path("data/markets") / f"{city}_{date}{suffix}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_range(market, market_id):
    if not market:
        return None
    for b in market.get("full_distribution", []) or []:
        if str(b.get("market_id")) == str(market_id):
            return b.get("range")
    return None


def _label_for_range(rng, unit_sym):
    """عیناً همان تابع _label_for_range در price_monitor.py."""
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


def build_report_text():
    """(درخواست کاربر) علاوه بر برآیند کلی، اطلاعات کامل هر بازار/قفل
    همان روز هم گزارش می‌شود -- دقیقاً با همان قالب و فیلدهایی که در
    پیام تلگرام نیم‌ساعتهٔ price_monitor.py دیده می‌شود (شهر، تاریخ،
    برچسب باکت، قیمت ورود/خروج، درصد برآیند، دلیل بسته‌شدن)."""
    daily = _daily_pnl()
    if not daily:
        return "\U0001F4C5 گزارش روزانه\n\nهنوز هیچ معامله‌ای بسته نشده -- چیزی برای گزارش نیست."

    latest_date = max(daily.keys())
    locks_today = daily[latest_date]

    pcts = []
    lines = []
    for l in locks_today:
        entry = l.get("entry_price")
        exit_price = l.get("exit_price")
        pct = (exit_price - entry) / entry * 100 if entry and exit_price is not None else None
        if pct is not None:
            pcts.append(pct)

        market = _load_market_for_lock(l)
        unit_sym = market.get("unit", "") if market else ""
        rng = _find_range(market, l.get("market_id"))
        label = _label_for_range(rng, unit_sym)

        name = LOCATIONS.get(l.get("city"), {}).get("name", l.get("city"))
        reason = "resolve خودکار" if l.get("status") == "closed_resolved" else "حذف دستی"
        entry_str = f"{int(round(entry * 100))}\u00a2" if entry is not None else "-"
        exit_str = f"{int(round(exit_price * 100))}\u00a2" if exit_price is not None else "-"
        pct_str = f"{pct:+.0f}%" if pct is not None else "-"

        lines.append(
            f"  \u2705 {name} \u2014 {l.get('date')} ({label}): "
            f"{entry_str} \u2192 {exit_str} ({pct_str}) [{reason}]"
        )

    avg_pct = sum(pcts) / len(pcts) if pcts else 0.0
    sign = "\U0001F7E2" if avg_pct >= 0 else "\U0001F534"
    header = f"\U0001F4C5 گزارش روزانه \u2014 {latest_date}\n\n{sign} برآیند کل: {avg_pct:+.1f}% ({len(locks_today)} معامله)"
    return header + "\n\n" + "\n".join(lines)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[daily_report] توکن یا چت آیدی تلگرام تنظیم نشده -- پیام فقط چاپ می‌شود:\n", text)
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
        print(f"[daily_report] هشدار: ارسال تلگرام ناموفق بود: {e}")


if __name__ == "__main__":
    report_text = build_report_text()
    send_telegram(report_text)
    print(report_text)
