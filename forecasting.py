"""
forecasting.py -- Ensemble weather forecasting + calibration engine for WeatherBet v3
=====================================================================================
Replaces deterministic single-value forecasts with real ensemble distributions
(ECMWF ensemble + GEFS ensemble) and calibrates them against historical
forecast-vs-actual data (no need to wait 30 days of live paper trading).

FIX LOG (this version -- full_completeness_v2.py)

1. full_bucket_distribution() normalizes on UNROUNDED probabilities, rounds
LAST (see bucket_probability/full_bucket_distribution).
2. `distribution_sum(dist)` lets callers sanity-check completeness.
(Student's t fat-tailed CDF unchanged -- see TAIL_DF.)

--- PHASE 2 (WEATHERBOT_ROADMAP.md, F2) --------------------------------------
`fetch_historical_actual()` fetches ground truth from the SAME station feed
used for live resolution (NOAA METAR archive / Hong Kong Observatory), not a
gridded reanalysis product. Disk cache: `data/actuals_cache.json`.

--- PHASE 4 (F4) --------------------------------------------------------------
`_bias_cap()` is DYNAMIC: `max(flat default, legacy manual floor,
BIAS_CAP_SAFETY_MARGIN x observed |raw bias|)`, bounded by an absolute
ceiling (`ABSOLUTE_BIAS_CAP_F`/`_C`).

--- PHASE 5 (F5) --------------------------------------------------------------
`backfill_calibration()` uses a day-of-year seasonal weighting
(`_seasonal_weight`, 45-day half-life) instead of a flat 180-day average, so
the current season's bias isn't diluted by a milder/stronger season earlier
in the rolling window.

--- PHASE 6 FIX LOG (WEATHERBOT_ROADMAP.md, یافته F6) -------------------------
Sigma for horizons D1/D2/D3 used to be pure guesswork: `base_mae * sqrt(1 +
h*0.6)`, a synthetic scaling of the D0 error with NO real measurement of how
much forecast skill actually degrades at 1/2/3 days lead time for that
specific city. Investigated whether a real per-horizon measurement is
possible: Open-Meteo's "Previous Model Runs API"
(previous-runs-api.open-meteo.com) archives forecasts at FIXED lead-time
offsets (`temperature_2m_previous_day1/2/3`, hourly, from Jan 2024 for most
models) specifically for this kind of lead-time skill analysis -- exactly
what was needed, and it was previously unused in this codebase.

FIX: `fetch_historical_forecast_by_horizon()` pulls the archived HOURLY
forecast at each lead-time offset (there is no daily-max variant in this
API, only hourly), aggregates it to a real daily max locally
(`_daily_max_from_hourly_previous_day`), and `backfill_calibration()` now
compares this REAL per-horizon forecast against the (Phase-2-corrected)
actual, with the SAME seasonal weighting as Phase 5, to get a genuinely
measured sigma/bias for D1/D2/D3 -- not a guess.

SAFETY / HONESTY NOTE: this is a NEW external API not previously used or
tested against live traffic in this project. Built with an explicit, tested
FALLBACK: if fewer than 20 real per-horizon samples come back for a given
city/horizon (endpoint down, insufficient history, unexpected response
shape, etc.), that horizon silently reverts to the OLD synthetic
`sqrt(1+0.6h)` scaling -- never crashes, never leaves a horizon
uncalibrated. Each `cal[key]["source"]` now says
"historical_backfill_real_horizon" or "historical_backfill_synthetic_fallback"
so you can see, per city per horizon, which path was actually used after a
real backfill run.

Output shape of `cal[key]` unchanged (still sigma/bias/base_mae/
base_bias_raw/n/source/updated_at, plus the Phase-5
`seasonal_half_life_days` field) -- get_sigma()/get_bias() need zero changes.
=====================================================================================
"""

import json
import math
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CALIBRATION_FILE = DATA_DIR / "calibration.json"
ACTUALS_CACHE_FILE = DATA_DIR / "actuals_cache.json"

