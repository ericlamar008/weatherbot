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
    """همان منطق سکشن داشبورد -- میانگین درصد به‌ازای هر روز تقویمی
    (ایران) که در آن حداقل یک قفل واقعاً بسته شده."""
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
        pct = (exit_price - entry) / entry * 100
        daily.setdefault(local_date, []).append(pct)
    return daily


def build_report_text():
    daily = _daily_pnl()
    if not daily:
        return "\U0001F4C5 گزارش روزانه\n\nهنوز هیچ معامله‌ای بسته نشده -- چیزی برای گزارش نیست."

    latest_date = max(daily.keys())
    pcts = daily[latest_date]
    avg_pct = sum(pcts) / len(pcts)
    sign = "\U0001F7E2" if avg_pct >= 0 else "\U0001F534"
    return f"\U0001F4C5 گزارش روزانه \u2014 {latest_date}\n\n{sign} برآیند کل: {avg_pct:+.1f}% ({len(pcts)} معامله)"


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
