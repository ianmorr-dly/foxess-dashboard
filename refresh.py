"""
Refreshes the FoxESS dashboard's data in-place.

Fetches the latest snapshot/history/report data from the FoxESS Cloud
OpenAPI, folds it into the same JSON shape the dashboard's JS expects, and
substitutes it into the repo's own index.html (which already contains the
full page markup/CSS/JS from the last build — this script only replaces
the embedded data blob and the "snapshot fetched" timestamp).

Required environment variables (set as GitHub Actions secrets):
  FOXESS_API_KEY   - FoxESS Cloud OpenAPI key
  FOXESS_DEVICE_SN - inverter serial number

Neither the station name nor the device serial number is ever written to
index.html or printed to logs — this script only uses the SN to make API
calls and always renders the dashboard under a generic station name.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from foxess_client import FoxESSClient, FoxESSError  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML_PATH = os.path.join(REPO_ROOT, "index.html")
CACHE_PATH = os.path.join(REPO_ROOT, "data", "cheap_rate_cache.json")
LOCAL_TZ = ZoneInfo("Europe/London")
GENERIC_NAME = "Home Energy"

HIST_VARS = [
    "pvPower", "loadsPower", "generationPower", "feedinPower",
    "gridConsumptionPower", "batChargePower", "batDischargePower",
    "SoC", "batTemperature",
]
REPORT_VARS = [
    "generation", "feedin", "gridConsumption", "loads",
    "chargeEnergyToTal", "dischargeEnergyToTal",
]
CHEAP_HOURS = set([23, 0, 1, 2, 3, 4, 5])  # 23:00-24:00 and 00:00-06:00, same calendar date


def local_to_epoch_ms(dt_local: datetime) -> int:
    return int(dt_local.replace(tzinfo=LOCAL_TZ).timestamp() * 1000)


def parse_local_time(s: str):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})", s)
    if not m:
        return None
    y, mo, d, h, mi, se = (int(x) for x in m.groups())
    return local_to_epoch_ms(datetime(y, mo, d, h, mi, se))


def month_to_daily(month_block):
    y, m = month_block["y"], month_block["m"]
    result = month_block["result"] or []
    by_date = {}
    for series in result:
        var = series["variable"]
        for i, val in enumerate(series["values"]):
            day = i + 1
            try:
                d = datetime(y, m, day).strftime("%Y-%m-%d")
            except ValueError:
                continue
            by_date.setdefault(d, {})[var] = val
    return by_date


def load_cache():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    # keep the file small: last 60 days is plenty since the dashboard only
    # ever shows the most recent 30
    cutoff = (datetime.now(LOCAL_TZ).date() - timedelta(days=60)).strftime("%Y-%m-%d")
    trimmed = {d: v for d, v in cache.items() if d >= cutoff}
    with open(CACHE_PATH, "w") as f:
        json.dump(trimmed, f, indent=2, sort_keys=True)


def fetch_cheap_peak_for_date(client, sn, target_date_str):
    """FoxESS's per-day hourly report is keyed one day off from the date it
    actually describes (matches the offset already baked into the
    dashboard's existing history), so we request target_date - 1 day."""
    target = datetime.strptime(target_date_str, "%Y-%m-%d")
    request_date = target - timedelta(days=1)
    result = client.report_query(
        sn, dimension="day", year=request_date.year, month=request_date.month,
        day=request_date.day, variables=["gridConsumption"],
    )
    if not result:
        return None
    values = result[0].get("values") or [0] * 24
    cheap = sum(values[h] for h in CHEAP_HOURS if h < len(values))
    peak = sum(values[h] for h in range(len(values)) if h not in CHEAP_HOURS)
    return {"gridCheap": round(cheap, 2), "gridPeak": round(peak, 2)}


def main():
    api_key = os.environ["FOXESS_API_KEY"]
    sn = os.environ["FOXESS_DEVICE_SN"]
    client = FoxESSClient(api_key)

    now_local = datetime.now(LOCAL_TZ)
    now_utc = datetime.now(timezone.utc)
    start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    print("Fetching real-time snapshot...")
    real_result = client.real_query([sn])
    real_datas = real_result[0]["datas"] if real_result and real_result[0].get("datas") else []
    real = {d["variable"]: d for d in real_datas}

    def rv(name, default=0):
        return real.get(name, {}).get("value", default)

    snapshot = {
        "fetchedAt": now_utc.isoformat(),
        "pvPower": rv("pvPower"),
        "loadsPower": rv("loadsPower"),
        "generationPower": rv("generationPower"),
        "feedinPower": rv("feedinPower"),
        "gridConsumptionPower": rv("gridConsumptionPower"),
        "batChargePower": rv("batChargePower"),
        "batDischargePower": rv("batDischargePower"),
        "SoC": rv("SoC"),
        "batTemperature": rv("batTemperature"),
        "invTemperation": rv("invTemperation"),
        "ambientTemperation": rv("ambientTemperation"),
        "runningState": real.get("runningState", {}).get("value"),
        "batStatusV2": real.get("batStatusV2", {}).get("value"),
        "SOH": rv("SOH"),
        "batCycleCount": rv("batCycleCount"),
        "generationTotal": rv("generation"),
        "gridConsumptionTotal": rv("gridConsumption"),
        "loadsTotal": rv("loads"),
        "feedinTotal": rv("feedin"),
        "chargeEnergyTotal": rv("chargeEnergyToTal"),
        "dischargeEnergyTotal": rv("dischargeEnergyToTal"),
    }

    print("Fetching today's history...")
    hist_result = client.history_query(
        sn, local_to_epoch_ms(start_of_day_local), int(now_local.timestamp() * 1000), HIST_VARS,
    )
    today_series = {}
    if hist_result and hist_result[0].get("datas"):
        for series in hist_result[0]["datas"]:
            pts = [[parse_local_time(p["time"]), p["value"]] for p in series["data"]]
            today_series[series["variable"]] = [p for p in pts if p[0] is not None]

    print("Fetching this/prior month reports...")
    y, m = now_local.year, now_local.month
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    this_month_result = client.report_query(sn, "month", y, month=m, variables=REPORT_VARS)
    prev_month_result = client.report_query(sn, "month", py, month=pm, variables=REPORT_VARS)

    print("Fetching device detail...")
    detail = client.device_detail(sn) or {}
    try:
        min_soc = client.battery_soc_get(sn) or {}
    except FoxESSError:
        min_soc = {}

    daily = {}
    daily.update(month_to_daily({"y": py, "m": pm, "result": prev_month_result}))
    daily.update(month_to_daily({"y": y, "m": m, "result": this_month_result}))

    today_str = now_local.strftime("%Y-%m-%d")
    all_dates = sorted(d for d in daily if d <= today_str)
    last30 = all_dates[-30:] if len(all_dates) >= 30 else all_dates

    print("Updating cheap/peak-rate cache...")
    cache = load_cache()
    dates_to_refresh = {today_str, (now_local - timedelta(days=1)).strftime("%Y-%m-%d")}
    # bootstrap: if the cache is missing history we need, backfill it too
    for d in last30:
        if d not in cache:
            dates_to_refresh.add(d)
    for d in sorted(dates_to_refresh):
        try:
            entry = fetch_cheap_peak_for_date(client, sn, d)
        except FoxESSError as e:
            print(f"  (skipped cheap/peak refresh for one date: {e.__class__.__name__})")
            entry = None
        if entry is not None:
            cache[d] = entry
    save_cache(cache)

    daily_rows = []
    for d in last30:
        rec = daily.get(d, {})
        gen = rec.get("generation", 0) or 0
        feedin = rec.get("feedin", 0) or 0
        gridc = rec.get("gridConsumption", 0) or 0
        loads = rec.get("loads", 0) or 0
        chg = rec.get("chargeEnergyToTal", 0) or 0
        dis = rec.get("dischargeEnergyToTal", 0) or 0
        self_consumed_solar = max(gen - feedin, 0)

        cr = cache.get(d)
        if cr and (cr["gridCheap"] + cr["gridPeak"]) > 0:
            hourly_total = cr["gridCheap"] + cr["gridPeak"]
            scale = gridc / hourly_total if hourly_total > 0 else 1
            grid_cheap = round(cr["gridCheap"] * scale, 2)
            grid_peak = round(gridc - grid_cheap, 2)
        else:
            grid_cheap = None
            grid_peak = round(gridc, 2) if gridc else 0

        daily_rows.append({
            "date": d,
            "generation": round(gen, 2),
            "feedin": round(feedin, 2),
            "gridConsumption": round(gridc, 2),
            "gridCheap": grid_cheap,
            "gridPeak": grid_peak,
            "loads": round(loads, 2),
            "chargeEnergy": round(chg, 2),
            "dischargeEnergy": round(dis, 2),
            "selfConsumedSolar": round(self_consumed_solar, 2),
        })

    out = {
        "generatedAt": now_utc.isoformat(),
        "station": {
            # station name / serial are intentionally never published
            "name": GENERIC_NAME,
            "deviceSN": "",
            "deviceType": detail.get("deviceType"),
            "inverterCapacityKw": detail.get("capacity"),
            "batteryDesignCapacityKwh": detail.get("batteryDesignCapacity"),
            "batteryModels": sorted(set(b["model"] for b in detail.get("batteryList", []) or [])),
            "minSoc": min_soc.get("minSoc"),
            "minSocOnGrid": min_soc.get("minSocOnGrid"),
        },
        "snapshot": snapshot,
        "todaySeries": today_series,
        "daily": daily_rows,
    }

    print("Rendering index.html...")
    with open(INDEX_HTML_PATH, encoding="utf-8") as f:
        html = f.read()

    new_data_script = (
        '<script id="dashboard-data" type="application/json">'
        + json.dumps(out)
        + "</script>"
    )
    # a function replacement is used (not a plain string) so any backslashes
    # inside the JSON payload are never misread as regex backreferences
    html, n_data = re.subn(
        r'<script id="dashboard-data" type="application/json">.*?</script>',
        lambda _m: new_data_script,
        html, count=1, flags=re.DOTALL,
    )
    if n_data != 1:
        print("ERROR: could not find the dashboard-data script tag to replace", file=sys.stderr)
        sys.exit(1)

    fetched_at_str = now_utc.strftime("%a %d %b %Y, %H:%M UTC")
    html, n_ts = re.subn(
        r"Snapshot fetched .*?<br>",
        f"Snapshot fetched {fetched_at_str}<br>",
        html, count=1,
    )
    if n_ts != 1:
        print("ERROR: could not find the snapshot-fetched timestamp to replace", file=sys.stderr)
        sys.exit(1)

    with open(INDEX_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote index.html ({len(html)} bytes), {len(daily_rows)} daily rows, "
          f"{len(today_series)} today-series variables.")


if __name__ == "__main__":
    main()