DEFAULT_SIGMA_F = 2.5
DEFAULT_SIGMA_C = 1.4
DEFAULT_BIAS_F = 0.0
DEFAULT_BIAS_C = 0.0
BIAS_CAP_C = 2.0
BIAS_CAP_F = 3.6
ABSOLUTE_BIAS_CAP_F = 10.0
ABSOLUTE_BIAS_CAP_C = 5.5
BIAS_CAP_SAFETY_MARGIN = 1.3
SEASONAL_HALF_LIFE_DAYS = 45
# --- FIX (Phase 6 / F6): minimum real per-horizon samples required before
# trusting the new real-lead-time measurement over the old synthetic scaling.
MIN_REAL_HORIZON_SAMPLES = 20
TAIL_DF = 5.0
MAX_RETRIES = 3
RETRY_DELAY_S = 3

BIAS_CAP_OVERRIDES = {
    "los-angeles": 7.5,
    "shanghai": 3.0,
    "seoul": 2.5,
    "kuala-lumpur": 2.5,
    "munich": 2.5,
    "tel-aviv": 2.5,
    "dallas": 3.6,
    "singapore": 2.5,
    "miami": 3.6,
    "mexico-city": 2.0,
    "hong-kong": 2.5,
    "houston": 3.6,
}


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _betacf(a, b, x, maxit=200, eps=3e-7, fpmin=1e-30):
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    else:
        return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t, df=TAIL_DF):
    x = df / (df + t * t)
    p = 0.5 * _betai(df / 2.0, 0.5, x)
    return 1.0 - p if t > 0 else p


def _bias_cap(unit, city_slug=None, observed_raw_bias=None):
    """(Phase 4 / F4: dynamic cap -- see module docstring.)"""
    flat_default = BIAS_CAP_F if unit == "F" else BIAS_CAP_C
    absolute_ceiling = ABSOLUTE_BIAS_CAP_F if unit == "F" else ABSOLUTE_BIAS_CAP_C
    floor = flat_default
    if city_slug is not None and city_slug in BIAS_CAP_OVERRIDES:
        floor = max(floor, BIAS_CAP_OVERRIDES[city_slug])
    if observed_raw_bias is not None:
        dynamic = min(absolute_ceiling, abs(observed_raw_bias) * BIAS_CAP_SAFETY_MARGIN)
        floor = max(floor, dynamic)
    return round(floor, 3)


def _day_of_year_distance(d1, d2):
    """(Phase 5 / F5.) Circular day-of-year distance, handles year wrap."""
    doy1 = d1.timetuple().tm_yday
    doy2 = d2.timetuple().tm_yday
    diff = abs(doy1 - doy2)
    return min(diff, 365 - diff)


def _seasonal_weight(date_obj, reference_date, half_life_days=SEASONAL_HALF_LIFE_DAYS):
    """(Phase 5 / F5.) Weight decaying by day-of-year distance from "today"."""
    dist = _day_of_year_distance(date_obj, reference_date)
    return 0.5 ** (dist / half_life_days)


def _weighted_mean(values, weights):
    total_w = sum(weights) or 1.0
    return sum(v * w for v, w in zip(values, weights)) / total_w


# =============================================================================
# ENSEMBLE FETCH (LIVE FORECASTING) -- unchanged
# =============================================================================

def _fetch_ensemble(lat, lon, tz, unit, models, forecast_days=7):
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&forecast_days={forecast_days}&timezone={tz}"
        f"&models={models}"
    )
    for attempt in range(MAX_RETRIES):
        try:
            data = requests.get(url, timeout=(5, 12)).json()
            if "error" in data:
                return {}
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            result = {d: [] for d in dates}
            for key, series in daily.items():
                if key == "time" or not key.startswith("temperature_2m_max"):
                    continue
                for d, v in zip(dates, series):
                    if v is not None:
                        result[d].append(v)
            return result
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_S)
            else:
                return {}
    return {}


