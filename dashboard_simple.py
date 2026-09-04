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
  ۷) زمان باقی‌مانده به انگلیسی نوشته می‌شود ("2h 15m remaining") تا با
     فارسی قاطی نشود و به‌هم‌ریختگی جهت متن پیش نیاید (طبق بازخورد کاربر).
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import history_manager

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

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>WeatherBet -- ساده</title>
<style>
* { box-sizing: border-box; }
body{background:#0f1115;color:#e6e6e6;font-family:Tahoma,Vazir,sans-serif;padding:12px;margin:0;font-size:15px}
h1{font-size:18px;margin:8px 0}
h2{font-size:15px;margin-top:28px;border-bottom:1px solid #2a2e37;padding-bottom:6px;scroll-margin-top:16px}
.meta{color:#9aa0a6;font-size:12px;margin-bottom:12px;line-height:1.6}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.toolbar input[type=text]{flex:1;min-width:140px;background:#181b21;border:1px solid #2a2e37;color:#e6e6e6;border-radius:8px;padding:9px 12px;font-size:13px}
.jump-btn{display:inline-block;background:#2563eb;color:#fff;border-radius:8px;padding:9px 14px;font-size:12.5px;text-decoration:none;white-space:nowrap}
a{color:#60a5fa}
details.city-block{background:#14161b;border:1px solid #2a2e37;border-radius:10px;padding:6px 10px;margin-bottom:8px}
details.date-block{background:#101216;border:1px solid #23262d;border-radius:8px;padding:6px 10px;margin:6px 0}
summary{cursor:pointer;font-size:13.5px;color:#c7ccd1;list-style:none;padding:6px 2px}
summary::-webkit-details-marker{display:none}
summary::before{content:"\u25B8";color:#6b7280;font-size:11px;margin-left:6px}
details[open]>summary::before{content:"\u25BE"}
.main-badge{background:#16a34a33;color:#4ade80;border:1px solid #16a34a;border-radius:6px;padding:2px 6px;font-size:10.5px;white-space:nowrap;margin-right:6px}
.time-note{color:#9aa0a6;font-size:11px;direction:ltr;unicode-bidi:embed;display:inline-block}
.table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:8px}
table{width:100%;min-width:480px;border-collapse:collapse;font-size:12.5px}
th,td{padding:8px 6px;text-align:center;border-bottom:1px solid #23262d;white-space:nowrap}
th{color:#9aa0a6;font-weight:normal;font-size:11.5px}
.lock-btn,.unlock-btn{border:none;border-radius:8px;padding:9px 14px;font-size:12.5px;cursor:pointer;min-height:38px}
.lock-btn{background:#2563eb;color:#fff}
.unlock-btn{background:#dc2626;color:#fff}
.locked-badge{color:#4ade80;font-size:11px;display:block;margin-bottom:4px}
.empty{color:#6b7280;font-style:italic;padding:12px 0}
.win{color:#4ade80}
.loss{color:#f87171}
.history-filters{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.history-filters select{background:#181b21;border:1px solid #2a2e37;color:#e6e6e6;border-radius:8px;padding:8px 10px;font-size:12.5px}
.download-btn{display:inline-block;margin:10px 0;background:#374151;color:#e6e6e6;border:1px solid #4b5563;border-radius:8px;padding:10px 16px;font-size:12.5px;text-decoration:none}
@media (max-width: 480px){
  body{padding:8px;font-size:14px}
  th,td{padding:7px 5px;font-size:11.5px}
}
</style>
</head>
<body>
<h1>WeatherBet -- داشبورد ساده</h1>
<div class="meta">آخرین به‌روزرسانی: LASTUPDATE UTC<br>فقط دما / احتمال مدل / احتمال بازار -- بدون سایزینگ</div>
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
function lockBucket(city, cityName, date, marketId, tokenId, side, price, label) {
  const repo = "GITHUB_REPO_PLACEHOLDER";
  const payload = {
    action: "lock", city: city, date: date,
    market_id: marketId + "|" + tokenId, side: side,
    price: price, locked_at: new Date().toISOString()
  };
  const title = encodeURIComponent("LOCK " + cityName + " " + date + " " + label + " " + side + " @ " + price);
  const body = encodeURIComponent(JSON.stringify(payload, null, 2));
  const url = "https://github.com/" + repo + "/issues/new?title=" + title + "&body=" + body + "&labels=lock-request";
  window.open(url, "_blank");
}
function unlockBucket(city, cityName, date, marketId, label) {
  const repo = "GITHUB_REPO_PLACEHOLDER";
  const payload = { action: "unlock", city: city, date: date, market_id: marketId };
  const title = encodeURIComponent("UNLOCK " + cityName + " " + date + " " + label);
  const body = encodeURIComponent(JSON.stringify(payload, null, 2));
  const url = "https://github.com/" + repo + "/issues/new?title=" + title + "&body=" + body + "&labels=unlock-request";
  window.open(url, "_blank");
}
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
        "<th>قیمت YES</th><th></th></tr>"
    ]
    for b in full_distribution:
        low, high = b.get("range", [None, None])
        label = _label_for_range(low, high, unit_sym)
        model_prob = b.get("model_prob")
        yes_price = b.get("yes_price")
        model_str = f"{model_prob * 100:.1f}%" if model_prob is not None else "-"
        market_str = f"{yes_price * 100:.1f}%" if yes_price is not None else "-"
        price_str = f"{yes_price:.3f}" if yes_price is not None else "-"
        market_id = str(b.get("market_id", ""))
        yes_token = b.get("yes_token_id", "")

        is_main = main_signal_id is not None and market_id == main_signal_id
        label_html = f"{label} " + ('<span class="main-badge">سیگنال اصلی</span>' if is_main else "")

        is_locked = (city_slug, date, market_id) in locked_keys
        action_html = ""
        if yes_price is not None and market_id:
            if is_locked:
                action_html = (
                    f'<span class="locked-badge">قفل \u2714</span>'
                    f'<button class="unlock-btn" onclick="unlockBucket(\'{city_slug}\',\'{city_name}\','
                    f"'{date}','{market_id}','{label}')\">حذف قفل</button>"
                )
            else:
                action_html = (
                    f'<button class="lock-btn" onclick="lockBucket(\'{city_slug}\',\'{city_name}\','
                    f"'{date}','{market_id}','{yes_token}','YES',{yes_price},'{label}')\">قفل کن</button>"
                )

        rows.append(
            f"<tr><td>{label_html}</td><td>{model_str}</td><td>{market_str}</td>"
            f"<td>{price_str}</td><td>{action_html}</td></tr>"
        )
    rows.append("</table>")
    return "".join(rows)


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

    body_html = "".join(parts)
    history_html, history_city_options, history_date_options = _history_table_html(locks)

    html = HTML_TEMPLATE
    html = html.replace("LASTUPDATE", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
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
