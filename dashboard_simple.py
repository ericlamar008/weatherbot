"""
dashboard_simple.py -- می‌سازد simple.html: نسخهٔ ساده و تعاملی داشبورد.
=====================================================================================
تغییرات این نسخه (فقط نمایشی، منطق ربات دست‌نخورده):
  ۱) بازارها دیگر پیش‌فرض باز نیستند -- فقط با کلیک روی اسم شهر باز می‌شوند.
  ۲) اگر یک شهر چند تاریخ داشته باشد، یک لایهٔ اضافی (تاریخ) بین شهر و
     جدول باکت‌ها اضافه شده.
  ۳) شهرهایی که سیگنال قابل‌معامله دارند (main_signal) بالای لیست می‌آیند؛
     بقیه به‌ترتیب الفبا.
  ۴) باکس جستجوی شهر + دو فیلتر (شهر/تاریخ) مخصوص بخش تاریخچه.
  ۵) اسم شهر در هر بازار حالا لینک مستقیم به پلی‌مارکت است.
  ۶) دکمهٔ پرش سریع به بخش تاریخچه، بالای صفحه.
  ۷) زمان باقی‌مانده به انگلیسی نوشته می‌شود ("2h 15m remaining").
  ۸) ستون "باور نهایی" در جدول باکت‌ها.
  ۹) سکشن «سیگنال‌های قفل‌شدهٔ فعال» بالای صفحه.
  ۱۰) رفع باگ ثبت‌نشدن قفل (وضعیت موقت + payload کوتاه‌تر).
  ۱۱) بازطراحی بصری کامل (پالت روشن، فونت Vazirmatn).
  ۱۲) (اصلاح جدید -- دور رفع باگ) چند اصلاح مهم:
      الف) «آخرین به‌روزرسانی» بالای صفحه دیگر یک رشتهٔ ثابت («X ساعت
          پیش») که در لحظهٔ ساخت صفحه محاسبه می‌شد نیست -- چون آن رشته
          تا وقتی کاربر صفحه را ببیند (که می‌تواند دقیقه‌ها/ساعت‌ها بعد
          از build باشد، به‌خاطر کش CDN/مرورگر) هیچ‌وقت خودش را به‌روز
          نمی‌کرد. حالا زمان مطلق (ISO) در data-attribute جاسازی می‌شود و
          خود مرورگر با جاوااسکریپت، در لحظهٔ نمایش، «X ساعت Y دقیقه پیش»
          را واقعی حساب می‌کند.
      ب) ساعت «آخرین به‌روزرسانی» به وقت ایران (Asia/Tehran) نمایش داده
          می‌شود، نه UTC -- تاریخ همچنان میلادی می‌ماند.
      ج) در سکشن «سیگنال‌های قفل‌شدهٔ فعال»: اسم شهر حالا لینک مستقیم به
          صفحهٔ بازار در پلی‌مارکت است (دقیقاً مثل لیست شهرها).
      د) در همان سکشن، ستون جدید «آخرین به‌روزرسانی» اضافه شد -- از فیلد
          last_checked_at که price_monitor.py حالا روی هر قفل ثبت
          می‌کند؛ این هم با همان مکانیزم جاوااسکریپت لحظه‌ای محاسبه می‌شود.
      ه) قفل‌هایی که بازار زیرینشان دیگر resolve/expire شده (بر اساس
          وضعیت واقعی در data/markets/*.json، نه فقط فیلد status خود
          قفل که ممکن است هنوز به‌روز نشده باشد) از این سکشن حذف می‌شوند
          -- رفع باگ نمایش قفل‌های منقضی (مثل نمونهٔ Seoul).
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import history_manager

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

try:
    from locations import LOCATIONS, MONTHS
except ImportError:
    LOCATIONS, MONTHS = {}, [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]

GITHUB_REPO = "ericlamar008/weatherbot"
MARKETS_DIR = Path("data/markets")
LOCKS_FILE = Path("data/locked_signals.json")
OUTPUT_FILE = Path("simple.html")
IRAN_TZ_NAME = "Asia/Tehran"

RESOLVED_LIKE_STATUSES = {"resolved", "resolved_no_signal", "expired_no_signal"}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>WeatherBet -- ساده</title>
<style>
* { box-sizing: border-box; }
:root{
  --bg: #eef1f6;
  --surface: #ffffff;
  --surface-2: #f6f8fb;
  --border: #dde3ec;
  --text: #1f2430;
  --text-dim: #6b7684;
  --accent: #2563eb;
  --accent-dim: #eaf1ff;
  --green: #16a34a;
  --green-bg: #e8f8ee;
  --red: #dc2626;
  --red-bg: #fdeceb;
  --amber: #b45309;
  --amber-bg: #fef3e0;
}
body{
  background:var(--bg);color:var(--text);
  font-family:"Vazirmatn","IRANSans","Segoe UI",Tahoma,Arial,sans-serif;
  padding:14px;margin:0;font-size:15px;line-height:1.55;
  -webkit-font-smoothing:antialiased;
}
h1{font-size:19px;margin:6px 0 10px;font-weight:700;color:var(--text)}
h2{font-size:15.5px;margin-top:30px;border-bottom:1px solid var(--border);padding-bottom:8px;scroll-margin-top:16px;font-weight:700}
.meta{color:var(--text-dim);font-size:12.5px;margin-bottom:14px;line-height:1.7}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.toolbar input[type=text]{flex:1;min-width:140px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:10px;padding:11px 14px;font-size:13.5px;box-shadow:0 1px 2px rgba(20,30,60,0.04)}
.toolbar input[type=text]:focus{outline:2px solid var(--accent);outline-offset:1px}
.jump-btn{display:inline-block;background:var(--accent);color:#fff;border-radius:10px;padding:11px 16px;font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap;box-shadow:0 2px 6px rgba(37,99,235,0.25)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
details.city-block{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:8px 12px;margin-bottom:10px;box-shadow:0 1px 3px rgba(20,30,60,0.05)}
details.date-block{background:var(--surface-2);border:1px solid var(--border);border-radius:10px;padding:8px 12px;margin:8px 0}
summary{cursor:pointer;font-size:14px;color:var(--text);list-style:none;padding:8px 4px;font-weight:500}
summary::-webkit-details-marker{display:none}
summary::before{content:"\u25B8";color:var(--text-dim);font-size:11px;margin-left:8px}
details[open]>summary::before{content:"\u25BE"}
.main-badge{background:var(--green-bg);color:var(--green);border:1px solid #16a34a55;border-radius:6px;padding:2px 8px;font-size:10.5px;font-weight:600;white-space:nowrap;margin-right:6px}
.time-note{color:var(--text-dim);font-size:11.5px;direction:ltr;unicode-bidi:embed;display:inline-block}
.locked-section{border:1px solid #16a34a55;background:var(--green-bg)}
.table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:10px;border-radius:8px}
table{width:100%;min-width:480px;border-collapse:collapse;font-size:13px;background:var(--surface);font-variant-numeric:tabular-nums}
th,td{padding:10px 8px;text-align:center;border-bottom:1px solid var(--border);white-space:nowrap}
th{color:var(--text-dim);font-weight:600;font-size:11.5px;background:var(--surface-2)}
tr:last-child td{border-bottom:none}
.lock-btn,.unlock-btn{border:none;border-radius:9px;padding:10px 16px;font-size:12.5px;font-weight:600;cursor:pointer;min-height:40px}
.lock-btn{background:var(--accent);color:#fff}
.lock-btn:hover{background:#1d4ed8}
.unlock-btn{background:var(--red);color:#fff}
.unlock-btn:hover{background:#b91c1c}
.locked-badge{color:var(--green);font-size:11.5px;font-weight:600;display:block;margin-bottom:4px}
.lock-status{display:block;font-size:10.5px;margin-top:5px;line-height:1.4;max-width:170px}
.empty{color:var(--text-dim);font-style:italic;padding:14px 2px}
.win{color:var(--green);font-weight:600}
.loss{color:var(--red);font-weight:600}
.history-filters{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.history-filters select{background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:10px;padding:9px 12px;font-size:12.5px}
.download-btn{display:inline-block;margin:10px 0;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:11px 18px;font-size:12.5px;font-weight:600;text-decoration:none;box-shadow:0 1px 2px rgba(20,30,60,0.04)}
.download-btn:hover{border-color:var(--accent);text-decoration:none}
@media (max-width: 480px){
  body{padding:10px;font-size:14.5px}
  h1{font-size:18px}
  th,td{padding:9px 6px;font-size:12px}
  .lock-btn,.unlock-btn{padding:11px 14px;min-height:44px}
  .toolbar input[type=text]{padding:12px 14px}
}
</style>
</head>
<body>
<h1>WeatherBet -- داشبورد ساده</h1>
<div class="meta">آخرین به‌روزرسانی: LASTUPDATE (به وقت ایران) <span class="time-note relative-time" data-ts="LASTSCANISO" data-kind="LASTSCANKIND">LASTSCANFALLBACK</span><br>فقط دما / احتمال مدل / احتمال بازار -- بدون سایزینگ</div>
<div class="toolbar">
  <input type="text" id="citySearch" placeholder="جستجوی شهر..." oninput="filterCities()">
  <a class="jump-btn" href="#history-section">مشاهدهٔ نتایج \u2193</a>
</div>
BODYHTML

<h2 id="history-section">تاریخچهٔ معاملات</h2>
<a class="download-btn" href="lock_history.csv" download>\u2b07 دانلود CSV کامل</a>
<div class="history-filters">
  <select id="historyCityFilter" onchange="filterHistory()"><option value="">همهٔ شهرها</option>HISTORYCITYOPTIONS</select>
  <select id="historyDateFilter" onchange="filterHistory()"><option value="">همهٔ تاریخ‌ها</option>HISTORYDATEOPTIONS</select>
</div>
<div class="table-scroll">
HISTORYHTML
</div>

<script>
function filterCities() {
  const q = document.getElementById('citySearch').value.trim().toLowerCase();
  document.querySelectorAll('.city-block').forEach(function(block) {
    const name = (block.getAttribute('data-city-name') || '').toLowerCase();
    block.style.display = (!q || name.indexOf(q) !== -1) ? '' : 'none';
  });
}
function filterHistory() {
  const city = document.getElementById('historyCityFilter').value;
  const date = document.getElementById('historyDateFilter').value;
  document.querySelectorAll('#historyTable tbody tr').forEach(function(row) {
    const okCity = !city || row.getAttribute('data-city') === city;
    const okDate = !date || row.getAttribute('data-date') === date;
    row.style.display = (okCity && okDate) ? '' : 'none';
  });
}
function setLockStatus(marketId, text, color) {
  const el = document.getElementById("lockstatus-" + marketId);
  if (el) { el.textContent = text; el.style.color = color || "#9aa0a6"; }
}
function lockBucket(city, cityName, date, marketId, tokenId, side, price, label) {
  setLockStatus(marketId, "\u23F3 در حال باز شدن گیت‌هاب...", "#eab308");
  const repo = "GITHUB_REPO_PLACEHOLDER";
  const payload = { city: city, date: date, market_id: marketId + "|" + tokenId, side: side, price: price };
  const title = encodeURIComponent("LOCK " + cityName + " " + date + " " + label + " " + side + " @ " + price);
  const body = encodeURIComponent(JSON.stringify(payload));
  const url = "https://github.com/" + repo + "/issues/new?title=" + title + "&body=" + body + "&labels=lock-request";
  const win = window.open(url, "_blank");
  if (win) {
    setLockStatus(marketId, "\u26A0\uFE0F در تب جدید حتماً روی «Submit new issue» کلیک کنید تا قفل ثبت شود!", "#f59e0b");
  } else {
    setLockStatus(marketId, "\u274C مرورگر پاپ‌آپ را مسدود کرد -- اجازه بدهید و دوباره امتحان کنید.", "#f87171");
  }
}
function unlockBucket(city, cityName, date, marketId, label) {
  setLockStatus(marketId, "\u23F3 در حال باز شدن گیت‌هاب...", "#eab308");
  const repo = "GITHUB_REPO_PLACEHOLDER";
  const payload = { city: city, date: date, market_id: marketId };
  const title = encodeURIComponent("UNLOCK " + cityName + " " + date + " " + label);
  const body = encodeURIComponent(JSON.stringify(payload));
  const url = "https://github.com/" + repo + "/issues/new?title=" + title + "&body=" + body + "&labels=unlock-request";
  const win = window.open(url, "_blank");
  if (win) {
    setLockStatus(marketId, "\u26A0\uFE0F در تب جدید حتماً روی «Submit new issue» کلیک کنید تا حذف شود!", "#f59e0b");
  } else {
    setLockStatus(marketId, "\u274C مرورگر پاپ‌آپ را مسدود کرد -- اجازه بدهید و دوباره امتحان کنید.", "#f87171");
  }
}
function computeRelativeTimes() {
  const now = new Date();
  document.querySelectorAll('.relative-time').forEach(function(el) {
    const ts = el.getAttribute('data-ts');
    if (!ts) { return; }
    const then = new Date(ts);
    if (isNaN(then.getTime())) { return; }
    let diffMin = Math.round((now - then) / 60000);
    if (diffMin < 0) { diffMin = 0; }
    const h = Math.floor(diffMin / 60);
    const m = diffMin % 60;
    const kind = el.getAttribute('data-kind') || '';
    el.textContent = h + 'h ' + m + 'm ago' + (kind ? (' -- ' + kind) : '');
  });
}
window.addEventListener('DOMContentLoaded', computeRelativeTimes);
</script>
</body>
</html>
"""