def get_ecmwf_ensemble(lat, lon, tz, unit):
    return _fetch_ensemble(lat, lon, tz, unit, "ecmwf_ifs025", forecast_days=7)


def get_gefs_ensemble(lat, lon, tz, unit):
    return _fetch_ensemble(lat, lon, tz, unit, "gfs_seamless", forecast_days=7)


def build_combined_distribution(city_slug, loc, date_str):
    members = []
    ecmwf = get_ecmwf_ensemble(loc["lat"], loc["lon"], loc["tz"], loc["unit"])
    gefs = get_gefs_ensemble(loc["lat"], loc["lon"], loc["tz"], loc["unit"])
    members.extend(ecmwf.get(date_str, []))
    members.extend(gefs.get(date_str, []))
    return members


# =============================================================================
# CALIBRATION (HISTORICAL BACKFILL)
# =============================================================================

def fetch_historical_forecast(lat, lon, tz, unit, start_date, end_date):
    """Unchanged. Used as the D0 baseline AND as the fallback source for any
    horizon that doesn't get enough real per-horizon samples (Phase 6)."""
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    url = (
        f"https://historical-forecast-api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&timezone={tz}&start_date={start_date}&end_date={end_date}"
        f"&models=ecmwf_ifs025"
    )
    for attempt in range(MAX_RETRIES):
        try:
            data = requests.get(url, timeout=(5, 15)).json()
            daily = data.get("daily", {})
            return dict(zip(daily.get("time", []), daily.get("temperature_2m_max", [])))
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_S)
            else:
                return {}
    return {}


def _daily_max_from_hourly_previous_day(hourly_data, var_key):
    """(Phase 6 / F6.) The Previous Runs API only exposes HOURLY variables
    (no daily-max variant), so we aggregate to a daily max ourselves."""
    times = hourly_data.get("time", [])
    series = hourly_data.get(var_key, [])
    daily_max = {}
    for t_str, v in zip(times, series):
        if v is None:
            continue
        date_part = t_str[:10]
        if date_part not in daily_max or v > daily_max[date_part]:
            daily_max[date_part] = v
    return daily_max


def fetch_historical_forecast_by_horizon(lat, lon, tz, unit, start_date, end_date, horizon_days=(0, 1, 2, 3)):
    """
    FIX (Phase 6 / F6). Real per-horizon forecast series via Open-Meteo's
    Previous Model Runs API (fixed lead-time offsets), aggregated to daily
    max locally. Returns dict[h] -> dict[date_str] -> daily_max_float.

    On ANY failure (network error, unexpected/empty response), returns a
    dict of empty sub-dicts for every requested horizon -- callers must
    treat an empty/sparse result as "not enough real data, use the fallback"
    (this file's backfill_calibration() already does).
    """
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    var_names = [f"temperature_2m_previous_day{h}" for h in horizon_days]
    url = (
        f"https://previous-runs-api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly={','.join(var_names)}"
        f"&temperature_unit={temp_unit}&timezone={tz}"
        f"&start_date={start_date}&end_date={end_date}"
        f"&models=ecmwf_ifs025"
    )
    result = {h: {} for h in horizon_days}
    for attempt in range(MAX_RETRIES):
        try:
            data = requests.get(url, timeout=(5, 20)).json()
            hourly = data.get("hourly", {})
            if not hourly:
                return result
            for h in horizon_days:
                key = f"temperature_2m_previous_day{h}"
                if key in hourly:
                    result[h] = _daily_max_from_hourly_previous_day(hourly, key)
            return result
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_S)
            else:
                return result
    return result


