"""
dashboard_simple.py -- می‌سازد simple.html: نسخهٔ ساده و تعاملی داشبورد.
=====================================================================================
تغییرات پیشین (فقط نمایشی، منطق ربات دست‌نخورده):
  ۱) بازارها دیگر پیش‌فرض باز نیستند -- فقط با کلیک روی اسم شهر باز می‌شوند.
  ۲) لایهٔ تاریخ بین شهر و جدول باکت‌ها (برای شهرهای چندتاریخه).
  ۳) شهرهای دارای سیگنال قابل‌معامله بالای لیست می‌آیند.
  ۴) باکس جستجوی شهر + دو فیلتر تاریخچه.
  ۵) اسم شهر لینک مستقیم به پلی‌مارکت است.
  ۶) دکمهٔ پرش سریع به بخش تاریخچه.
  ۷) زمان باقی‌مانده به انگلیسی.
  ۸) ستون «باور نهایی».
  ۹) سکشن «سیگنال‌های قفل‌شدهٔ فعال».
  ۱۰) رفع باگ ثبت‌نشدن قفل.
  ۱۱) بازطراحی بصری کامل (پالت روشن، فونت Vazirmatn).
  ۱۲) «آخرین به‌روزرسانی» با محاسبهٔ لحظه‌ای جاوااسکریپت، به وقت ایران.
  ۱۳) رفع باگ نمایش قفل‌های منقضی.
  ۱۴) ستون «دمای سیگنال قفل‌شده»، رفع SyntaxError، رفع ناهماهنگی زمان،
      رفع کاراکترهای عجیب مارکر، اسکریپت ضدکش CDN، میانبر حذف قفل در
      سکشن فعال، متاتگ noindex، نمایش تغییر model_prob/yes_price در
      اسکن لایت.

=====================================================================================
نقشه‌راه بهبود (۱۶ سپتامبر ۲۰۲۶) -- خلاصهٔ افزودنی‌های این نسخه
=====================================================================================
فاز ۱: ستون EV اضافه شد (از فیلد از‌قبل‌محاسبه‌شدهٔ ev در هر باکت -- بدون
محاسبهٔ جدید). فیلتر BUCKET_PLAUSIBILITY_MIN=0.05: باکت‌هایی که هیچ‌کدام
از model_prob/market_prob آن‌ها به این آستانه نمی‌رسد، از جدول حذف
می‌شوند (تصمیم نهایی کاربر بعد از رد گزینهٔ فیلتر بر اساس volume که روی
دادهٔ واقعی امتحان و رد شد).

فاز ۲: رنگ‌بندی سیگنال (EV_GATE_THRESHOLD=0.10) -- آبی (is_main بدون EV
کافی)، بنفش (EV کافی بدون is_main)، سبز (هر دو). is_main از همان فیلد
از‌قبل‌محاسبه‌شدهٔ main_signal_market_id می‌آید -- بدون بازسازی منطق گیت
belief. ⚠️ بدهی فنی: مسیرهای آبی/سبز فقط با تست مصنوعی تأیید شدند (هیچ
مارکت واقعی با سیگنال فعال در این نشست دیده نشد) -- نیاز به تأیید نهایی
با یک سیگنال واقعی در فاز ۷.

فاز ۶ (بازار دمای کمینه): دو بخش لنگردار (#max-section/#min-section) در
یک صفحه. _load_all_markets اصلاح شد تا فایل‌های *_min.json را قاطی نکند.
توابع موازی کامل برای کمینه (_load_all_min_markets، _build_polymarket_
url_min، _date_block_html_min). تابع JS لاک‌کردن (lockBucket) پارامتر
market_type می‌گیرد تا payload درخواست قفل مشخص کند این باکت مربوط به
کدام نوع بازار است (lock_manager.py از این فیلد استفاده می‌کند).
=====================================================================================
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

from market_time import local_day_status

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

# (فاز ۱) آستانهٔ حداقل معناداری برای نمایش یک باکت در جدول.
BUCKET_PLAUSIBILITY_MIN = 0.05
# (فاز ۲) آستانهٔ EV برای گیت سیگنال سبز/بنفش -- موقت، تا بک‌تست واقعی
# (بعد از افزودن EV به گزارش دقت آینده) جایگزین شود.
EV_GATE_THRESHOLD = 0.10

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<script>
(function(){
  try {
    var url = new URL(window.location.href);
    if (!url.searchParams.has("_r")) {
      url.searchParams.set("_r", Date.now().toString());
      window.location.replace(url.toString());
    }
  } catch (e) {}
})();
</script>
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
  --blue: #2563eb;
  --blue-bg: #eaf1ff;
  --purple: #9333ea;
  --purple-bg: #f3e8ff;
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
summary::before{content:"";display:inline-block;width:0;height:0;border-top:4px solid transparent;border-bottom:4px solid transparent;border-left:5px solid var(--text-dim);margin-left:8px;vertical-align:middle;transition:transform .12s ease}
details[open]>summary::before{transform:rotate(90deg)}
.main-badge{background:var(--green-bg);color:var(--green);border:1px solid #16a34a55;border-radius:6px;padding:2px 8px;font-size:10.5px;font-weight:600;white-space:nowrap;margin-right:6px}
.signal-badge{border-radius:6px;padding:2px 8px;font-size:10.5px;font-weight:600;white-space:nowrap;margin-right:6px;border:1px solid}
.signal-blue{background:var(--blue-bg);color:var(--blue);border-color:#2563eb55}
.signal-purple{background:var(--purple-bg);color:var(--purple);border-color:#9333ea55}
.signal-green{background:var(--green-bg);color:var(--green);border-color:#16a34a55}
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
  <a class="jump-btn" href="#max-section">\U0001F525 حداکثر</a>
  <a class="jump-btn" href="#min-section">\u2744\uFE0F حداقل</a>
  <a class="jump-btn" href="#history-section">مشاهدهٔ نتایج \u2193</a>
</div>
<h2 id="max-section">\U0001F525 دمای حداکثر</h2>
BODYHTML
<h2 id="min-section">\u2744\uFE0F دمای حداقل</h2>
MIN_BODYHTML
DAILYPNLHTML
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
function lockBucket(city, cityName, date, marketId, tokenId, side, price, label, marketType) {
  setLockStatus(marketId, "\u23F3 در حال باز شدن گیت‌هاب...", "#eab308");
  const repo = "GITHUB_REPO_PLACEHOLDER";
  const payload = { city: city, date: date, market_id: marketId + "|" + tokenId, side: side, price: price, market_type: (marketType || "max") };
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
    """(فاز ۶) فایل‌های *_min.json عمداً رد می‌شوند تا با بازار حداکثر
    قاطی نشوند."""
    out = []
    if not MARKETS_DIR.exists():
        return out
    for f in MARKETS_DIR.glob("*.json"):
        if f.name.endswith("_min.json"):
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def _load_all_min_markets():
    """(فاز ۶) بارگذاری مستقل بازارهای کمینه."""
    out = []
    if not MARKETS_DIR.exists():
        return out
    for f in MARKETS_DIR.glob("*_min.json"):
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

def _local_day_time_label(city, date, now):
    """پایان روز محلی را از settlement رسمی جدا نگه می‌دارد."""
    loc = LOCATIONS.get(city, {})
    timing = local_day_status(date, loc, now)
    if timing["kind"] == "local_day_open":
        return _hours_left_str(timing["remaining_seconds"] / 3600.0), False
    return "awaiting official settlement", True


def _find_locked_bucket_label(mkt, market_id, unit_sym):
    """پیدا کردن برچسب دمای باکتی که قفل شده، از full_distribution بازار."""
    if not mkt:
        return "-"
    for b in mkt.get("full_distribution", []) or []:
        if str(b.get("market_id")) == str(market_id):
            rng = b.get("range")
            if not rng:
                return "-"
            low, high = rng
            return _label_for_range(low, high, unit_sym)
    return "-"


LAST_SCAN_FILE = Path("data/last_scan.json")


def _latest_scan_info():
    """برمی‌گرداند (iso_timestamp, kind_label) آخرین اسکن، یا (None, None)."""
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
    """تبدیل یک datetime آگاه از UTC به رشتهٔ زمان محلی ایران."""
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


def _build_polymarket_url_min(city, date_str):
    """(فاز ۶) معادل _build_polymarket_url ولی lowest- به‌جای highest-."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        month = MONTHS[dt.month - 1]
        return f"https://polymarket.com/event/lowest-temperature-in-{city}-on-{month}-{dt.day}-{dt.year}"
    except Exception:
        return "https://polymarket.com"


