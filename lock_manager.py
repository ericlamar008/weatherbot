"""
lock_manager.py -- خواندن درخواست‌های قفل/حذف قفل از GitHub Issues و ثبت‌شان.
=====================================================================================
نسخهٔ نهایی (شامل رفع باگ قفل تکراری از فاز ۶).
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from clob_utils import get_clob_book_bid
import history_manager

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "USERNAME/weatherbot")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
API_BASE = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
LOCKS_FILE = Path("data/locked_signals.json")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


def _load_locks():
    if LOCKS_FILE.exists():
        try:
            return json.loads(LOCKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_locks(locks):
    LOCKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCKS_FILE.write_text(json.dumps(locks, indent=2, ensure_ascii=False), encoding="utf-8")


def _fetch_open_issues(label):
    r = requests.get(
        f"{API_BASE}/issues",
        headers=HEADERS,
        params={"state": "open", "labels": label, "per_page": 100},
        timeout=15,
    )
    r.raise_for_status()
    return [i for i in r.json() if "pull_request" not in i]


def _close_issue(number, comment=None, extra_label="processed"):
    try:
        if comment:
            requests.post(
                f"{API_BASE}/issues/{number}/comments",
                headers=HEADERS,
                json={"body": comment},
                timeout=15,
            )
        requests.patch(
            f"{API_BASE}/issues/{number}",
            headers=HEADERS,
            json={"state": "closed"},
            timeout=15,
        )
        requests.post(
            f"{API_BASE}/issues/{number}/labels",
            headers=HEADERS,
            json={"labels": [extra_label]},
            timeout=15,
        )
    except Exception as e:
        print(f"[lock_manager] هشدار: نشد issue شماره {number} را ببندم: {e}")


def _has_open_lock(locks, city, date, market_id):
    return any(
        l.get("city") == city and l.get("date") == date
        and l.get("market_id") == market_id and l.get("status") == "open"
        for l in locks
    )


def process_lock_issues():
    locks = _load_locks()
    existing_numbers = {l.get("issue_number") for l in locks}
    new_count = 0

    for issue in _fetch_open_issues("lock-request"):
        number = issue["number"]
        if number in existing_numbers:
            continue
        try:
            data = json.loads(issue["body"])
            market_id_raw = str(data["market_id"])
            market_id, _, token_id = market_id_raw.partition("|")
            city, date = data["city"], data["date"]
        except Exception as e:
            _close_issue(
                number,
                f"\u26a0\ufe0f فرمت این درخواست قفل قابل خواندن نبود ({e}). لطفاً فقط از دکمهٔ «قفل کن» در simple.html استفاده کنید.",
            )
            continue

        if _has_open_lock(locks, city, date, market_id):
            _close_issue(number, "\u2139\ufe0f این باکت از قبل قفل باز دارد -- درخواست تکراری نادیده گرفته شد.")
            continue

        entry = {
            "issue_number": number,
            "city": city,
            "date": date,
            "market_id": market_id,
            "token_id": token_id or None,
            "side": data.get("side", "YES"),
            "entry_price": float(data["price"]),
            "locked_at": data.get("locked_at", datetime.now(timezone.utc).isoformat()),
            "status": "open",
            "last_price": None,
            "last_pct": None,
            "exit_price": None,
            "closed_at": None,
            "close_reason": None,
            "actual_temp": None,
        }
        locks.append(entry)
        _close_issue(number, f"\u2705 قفل ثبت شد: {entry['city']} {entry['date']} ({entry['side']}) @ {entry['entry_price']}")
        new_count += 1

    _save_locks(locks)
    return new_count


def process_unlock_issues():
    locks = _load_locks()
    already_unlocked_issue_numbers = {l.get("unlock_issue_number") for l in locks if l.get("unlock_issue_number")}
    closed_count = 0

    for issue in _fetch_open_issues("unlock-request"):
        number = issue["number"]
        if number in already_unlocked_issue_numbers:
            continue
        try:
            data = json.loads(issue["body"])
            city = data["city"]
            date = data["date"]
            market_id = str(data["market_id"])
        except Exception as e:
            _close_issue(number, f"\u26a0\ufe0f فرمت این درخواست حذف قفل قابل خواندن نبود ({e}).")
            continue

        target = None
        for l in locks:
            if l.get("city") == city and l.get("date") == date and l.get("market_id") == market_id and l.get("status") == "open":
                target = l
                break

        if target is None:
            _close_issue(number, "\u26a0\ufe0f هیچ قفل بازی با این مشخصات پیدا نشد (شاید قبلاً حذف یا resolve شده).")
            continue

        current_price = get_clob_book_bid(target.get("token_id"))
        if current_price is None:
            _close_issue(
                number,
                "\u26a0\ufe0f نشد قیمت زندهٔ این باکت را بگیرم (ممکن است شبکه یا پلی‌مارکت موقتاً در دسترس نباشد). "
                "دوباره یک درخواست حذف قفل جدید بسازید تا مجدد تلاش شود.",
            )
            continue

        target["status"] = "closed_manual"
        target["exit_price"] = current_price
        target["closed_at"] = datetime.now(timezone.utc).isoformat()
        target["close_reason"] = "manual_unlock"
        target["unlock_issue_number"] = number

        entry_price = target["entry_price"]
        pct = (current_price - entry_price) / entry_price * 100 if entry_price else 0.0
        _close_issue(
            number,
            f"\u2705 قفل حذف شد: {city} {date} -- ورود {entry_price:.3f} -> خروج {current_price:.3f} ({pct:+.1f}%)",
        )
        closed_count += 1

    _save_locks(locks)
    return closed_count


if __name__ == "__main__":
    n_locked = process_lock_issues()
    n_unlocked = process_unlock_issues()
    locks = _load_locks()
    n_history = history_manager.write_history_csv(locks)
    print(f"[lock_manager] {n_locked} قفل جدید ثبت شد, {n_unlocked} قفل حذف شد, {n_history} ردیف در تاریخچه")