def _load_actuals_cache():
    if ACTUALS_CACHE_FILE.exists():
        try:
            return json.loads(ACTUALS_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_actuals_cache(cache):
    ACTUALS_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _fetch_metar_max_for_date(station, unit, date_str):
    url = (
        f"https://aviationweather.gov/api/data/metar"
        f"?ids={station}&format=json&date={date_str.replace('-', '')}"
    )
    for attempt in range(MAX_RETRIES):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if not data:
                return None
            temps = [float(d.get("temp")) for d in data if d.get("temp") is not None]
            if not temps:
                return None
            max_c = max(temps)
            return round(max_c * 9 / 5 + 32, 1) if unit == "F" else round(max_c, 1)
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_S)
            else:
                return None
    return None


def _fetch_historical_actual_noaa_range(station, unit, start_date, end_date, cache):
    station_cache = cache.setdefault(station, {})
    results = {}
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    while d <= end:
        date_str = d.isoformat()
        if date_str in station_cache:
            val = station_cache[date_str]
        else:
            val = _fetch_metar_max_for_date(station, unit, date_str)
            station_cache[date_str] = val
            time.sleep(0.2)
        if val is not None:
            results[date_str] = val
        d += timedelta(days=1)
    return results


def _fetch_historical_actual_hko_range(start_date, end_date, cache):
    station_cache = cache.setdefault("HKO", {})
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    needed_dates = set()
    while d <= end:
        needed_dates.add(d.isoformat())
        d += timedelta(days=1)

    if not needed_dates.issubset(station_cache.keys()):
        try:
            url = (
                "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
                "?dataType=CLMMAXT&lang=en&rformat=json&station=HKO"
            )
            data = requests.get(url, timeout=(5, 15)).json()
            for row in data.get("data", []):
                raw_date = str(row.get("date", ""))
                if len(raw_date) == 8 and row.get("value") is not None:
                    iso = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                    station_cache[iso] = round(float(row["value"]), 1)
        except Exception:
            pass

    return {dt: station_cache[dt] for dt in needed_dates if dt in station_cache and station_cache[dt] is not None}


def fetch_historical_actual(loc, start_date, end_date):
    """(Phase 2 / F2.) Ground truth from the SAME station feed used for live
    resolution. Output shape: dict[date_str] -> float."""
    cache = _load_actuals_cache()
    unit = loc["unit"]
    if loc.get("resolve_source") == "hko":
        result = _fetch_historical_actual_hko_range(start_date, end_date, cache)
    else:
        result = _fetch_historical_actual_noaa_range(loc["station"], unit, start_date, end_date, cache)
    _save_actuals_cache(cache)
    return result


