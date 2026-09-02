"""
clob_utils.py -- تابع مشترک برای گرفتن قیمت زنده از پلی‌مارکت
=====================================================================================
تنها کاری که باید در weatherbot_v3.py انجام شود (یک بار، دستی):
  ۱. تابع خودش به‌نام get_clob_book_bid را از داخل آن فایل پاک کنید.
  ۲. این خط را بالای weatherbot_v3.py، کنار بقیهٔ import ها اضافه کنید:
     from clob_utils import get_clob_book_bid
"""
import requests

TIMEOUT = (5, 8)


def get_clob_book_bid(token_id):
    """
    بالاترین قیمت خرید فعلی (bid) برای یک باکت خاص را از پلی‌مارکت برمی‌گرداند.
    اگر چیزی پیدا نشود یا خطا رخ دهد، None برمی‌گرداند و هیچ‌وقت برنامه را
    متوقف نمی‌کند.
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
