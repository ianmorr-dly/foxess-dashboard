"""
Minimal FoxESS Cloud OpenAPI client.

Auth scheme (per FoxESS OpenAPI docs):
  headers:
    Token: <api key>
    Timestamp: <ms since epoch>
    Signature: md5(f"{path}\r\n{token}\r\n{timestamp}")
    Lang: en
    Content-Type: application/json
"""
import hashlib
import time
import requests

BASE_URL = "https://www.foxesscloud.com"
TIMEZONE = "Europe/London"


class FoxESSError(RuntimeError):
    pass


class FoxESSClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    def _headers(self, path: str) -> dict:
        timestamp = str(round(time.time() * 1000))
        signature = hashlib.md5(
            f"{path}\r\n{self.api_key}\r\n{timestamp}".encode("UTF-8")
        ).hexdigest()
        return {
            "Token": self.api_key,
            "Timestamp": timestamp,
            "Signature": signature,
            "Lang": "en",
            "Timezone": TIMEZONE,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (dashboard-refresh-script)",
        }

    def _post(self, path: str, body: dict) -> dict:
        r = self.session.post(BASE_URL + path, headers=self._headers(path), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("errno", 0) != 0:
            raise FoxESSError(f"{path} -> errno {data.get('errno')}: {data.get('msg')}")
        return data.get("result")

    def _get(self, path: str, params: dict) -> dict:
        r = self.session.get(BASE_URL + path, headers=self._headers(path), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("errno", 0) != 0:
            raise FoxESSError(f"{path} -> errno {data.get('errno')}: {data.get('msg')}")
        return data.get("result")

    def device_detail(self, sn: str):
        return self._get("/op/v0/device/detail", {"sn": sn})

    def real_query(self, sns, variables=None):
        body = {"sns": sns if isinstance(sns, list) else [sns]}
        if variables:
            body["variables"] = variables
        return self._post("/op/v1/device/real/query", body)

    def history_query(self, sn: str, begin_ms: int, end_ms: int, variables=None):
        body = {"sn": sn, "begin": begin_ms, "end": end_ms}
        if variables:
            body["variables"] = variables
        return self._post("/op/v0/device/history/query", body)

    def report_query(self, sn: str, dimension: str, year: int, month: int = None, day: int = None, variables=None):
        body = {
            "sn": sn,
            "dimension": dimension,
            "year": year,
            "variables": variables or [],
        }
        if month is not None:
            body["month"] = month
        if day is not None:
            body["day"] = day
        return self._post("/op/v0/device/report/query", body)

    def battery_soc_get(self, sn: str):
        return self._get("/op/v0/device/battery/soc/get", {"sn": sn})