def backfill_calibration(locations, lookback_days=180, horizon_days=(0, 1, 2, 3)):
    cal = load_calibration()
    end = datetime.now(timezone.utc).date() - timedelta(days=6)
    start = end - timedelta(days=lookback_days)
    reference_date = end  # Phase 5: "today" for seasonal weighting

    for city_slug, loc in locations.items():
        forecasts = fetch_historical_forecast(
            loc["lat"], loc["lon"], loc["tz"], loc["unit"],
            start.isoformat(), end.isoformat()
        )
        actuals = fetch_historical_actual(loc, start.isoformat(), end.isoformat())

        if not forecasts or not actuals:
            print(f"  [CAL-SKIP] {loc['name']}: no historical data")
            continue

        abs_errors = []
        signed_errors = []
        weights = []
        for date_str, fval in forecasts.items():
            actual = actuals.get(date_str)
            if fval is not None and actual is not None:
                abs_errors.append(abs(fval - actual))
                signed_errors.append(actual - fval)
                d_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                weights.append(_seasonal_weight(d_obj, reference_date))

        if len(abs_errors) < 20:
            print(f"  [CAL-SKIP] {loc['name']}: only {len(abs_errors)} samples")
            continue

        base_mae = _weighted_mean(abs_errors, weights)
        base_bias_raw = _weighted_mean(signed_errors, weights)

        # --- FIX (Phase 6 / F6): try to get a REAL per-horizon measurement
        # for each horizon; fall back to the old synthetic sqrt(1+0.6h)
        # scaling of the D0 measurement above if not enough real samples.
        per_horizon_forecast = fetch_historical_forecast_by_horizon(
            loc["lat"], loc["lon"], loc["tz"], loc["unit"],
            start.isoformat(), end.isoformat(), horizon_days
        )

        horizon_summary = []
        for h in horizon_days:
            horizon_forecasts = per_horizon_forecast.get(h, {})
            abs_errors_h, signed_errors_h, weights_h = [], [], []
            for date_str, fval in horizon_forecasts.items():
                actual = actuals.get(date_str)
                if fval is not None and actual is not None:
                    abs_errors_h.append(abs(fval - actual))
                    signed_errors_h.append(actual - fval)
                    d_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                    weights_h.append(_seasonal_weight(d_obj, reference_date))

            if len(abs_errors_h) >= MIN_REAL_HORIZON_SAMPLES:
                mae_h = _weighted_mean(abs_errors_h, weights_h)
                bias_raw_h = _weighted_mean(signed_errors_h, weights_h)
                n_h = len(abs_errors_h)
                source_h = "historical_backfill_real_horizon"
            else:
                g = math.sqrt(1 + h * 0.6)
                mae_h = base_mae * g
                bias_raw_h = base_bias_raw
                n_h = len(abs_errors)
                source_h = "historical_backfill_synthetic_fallback"

            cap = _bias_cap(loc["unit"], city_slug, observed_raw_bias=bias_raw_h)
            bias_h = max(-cap, min(cap, bias_raw_h))
            key = f"{city_slug}_D{h}"
            cal[key] = {
                "sigma": round(mae_h, 3), "bias": round(bias_h, 3),
                "base_mae": round(mae_h, 3), "base_bias_raw": round(bias_raw_h, 3),
                "n": n_h, "source": source_h,
                "seasonal_half_life_days": SEASONAL_HALF_LIFE_DAYS,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            horizon_summary.append(f"D{h}:{source_h.split('_')[-1]}(n={n_h})")

        print(f"  [CAL] {loc['name']}: base_mae={base_mae:.2f} bias={base_bias_raw:+.2f} "
              f"n={len(abs_errors)} | " + " ".join(horizon_summary))
        time.sleep(0.5)

    save_calibration(cal)
    return cal


def load_calibration():
    if CALIBRATION_FILE.exists():
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    return {}


def save_calibration(cal):
    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2, ensure_ascii=False), encoding="utf-8")


def update_calibration_from_live(markets, locations):
    cal = load_calibration()
    resolved = [
        m for m in markets
        if m.get("status") in ("resolved", "resolved_no_signal")
        and m.get("actual_temp") is not None
    ]
    for city_slug in locations:
        unit = locations[city_slug]["unit"]
        for h in range(4):
            group = [m for m in resolved if m["city"] == city_slug and m.get("horizon_days") == h]
            if len(group) < 15:
                continue
            abs_errors = [abs(m["forecast_mean"] - m["actual_temp"]) for m in group if m.get("forecast_mean") is not None]
            signed_errors = [m["actual_temp"] - m["forecast_mean"] for m in group if m.get("forecast_mean") is not None]
            if not abs_errors:
                continue

            live_mae = sum(abs_errors) / len(abs_errors)
            live_bias_raw = sum(signed_errors) / len(signed_errors)
            n_live = len(abs_errors)
            cap = _bias_cap(unit, city_slug, observed_raw_bias=live_bias_raw)

            key = f"{city_slug}_D{h}"
            old = cal.get(key, {})
            default_sigma = DEFAULT_SIGMA_F if unit == "F" else DEFAULT_SIGMA_C
            default_bias = DEFAULT_BIAS_F if unit == "F" else DEFAULT_BIAS_C
            old_sigma = old.get("sigma", default_sigma)
            old_bias = old.get("bias", default_bias)

            w_live = min(0.7, n_live / (n_live + 30))
            new_sigma = round(old_sigma * (1 - w_live) + live_mae * w_live, 3)
            new_bias_raw = old_bias * (1 - w_live) + live_bias_raw * w_live
            new_bias = round(max(-cap, min(cap, new_bias_raw)), 3)

            cal[key] = {
                "sigma": new_sigma, "bias": new_bias, "n_live": n_live,
                "live_mae": round(live_mae, 3), "live_bias_raw": round(live_bias_raw, 3),
                "source": "blended", "updated_at": datetime.now(timezone.utc).isoformat(),
            }
    save_calibration(cal)
    return cal