def _signal_badge_html(is_main, ev_val):
    """(فاز ۲) نشان رنگی سیگنال -- آبی/بنفش/سبز بر اساس is_main و EV."""
    ev_val = ev_val if ev_val is not None else 0.0
    if is_main and ev_val >= EV_GATE_THRESHOLD:
        return '<span class="signal-badge signal-green">\U0001F7E2 سیگنال نهایی</span>'
    if is_main:
        return '<span class="signal-badge signal-blue">\U0001F535 محتمل\u200cترین</span>'
    if ev_val >= EV_GATE_THRESHOLD:
        return '<span class="signal-badge signal-purple">\U0001F7E3 ارزش بالا</span>'
    return ''


def _bucket_table(city_slug, city_name, date, unit_sym, full_distribution, locked_keys, main_signal_id, market_type="max"):
    """(فاز ۱+۲+۶) جدول per-bucket -- فیلتر plausibility، ستون EV، رنگ‌بندی
    سیگنال، و پارامتر market_type برای payload صحیح دکمهٔ قفل."""
    if not full_distribution:
        return '<div class="empty">داده‌ای موجود نیست.</div>'

    tradable = [
        b for b in full_distribution
        if max(b.get("model_prob") or 0.0, b.get("yes_price") or 0.0) >= BUCKET_PLAUSIBILITY_MIN
    ]
    if not tradable:
        return '<div class="empty">داده‌ای موجود نیست.</div>'

    rows = [
        "<table><tr><th>باکت</th><th>احتمال مدل</th><th>احتمال بازار (YES)</th>"
        "<th>قیمت YES</th><th>باور نهایی</th><th>EV</th><th></th></tr>"
    ]
    for b in tradable:
        low, high = b.get("range", [None, None])
        label = _label_for_range(low, high, unit_sym)
        model_prob = b.get("model_prob")
        yes_price = b.get("yes_price")

        model_change = b.get("model_prob_change")
        price_change = b.get("yes_price_change")

        model_str = f"{model_prob * 100:.1f}%" if model_prob is not None else "-"
        if model_prob is not None and model_change is not None:
            model_str += f" ({model_change * 100:+.1f}%)"

        market_str = f"{yes_price * 100:.1f}%" if yes_price is not None else "-"
        if yes_price is not None and price_change is not None:
            market_str += f" ({price_change * 100:+.1f}%)"

        price_str = f"{yes_price:.3f}" if yes_price is not None else "-"
        if yes_price is not None and price_change is not None:
            price_str += f" ({price_change:+.3f})"

        belief_val = b.get("belief_prob")
        belief_str = f"{belief_val * 100:.1f}%" if belief_val is not None else "-"

        ev_val = b.get("ev")
        if ev_val is None:
            ev_str = "-"
        else:
            ev_pct = ev_val * 100
            ev_css = "win" if ev_pct > 0 else ("loss" if ev_pct < 0 else "")
            ev_sign = "+" if ev_pct > 0 else ""
            ev_str = f'<span class="{ev_css}">{ev_sign}{ev_pct:.1f}%</span>' if ev_css else f"{ev_pct:.1f}%"

        market_id = str(b.get("market_id", ""))
        yes_token = b.get("yes_token_id", "")

        is_main = main_signal_id is not None and market_id == main_signal_id
        label_html = f"{label} " + _signal_badge_html(is_main, ev_val)

        is_locked = (city_slug, date, market_id) in locked_keys
        action_html = ""
        status_span = f'<span class="lock-status" id="lockstatus-{market_id}"></span>' if market_id else ""
        if yes_price is not None and market_id:
            if is_locked:
                unlock_call = "unlockBucket('{0}','{1}','{2}','{3}','{4}')".format(
                    city_slug, city_name, date, market_id, label
                )
                action_html = (
                    '<span class="locked-badge">قفل \u2714</span>'
                    f'<button class="unlock-btn" onclick="{unlock_call}">حذف قفل</button>'
                    f'{status_span}'
                )
            else:
                lock_call = "lockBucket('{0}','{1}','{2}','{3}','{4}','YES',{5},'{6}','{7}')".format(
                    city_slug, city_name, date, market_id, yes_token, yes_price, label, market_type
                )
                action_html = (
                    f'<button class="lock-btn" onclick="{lock_call}">قفل کن</button>'
                    f'{status_span}'
                )

        rows.append(
            f"<tr><td>{label_html}</td><td>{model_str}</td><td>{market_str}</td>"
            f"<td>{price_str}</td><td>{belief_str}</td><td>{ev_str}</td><td>{action_html}</td></tr>"
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


def _load_market_by_key_min(city, date):
    """معادل _load_market_by_key ولی برای فایل بازار کمینه -- برای رفع باگ
    سکشن قفل‌های فعال که تا این پچ همیشه فایل بیشینه را می‌خواند، حتی
    برای قفل‌های واقعاً کمینه (market_type == "min")."""
    p = MARKETS_DIR / f"{city}_{date}_min.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _locked_signals_section_html(locks):
    """سکشن مجزا و قابل‌اسکرول بالای صفحه که همهٔ سیگنال‌های قفل‌شدهٔ فعال
    را نشان می‌دهد."""
    open_locks = [l for l in locks if l.get("status") == "open"]
    if not open_locks:
        return ""

    now = datetime.now(timezone.utc)
    visible_rows = []

    for l in sorted(open_locks, key=lambda x: (x.get("city", ""), x.get("date", ""))):
        city = l.get("city", "")
        date = l.get("date", "")
        is_min = l.get("market_type") == "min"

        mkt = _load_market_by_key_min(city, date) if is_min else _load_market_by_key(city, date)

        if mkt is not None and mkt.get("status") in RESOLVED_LIKE_STATUSES:
            continue

        time_str, awaiting_settlement = _local_day_time_label(city, date, now)

        unit_sym = mkt.get("unit", "") if mkt else ""
        bucket_label = _find_locked_bucket_label(mkt, l.get("market_id"), unit_sym)

        name = LOCATIONS.get(city, {}).get("name", city)
        link = _build_polymarket_url_min(city, date) if is_min else _build_polymarket_url(city, date)
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

        last_checked = l.get("last_checked_at")
        if last_checked:
            updated_html = f'<span class="relative-time" data-ts="{last_checked}"></span>'
        else:
            updated_html = "-"

        market_id = str(l.get("market_id", ""))
        unlock_call = "unlockBucket('{0}','{1}','{2}','{3}','{4}')".format(
            city, name, date, market_id, bucket_label
        )
        action_html = (
            f'<button class="unlock-btn" onclick="{unlock_call}">حذف قفل</button>'
            f'<span class="lock-status" id="lockstatus-{market_id}-active"></span>'
        )

        visible_rows.append(
            f"<tr><td>{name_html}</td><td>{date}</td><td>{bucket_label}</td>"
            f"<td>{entry_str}</td><td>{last_str}</td><td>{pct_html}</td>"
            f"<td>{time_str}</td><td>{updated_html}</td><td>{action_html}</td></tr>"
        )

    if not visible_rows:
        return ""

    rows = [
        "<table><tr><th>شهر</th><th>تاریخ</th><th>دمای سیگنال قفل‌شده</th>"
        "<th>قیمت قفل‌شده</th><th>قیمت فعلی</th><th>درصد تغییر</th>"
        "<th>تا پایان روز محلی</th><th>آخرین به‌روزرسانی</th><th>عملیات</th></tr>"
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
    if m.get("time_status") == "awaiting_settlement":
        time_note = "awaiting official settlement"
    else:
        time_note = _hours_left_str(m.get("hours_left"))
    summary = (
        f'<a href="{link}" target="_blank" rel="noopener">{city_name}</a> \u2014 {date}'
        f'  <span class="time-note">{time_note}</span>'
    )
    table = _bucket_table(
        city_slug, city_name, date, unit_sym,
        m.get("full_distribution"), locked_keys, main_signal_id, market_type="max",
    )
    return f"<details class='date-block'><summary>{summary}</summary><div class='table-scroll'>{table}</div></details>"


def _date_block_html_min(m, city_slug, city_name, locked_keys):
    """(فاز ۶) معادل _date_block_html برای بازار کمینه."""
    date = m.get("date", "")
    unit_sym = m.get("unit", "")
    main_signal_id = _main_signal_market_id(m)
    link = _build_polymarket_url_min(city_slug, date)
    if m.get("time_status") == "awaiting_settlement":
        time_note = "awaiting official settlement"
    else:
        time_note = _hours_left_str(m.get("hours_left"))
    summary = (
        f'<a href="{link}" target="_blank" rel="noopener">{city_name}</a> \u2014 {date}'
        f'  <span class="time-note">{time_note}</span>'
    )
    table = _bucket_table(
        city_slug, city_name, date, unit_sym,
        m.get("full_distribution"), locked_keys, main_signal_id, market_type="min",
    )
    return f"<details class='date-block'><summary>{summary}</summary><div class='table-scroll'>{table}</div></details>"


def _daily_pnl_section_html(locks):
    """سکشن مستقل و جدید: برآیند کل (میانگین درصد سود/زیان) هر روز، بر
    اساس روزی که واقعاً قفل بسته شده (closed_at، به وقت ایران) -- نه
    تاریخ هدف بازار. کاملاً مستقل از _history_table_html/lock_history.csv
    است؛ مستقیماً از خودِ locks محاسبه می‌شود، بدون اطلاعات اضافی به‌جز
    تاریخ و درصد."""
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
        if ZoneInfo is not None:
            try:
                local_date = dt_utc.astimezone(ZoneInfo(IRAN_TZ_NAME)).strftime("%Y-%m-%d")
            except Exception:
                local_date = dt_utc.strftime("%Y-%m-%d")
        else:
            local_date = dt_utc.strftime("%Y-%m-%d")
        pct = (exit_price - entry) / entry * 100
        daily.setdefault(local_date, []).append(pct)

    if not daily:
        return ""

    rows = []
    for date in sorted(daily.keys(), reverse=True):
        pcts = daily[date]
        avg_pct = sum(pcts) / len(pcts)
        css = "win" if avg_pct >= 0 else "loss"
        rows.append(
            f"<tr><td>{date}</td><td><span class=\'{css}\'>{avg_pct:+.1f}%</span></td></tr>"
        )

    table_html = (
        "<table><tr><th>تاریخ</th><th>برآیند کل</th></tr>" + "".join(rows) + "</table>"
    )
    return (
        "<h2>\U0001F4C5 گزارش روزانه</h2>"
        f"<div class=\'table-scroll\'>{table_html}</div>"
    )


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


def _build_group_section(markets, date_block_fn):
    """(فاز ۶) کپسوله‌سازی مشترک منطق ساخت بلوک‌های شهر -- برای هر دو
    بخش (max/min) بدون کپی کد استفاده می‌شود."""
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
        return '<div class="empty">هیچ بازار بازی برای نمایش وجود ندارد.</div>'

    for slug in sorted_slugs:
        city_name = city_names[slug]
        mlist = groups[slug]
        has_signal = group_has_signal(mlist)
        badge = '<span class="main-badge">سیگنال فعال</span>' if has_signal else ""

        if len(mlist) == 1:
            inner = date_block_fn(mlist[0], slug, city_name, locked_keys)
            summary = f"<b>{city_name}</b> {badge}"
        else:
            inner = "".join(date_block_fn(m, slug, city_name, locked_keys) for m in mlist)
            summary = f"<b>{city_name}</b> {badge} &nbsp;({len(mlist)} تاریخ)"

        parts.append(
            f"<details class='city-block' data-city-name='{city_name}'>"
            f"<summary>{summary}</summary>{inner}</details>"
        )

    return "".join(parts)


def build_simple_dashboard():
    markets = [m for m in _load_all_markets() if m.get("status") == "open" and m.get("full_distribution")]
    locks = _load_locks()

    body_html = _locked_signals_section_html(locks) + _build_group_section(markets, _date_block_html)

    # (فاز ۶) بخش کمینه -- کاملاً موازی، بدون تغییر منطق بالا.
    min_markets = [m for m in _load_all_min_markets() if m.get("status") == "open" and m.get("full_distribution")]
    min_body_html = _build_group_section(min_markets, _date_block_html_min)

    history_html, history_city_options, history_date_options = _history_table_html(locks)
    daily_pnl_html = _daily_pnl_section_html(locks)

    scan_iso, scan_kind = _latest_scan_info()
    reference_dt = datetime.fromisoformat(scan_iso) if scan_iso else datetime.now(timezone.utc)

    html = HTML_TEMPLATE
    html = html.replace("LASTUPDATE", _iran_time_str(reference_dt))
    html = html.replace("LASTSCANISO", scan_iso or "")
    html = html.replace("LASTSCANKIND", scan_kind or "")
    html = html.replace("LASTSCANFALLBACK", "بدون سابقهٔ اسکن" if not scan_iso else "")
    html = html.replace("MIN_BODYHTML", min_body_html)
    html = html.replace("BODYHTML", body_html)
    html = html.replace("DAILYPNLHTML", daily_pnl_html)
    html = html.replace("HISTORYHTML", history_html)
    html = html.replace("HISTORYCITYOPTIONS", history_city_options)
    html = html.replace("HISTORYDATEOPTIONS", history_date_options)
    html = html.replace("GITHUB_REPO_PLACEHOLDER", GITHUB_REPO)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    return str(OUTPUT_FILE.resolve())


if __name__ == "__main__":
    path = build_simple_dashboard()
    print(f"simple.html ساخته شد: {path}")
