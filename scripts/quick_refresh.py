"""
Lightweight, frequent companion to refresh.py.

Pulls only the live real-time snapshot (battery %, charge/discharge power,
solar, house load) from the FoxESS Cloud OpenAPI and patches it into the
"snapshot" section of index.html's embedded data — leaving the historical
charts (today's series, last-30-days) untouched. This is what keeps the
Echo Show "quick view" (status.html) and the main dashboard's "RIGHT NOW"
tiles feeling close to real-time, while the heavier full rebuild
(refresh.py) still only runs hourly.

Required environment variables (same secrets refresh.py uses):
  FOXESS_API_KEY
  FOXESS_DEVICE_SN
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from foxess_client import FoxESSClient  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML_PATH = os.path.join(REPO_ROOT, "index.html")


def main():
    api_key = os.environ["FOXESS_API_KEY"]
    sn = os.environ["FOXESS_DEVICE_SN"]
    client = FoxESSClient(api_key)
    now_utc = datetime.now(timezone.utc)

    real_result = client.real_query([sn])
    real_datas = real_result[0]["datas"] if real_result and real_result[0].get("datas") else []
    real = {d["variable"]: d for d in real_datas}

    def rv(name, default=0):
        return real.get(name, {}).get("value", default)

    with open(INDEX_HTML_PATH, encoding="utf-8") as f:
        html = f.read()

    m = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        print("ERROR: dashboard-data block not found", file=sys.stderr)
        sys.exit(1)
    data = json.loads(m.group(1))

    snapshot = data.get("snapshot", {})
    snapshot.update({
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
    })
    data["snapshot"] = snapshot

    new_data_script = (
        '<script id="dashboard-data" type="application/json">'
        + json.dumps(data)
        + "</script>"
    )
    html, n_data = re.subn(
        r'<script id="dashboard-data" type="application/json">.*?</script>',
        lambda _m: new_data_script,
        html, count=1, flags=re.DOTALL,
    )
    if n_data != 1:
        print("ERROR: could not replace the dashboard-data block", file=sys.stderr)
        sys.exit(1)

    fetched_at_str = now_utc.strftime("%a %d %b %Y, %H:%M UTC")
    html, n_ts = re.subn(
        r"Snapshot fetched .*?<br>",
        f"Snapshot fetched {fetched_at_str}<br>",
        html, count=1,
    )
    if n_ts != 1:
        print("ERROR: could not replace the snapshot timestamp", file=sys.stderr)
        sys.exit(1)

    with open(INDEX_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(
        f"Quick refresh: SoC={snapshot.get('SoC')} pv={snapshot.get('pvPower')} "
        f"load={snapshot.get('loadsPower')} battChg={snapshot.get('batChargePower')} "
        f"battDis={snapshot.get('batDischargePower')}"
    )


if __name__ == "__main__":
    main()