def get_sigma(city_slug, horizon_days, unit):
    cal = load_calibration()
    h = min(horizon_days, 3)
    key = f"{city_slug}_D{h}"
    if key in cal:
        return cal[key]["sigma"]
    return DEFAULT_SIGMA_F if unit == "F" else DEFAULT_SIGMA_C


def get_bias(city_slug, horizon_days, unit):
    cal = load_calibration()
    h = min(horizon_days, 3)
    key = f"{city_slug}_D{h}"
    if key in cal:
        raw = cal[key].get("live_bias_raw", cal[key].get("base_bias_raw"))
        cap = _bias_cap(unit, city_slug, observed_raw_bias=raw)
        return max(-cap, min(cap, cal[key].get("bias", 0.0)))
    return DEFAULT_BIAS_F if unit == "F" else DEFAULT_BIAS_C


# =============================================================================
# PROBABILITY DISTRIBUTION FROM ENSEMBLE + CALIBRATED SIGMA/BIAS -- unchanged
# =============================================================================

def build_calibrated_distribution(members, city_slug, horizon_days, unit):
    calibrated_sigma = get_sigma(city_slug, horizon_days, unit)
    bias = get_bias(city_slug, horizon_days, unit)

    if not members:
        return None, calibrated_sigma

    raw_mean = sum(members) / len(members)
    corrected_mean = raw_mean + bias

    if len(members) > 1:
        raw_sigma = (sum((x - raw_mean) ** 2 for x in members) / (len(members) - 1)) ** 0.5
    else:
        raw_sigma = calibrated_sigma

    final_sigma = max(raw_sigma, calibrated_sigma)
    return round(corrected_mean, 2), round(final_sigma, 3)


def bucket_probability(mean, sigma, t_low, t_high):
    if mean is None:
        return 0.0
    s = max(sigma, 0.3)
    if t_low <= -998:
        return student_t_cdf((t_high + 0.5 - mean) / s)
    if t_high >= 998:
        return 1.0 - student_t_cdf((t_low - 0.5 - mean) / s)
    lo = t_low - 0.5 if t_low == t_high else t_low
    hi = t_high + 0.5 if t_low == t_high else t_high
    return student_t_cdf((hi - mean) / s) - student_t_cdf((lo - mean) / s)


def full_bucket_distribution(mean, sigma, outcomes):
    raw = []
    for o in outcomes:
        t_low, t_high = o["range"]
        raw.append(bucket_probability(mean, sigma, t_low, t_high))
    total = sum(raw) or 1.0
    dist = []
    for o, p in zip(outcomes, raw):
        dist.append({**o, "model_prob": round(p / total, 4)})
    return dist


def distribution_sum(dist):
    return round(sum(d.get("model_prob", 0.0) for d in dist), 4)


def scenario_grid(mean, sigma, n_sigma=4.0, step=0.5):
    if mean is None:
        return []
    s = max(sigma, 0.3)
    lo = mean - n_sigma * s
    hi = mean + n_sigma * s
    n_steps = max(1, int(round((hi - lo) / step)))
    points = [lo + i * step for i in range(n_steps + 1)]
    raw = []
    for t in points:
        p = student_t_cdf((t + step / 2 - mean) / s) - student_t_cdf((t - step / 2 - mean) / s)
        raw.append((round(t, 2), p))
    total = sum(p for _, p in raw) or 1.0
    return [(t, p / total) for t, p in raw]
