"""eTimeOffice REST adapter.

Their API is undocumented publicly, so two things are deliberate here:

1. **Response classification.** The server answers with HTTP 500 in two very
   different situations, and telling them apart is the whole diagnostic story:
     * 500 with an empty, type-less body  -> rejected at the auth layer
       (wrong corporate ID / user / password, or login not API-enabled)
     * 500 with a JSON .NET exception     -> credentials were ACCEPTED and
       their controller then failed internally, which in practice means API
       access is not enabled on the subscription
   A dashboard that says "wrong password" when the password is right wastes
   days, so the tester reports these separately.

2. **A raw mode.** Because the payload schema is unpublished, `raw()` returns
   the untouched body so the shape can be inspected once a connection works.
"""
from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .base import (HUMAN, STATUS_ACCOUNT_BLOCKED, STATUS_AUTH_REJECTED,
                   STATUS_EMPTY, STATUS_ENDPOINT_MISSING, STATUS_HTTP_ERROR,
                   STATUS_NETWORK_ERROR, STATUS_OK, ProbeResult, PunchRecord,
                   SourceError)
from .normalize import normalize_rows

DEFAULT_BASE = "https://api.etimeoffice.com/api"
DEFAULT_ENDPOINT = "DownloadInOutPunchData"
# Endpoints confirmed to exist on the live server (others return 404).
KNOWN_ENDPOINTS = ("DownloadInOutPunchData", "DownloadPunchData",
                   "DownloadPunchDataMCID")


class EtimeOfficeSource:
    def __init__(self, corp_id: str, user_id: str, password: str,
                 base_url: str = DEFAULT_BASE, admin_flag: str = "true",
                 endpoint: str = DEFAULT_ENDPOINT, timeout: int = 60,
                 date_format: str = "%d/%m/%Y") -> None:
        self.corp_id = corp_id
        self.user_id = user_id
        self.password = password
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self.admin_flag = admin_flag
        self.endpoint = endpoint or DEFAULT_ENDPOINT
        self.timeout = timeout
        self.date_format = date_format

    # ------------------------------------------------------------------ wire
    def _auth(self) -> str:
        """Their Basic username is a composite, not a plain user name."""
        parts = [self.corp_id, self.user_id, self.password]
        if self.admin_flag:
            parts.append(self.admin_flag)
        token = base64.b64encode(":".join(parts).encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _url(self, endpoint: str, params: dict[str, str]) -> str:
        # Their date values contain '/', which must survive urlencoding.
        query = urllib.parse.urlencode(params, safe="/:_")
        return f"{self.base_url}/{endpoint}?{query}"

    def _call(self, endpoint: str, params: dict[str, str]) -> tuple[int, str, bytes]:
        req = urllib.request.Request(
            self._url(endpoint, params),
            headers={"Authorization": self._auth(),
                     "Accept": "application/json",
                     "User-Agent": "attendance-sync/1.0"},
        )
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", body
        except urllib.error.URLError as exc:
            raise SourceError(f"could not reach {self.base_url}: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise SourceError(f"could not reach {self.base_url}: {exc}") from exc

    # ------------------------------------------------------------- diagnosis
    @staticmethod
    def classify(status: int, content_type: str, body: bytes) -> tuple[str, str]:
        text = body.decode("utf-8", "replace").strip()
        is_json = "json" in (content_type or "").lower()

        if status == 404:
            return STATUS_ENDPOINT_MISSING, HUMAN[STATUS_ENDPOINT_MISSING]
        if status == 401 or status == 403:
            return STATUS_AUTH_REJECTED, HUMAN[STATUS_AUTH_REJECTED]
        if status == 200:
            return STATUS_OK, HUMAN[STATUS_OK]
        if status >= 500:
            if not text:
                # Empty, type-less 500 = their auth layer refused us.
                return STATUS_AUTH_REJECTED, HUMAN[STATUS_AUTH_REJECTED]
            if is_json and ("ExceptionMessage" in text or "StackTrace" in text):
                detail = HUMAN[STATUS_ACCOUNT_BLOCKED]
                try:
                    msg = json.loads(text).get("ExceptionMessage", "")
                    if msg:
                        detail += f" (their error: {msg})"
                except Exception:
                    pass
                return STATUS_ACCOUNT_BLOCKED, detail
        return STATUS_HTTP_ERROR, f"{HUMAN[STATUS_HTTP_ERROR]} HTTP {status}."

    def probe(self, from_date, to_date) -> ProbeResult:
        """Test the connection and say what is actually wrong."""
        params = {"Empcode": "ALL",
                  "FromDate": from_date.strftime(self.date_format),
                  "ToDate": to_date.strftime(self.date_format)}
        try:
            status, ctype, body = self._call(self.endpoint, params)
        except SourceError as exc:
            return ProbeResult(False, STATUS_NETWORK_ERROR, str(exc))

        code, detail = self.classify(status, ctype, body)
        sample = body[:600].decode("utf-8", "replace")
        if code != STATUS_OK:
            return ProbeResult(False, code, detail, sample)

        try:
            rows = list(normalize_rows(json.loads(body.decode("utf-8", "replace"))))
        except Exception as exc:
            return ProbeResult(False, STATUS_HTTP_ERROR,
                               f"Connected, but the response could not be parsed: {exc}",
                               sample)
        if not rows:
            return ProbeResult(True, STATUS_EMPTY, HUMAN[STATUS_EMPTY], sample)
        return ProbeResult(True, STATUS_OK,
                           f"Connected - {len(rows)} punch record(s) received.", sample)

    # ----------------------------------------------------------------- fetch
    def raw(self, from_date, to_date) -> str:
        params = {"Empcode": "ALL",
                  "FromDate": from_date.strftime(self.date_format),
                  "ToDate": to_date.strftime(self.date_format)}
        _status, _ctype, body = self._call(self.endpoint, params)
        return body.decode("utf-8", "replace")

    def fetch(self, from_date, to_date) -> list[PunchRecord]:
        params = {"Empcode": "ALL",
                  "FromDate": from_date.strftime(self.date_format),
                  "ToDate": to_date.strftime(self.date_format)}
        status, ctype, body = self._call(self.endpoint, params)
        code, detail = self.classify(status, ctype, body)
        if code not in (STATUS_OK,):
            raise SourceError(detail)
        payload: Any = json.loads(body.decode("utf-8", "replace"))
        return list(normalize_rows(payload))