def _load_all_markets():
    out = []
    if not MARKETS_DIR.exists():
        return out
    for f in MARKETS_DIR.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def _load_locks():
    if not LOCKS_FILE.exists():
        return []
    try:
        return json.loads(LOCKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _load_locked_keys(locks):
    return {
        (l.get("city"), l.get("date"), str(l.get("market_id")))
        for l in locks
        if l.get("status") == "open"
    }


def _main_signal_market_id(market):
    allocation = market.get("committed_allocation") or market.get("live_allocation") or []
    for a in allocation:
        if a.get("role") == "main_signal":
            return str(a.get("market_id"))
    return None


def _label_for_range(low, high, unit_sym):
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
    """به انگلیسی -- تا با متن فارسی قاطی نشود (طبق بازخورد کاربر)."""
    if hours is None:
        return ""
    total_minutes = int(round(hours * 60))
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m}m remaining"


LAST_SCAN_FILE = Path("data/last_scan.json")


def _latest_scan_info():
    """برمی‌گرداند (iso_timestamp, kind_label) آخرین اسکن (کامل یا سبک)،
    یا (None, None) اگر هیچ سابقه‌ای نبود. محاسبهٔ «X ساعت پیش» دیگر اینجا
    (سمت پایتون/build-time) انجام نمی‌شود -- به جاوااسکریپت سمت مرورگر
    سپرده شده تا همیشه، حتی با تأخیر کش، درست باشد."""
    if not LAST_SCAN_FILE.exists():
        return None, None
    try:
        data = json.loads(LAST_SCAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    timestamps = []
    for kind in ("full", "lite"):
        ts = data.get(kind)
        if not ts:
            continue
        try:
            timestamps.append((kind, datetime.fromisoformat(ts)))
        except Exception:
            continue
    if not timestamps:
        return None, None
    kind, latest = max(timestamps, key=lambda kv: kv[1])
    kind_label = "اسکن کامل" if kind == "full" else "اسکن سبک"
    return latest.isoformat(), kind_label


def _iran_time_str(dt_utc):
    """تبدیل یک datetime آگاه از UTC به رشتهٔ زمان محلی ایران، با همان
    فرمت میلادی قبلی (YYYY-MM-DD HH:MM). اگر zoneinfo/tzdata در دسترس
    نبود، بی‌صدا به UTC برمی‌گردد -- هرگز نباید ساخت داشبورد را متوقف کند."""
    if ZoneInfo is not None:
        try:
            local_dt = dt_utc.astimezone(ZoneInfo(IRAN_TZ_NAME))
            return local_dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return dt_utc.strftime("%Y-%m-%d %H:%M") + " UTC"


def _build_polymarket_url(city, date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        month = MONTHS[dt.month - 1]
        return f"https://polymarket.com/event/highest-temperature-in-{city}-on-{month}-{dt.day}-{dt.year}"
    except Exception:
        return "https://polymarket.com"


def _bucket_table(city_slug, city_name, date, unit_sym, full_distribution, locked_keys, main_signal_id):
    if not full_distribution:
        return '<div class="empty">داده‌ای موجود نیست.</div>'
    rows = [
        "<table><tr><th>باکت</th><th>احتمال مدل</th><th>احتمال بازار (YES)</th>"
        "<th>قیمت YES</th><th>باور نهایی</th><th></th></tr>"
    ]
    for b in full_distribution:
        low, high = b.get("range", [None, None])
        label = _label_for_range(low, high, unit_sym)
        model_prob = b.get("model_prob")
        yes_price = b.get("yes_price")
        model_str = f"{model_prob * 100:.1f}%" if model_prob is not None else "-"
        market_str = f"{yes_price * 100:.1f}%" if yes_price is not None else "-"
        price_str = f"{yes_price:.3f}" if yes_price is not None else "-"
        belief_val = b.get("belief_prob")
        belief_str = f"{belief_val * 100:.1f}%" if belief_val is not None else "-"
        market_id = str(b.get("market_id", ""))
        yes_token = b.get("yes_token_id", "")

        is_main = main_signal_id is not None and market_id == main_signal_id
        label_html = f"{label} " + ('<span class="main-badge">سیگنال اصلی</span>' if is_main else "")

        is_locked = (city_slug, date, market_id) in locked_keys
        action_html = ""
        status_span = f'<span class="lock-status" id="lockstatus-{market_id}"></span>' if market_id else ""
        if yes_price is not None and market_id:
            if is_locked:
                action_html = (
                    f'<span class="locked-badge">قفل \u2714</span>'
                    f'<button class="unlock-btn" onclick="unlockBucket(\'{city_slug}\',\'{city_name}\','
                    f"'{date}','{market_id}','{label}')\">حذف قفل</button>"
                    f'{status_span}'
                )
            else:
                action_html = (
                    f'<button class="lock-btn" onclick="lockBucket(\'{city_slug}\',\'{city_name}\','
                    f"'{date}','{market_id}','{yes_token}','YES',{yes_price},'{label}')\">قفل کن</button>"
                    f'{status_span}'
                )

        rows.append(
            f"<tr><td>{label_html}</td><td>{model_str}</td><td>{market_str}</td>"
            f"<td>{price_str}</td><td>{belief_str}</td><td>{action_html}</td></tr>"
        )
    rows.append("</table>")
    return "".join(rows)


def _load_market_by_key(city, date):
    p = MARKETS_DIR / f"{city}_{date}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _locked_signals_section_html(locks):
    """سکشن مجزا و قابل‌اسکرول بالای صفحه که همهٔ سیگنال‌های قفل‌شدهٔ فعال
    را با قیمت قفل، قیمت فعلی، درصد تغییر، ساعت باقی‌مانده، و آخرین
    به‌روزرسانی نشان می‌دهد. قفل‌هایی که بازار زیرینشان طبق داده‌های
    واقعی resolve/expire شده، حتی اگر status خود قفل هنوز به‌روز نشده
    باشد، از این لیست حذف می‌شوند."""
    open_locks = [l for l in locks if l.get("status") == "open"]
    if not open_locks:
        return ""

    now = datetime.now(timezone.utc)
    visible_rows = []

    for l in sorted(open_locks, key=lambda x: (x.get("city", ""), x.get("date", ""))):
        city = l.get("city", "")
        date = l.get("date", "")

        mkt = _load_market_by_key(city, date)

        # رفع باگ: قفلی که بازار زیرینش واقعاً resolve/expire شده را نادیده بگیر،
        # حتی اگر status خود قفل هنوز "open" باشد (ممکن است price_monitor.py
        # هنوز فرصت نکرده باشد آن را ببندد).
        if mkt is not None and mkt.get("status") in RESOLVED_LIKE_STATUSES:
            continue

        hours_left = None
        if mkt and mkt.get("event_end_date"):
            try:
                end = datetime.fromisoformat(mkt["event_end_date"])
                hours_left = max(0.0, (end - now).total_seconds() / 3600)
            except Exception:
                hours_left = None
        if hours_left is not None and hours_left <= 0:
            continue

        name = LOCATIONS.get(city, {}).get("name", city)
        link = _build_polymarket_url(city, date)
        name_html = f'<a href="{link}" target="_blank" rel="noopener">{name}</a>'

        entry = l.get("entry_price")
        entry_str = f"{entry:.3f}" if entry is not None else "-"
        last = l.get("last_price")
        last_str = f"{last:.3f}" if last is not None else "-"

        pct = l.get("last_pct")
        if pct is None:
            pct_html = "-"
        else:
            css = "win" if pct >= 0 else "loss"
            pct_html = f'<span class="{css}">{pct:+.1f}%</span>'

        time_str = _hours_left_str(hours_left) if hours_left is not None else "-"

        last_checked = l.get("last_checked_at")
        if last_checked:
            updated_html = f'<span class="relative-time" data-ts="{last_checked}"></span>'
        else:
            updated_html = "-"

        visible_rows.append(
            f"<tr><td>{name_html}</td><td>{date}</td><td>{entry_str}</td>"
            f"<td>{last_str}</td><td>{pct_html}</td><td>{time_str}</td>"
            f"<td>{updated_html}</td></tr>"
        )

    if not visible_rows:
        return ""

    rows = [
        "<table><tr><th>شهر</th><th>تاریخ</th><th>قیمت قفل‌شده</th>"
        "<th>قیمت فعلی</th><th>درصد تغییر</th><th>باقی‌مانده تا resolve</th>"
        "<th>آخرین به‌روزرسانی</th></tr>"
    ] + visible_rows
    rows.append("</table>")
    table_html = "".join(rows)

    return (
        "<details class='city-block locked-section' open>"
        f"<summary><b>\U0001F512 سیگنال‌های قفل‌شدهٔ فعال</b> ({len(visible_rows)})</summary>"
        f"<div class='table-scroll'>{table_html}</div>"
        "</details>"
    )


def _date_block_html(m, city_slug, city_name, locked_keys):
    date = m.get("date", "")
    unit_sym = m.get("unit", "")
    main_signal_id = _main_signal_market_id(m)
    link = _build_polymarket_url(city_slug, date)
    time_note = _hours_left_str(m.get("hours_left"))
    summary = (
        f'<a href="{link}" target="_blank" rel="noopener">{city_name}</a> \u2014 {date}'
        f'  <span class="time-note">{time_note}</span>'
    )
    table = _bucket_table(
        city_slug, city_name, date, unit_sym,
        m.get("full_distribution"), locked_keys, main_signal_id,
    )
    return f"<details class='date-block'><summary>{summary}</summary><div class='table-scroll'>{table}</div></details>"


def _history_table_html(locks):
    rows = history_manager.get_history_rows(locks)
    cities = sorted({r["city_name"] for r in rows})
    dates = sorted({r["date"] for r in rows}, reverse=True)
    city_options = "".join(f'<option value="{c}">{c}</option>' for c in cities)
    date_options = "".join(f'<option value="{d}">{d}</option>' for d in dates)

    if not rows:
        table_html = '<div class="empty">هنوز هیچ معامله‌ای بسته نشده.</div>'
    else:
        parts = [
            "<table id='historyTable'><tr><th>تاریخ</th><th>شهر</th><th>دمای سیگنال</th>"
            "<th>دمای نهایی</th><th>خرید (سنت)</th><th>فروش (سنت)</th><th>برآیند</th></tr><tbody>"
        ]
        for r in rows:
            pct = r["outcome_pct"]
            if pct is None:
                pct_html = "-"
            else:
                css = "win" if pct >= 0 else "loss"
                pct_html = f'<span class="{css}">{pct:+.1f}%</span>'
            sell = r["sell_cents"] if r["sell_cents"] is not None else "-"
            parts.append(
                f"<tr data-city=\"{r['city_name']}\" data-date=\"{r['date']}\">"
                f"<td>{r['date']}</td><td>{r['city_name']}</td><td>{r['signal_label']}</td>"
                f"<td>{r['final_temp']}</td><td>{r['buy_cents']}</td><td>{sell}</td><td>{pct_html}</td></tr>"
            )
        parts.append("</tbody></table>")
        table_html = "".join(parts)

    return table_html, city_options, date_options


def build_simple_dashboard():
    markets = [m for m in _load_all_markets() if m.get("status") == "open" and m.get("full_distribution")]
    locks = _load_locks()
    locked_keys = _load_locked_keys(locks)

    groups = defaultdict(list)
    for m in markets:
        groups[m.get("city", "")].append(m)
    for slug in groups:
        groups[slug].sort(key=lambda m: m.get("date", ""))

    def group_has_signal(mlist):
        return any(_main_signal_market_id(m) is not None for m in mlist)

    city_names = {slug: LOCATIONS.get(slug, {}).get("name", slug) for slug in groups}
    sorted_slugs = sorted(
        groups.keys(),
        key=lambda s: (0 if group_has_signal(groups[s]) else 1, city_names[s]),
    )

    parts = []
    if not sorted_slugs:
        parts.append('<div class="empty">هیچ بازار بازی برای نمایش وجود ندارد.</div>')

    for slug in sorted_slugs:
        city_name = city_names[slug]
        mlist = groups[slug]
        has_signal = group_has_signal(mlist)
        badge = '<span class="main-badge">سیگنال فعال</span>' if has_signal else ""

        if len(mlist) == 1:
            inner = _date_block_html(mlist[0], slug, city_name, locked_keys)
            summary = f"<b>{city_name}</b> {badge}"
        else:
            inner = "".join(_date_block_html(m, slug, city_name, locked_keys) for m in mlist)
            summary = f"<b>{city_name}</b> {badge} &nbsp;({len(mlist)} تاریخ)"

        parts.append(
            f"<details class='city-block' data-city-name='{city_name}'>"
            f"<summary>{summary}</summary>{inner}</details>"
        )

    body_html = _locked_signals_section_html(locks) + "".join(parts)
    history_html, history_city_options, history_date_options = _history_table_html(locks)

    now_utc = datetime.now(timezone.utc)
    scan_iso, scan_kind = _latest_scan_info()

    html = HTML_TEMPLATE
    html = html.replace("LASTUPDATE", _iran_time_str(now_utc))
    html = html.replace("LASTSCANISO", scan_iso or "")
    html = html.replace("LASTSCANKIND", scan_kind or "")
    html = html.replace("LASTSCANFALLBACK", "بدون سابقهٔ اسکن" if not scan_iso else "")
    html = html.replace("BODYHTML", body_html)
    html = html.replace("HISTORYHTML", history_html)
    html = html.replace("HISTORYCITYOPTIONS", history_city_options)
    html = html.replace("HISTORYDATEOPTIONS", history_date_options)
    html = html.replace("GITHUB_REPO_PLACEHOLDER", GITHUB_REPO)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    return str(OUTPUT_FILE.resolve())


if __name__ == "__main__":
    path = build_simple_dashboard()
    print(f"simple.html ساخته شد: {path}")
