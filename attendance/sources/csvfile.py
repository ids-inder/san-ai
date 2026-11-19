"""CSV adapter: reads an export saved from the eTimeOffice portal.

This is the path that works with no API access at all, and it is also how a
new deployment gets backfilled. Real exports are messy - title banners above
the header, a stray delimiter, a BOM - so the header row is discovered rather
than assumed.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

from .base import (HUMAN, STATUS_EMPTY, STATUS_HTTP_ERROR, STATUS_OK,
                   ProbeResult, PunchRecord, SourceError)
from .normalize import EMPCODE_KEYS, _key, normalize_rows


class CsvSource:
    def __init__(self, path: str | Path, device_default: str = "",
                 encoding: str = "utf-8-sig") -> None:
        self.path = Path(path)
        self.device_default = device_default
        self.encoding = encoding

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            raise SourceError(f"file not found: {self.path}")
        try:
            text = self.path.read_text(encoding=self.encoding, errors="replace")
        except OSError as exc:
            raise SourceError(f"could not read {self.path}: {exc}") from exc
        if not text.strip():
            return []

        lines = text.splitlines()
        try:
            dialect = csv.Sniffer().sniff(
                "\n".join(lines[:20]), delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","

        # Find the header: the first line carrying a recognisable employee-code
        # column. Portal exports often put a report title above it.
        start = 0
        for idx, line in enumerate(lines[:40]):
            cells = {_key(c) for c in next(csv.reader([line], delimiter=delimiter), [])}
            if cells & set(EMPCODE_KEYS) or any(
                    "empcode" in c or "paycode" in c for c in cells):
                start = idx
                break
        reader = csv.DictReader(io.StringIO("\n".join(lines[start:])),
                                delimiter=delimiter)
        return [r for r in reader if any((v or "").strip() for v in r.values())]

    def fetch(self, from_date: dt.date, to_date: dt.date) -> list[PunchRecord]:
        records = list(normalize_rows(self._rows(),
                                      device_default=self.device_default))
        lo, hi = from_date.isoformat(), to_date.isoformat()
        # Keep the day after `to_date` too: an overnight out-punch belongs to
        # the last requested work date.
        hi_incl = (to_date + dt.timedelta(days=1)).isoformat()
        return [r for r in records if lo <= r.punch_at[:10] <= hi_incl]

    def probe(self, from_date: dt.date, to_date: dt.date) -> ProbeResult:
        try:
            rows = self._rows()
        except SourceError as exc:
            return ProbeResult(False, STATUS_HTTP_ERROR, str(exc))
        records = list(normalize_rows(rows, device_default=self.device_default))
        sample = ", ".join(sorted(rows[0].keys())[:12]) if rows else ""
        if not records:
            return ProbeResult(
                False, STATUS_HTTP_ERROR,
                f"Read {len(rows)} row(s) but found no usable punches. "
                f"Columns seen: {sample or 'none'}", sample)
        return ProbeResult(True, STATUS_OK,
                           f"Read {len(records)} punch record(s) from {self.path.name}.",
                           sample)
