"""Shared types for punch-data sources."""
from __future__ import annotations

from typing import NamedTuple


class SourceError(RuntimeError):
    """Upstream could not be read. Message is safe to show an operator."""


class PunchRecord(NamedTuple):
    empcode: str
    name: str
    punch_at: str        # 'YYYY-MM-DD HH:MM:SS', clinic-local
    direction: str       # 'in' | 'out' | ''
    device: str = ""


class ProbeResult(NamedTuple):
    """Outcome of testing a connection, in terms an operator can act on."""
    ok: bool
    status: str          # see STATUS_* below
    detail: str
    sample: str = ""


STATUS_OK = "ok"
STATUS_AUTH_REJECTED = "auth_rejected"
STATUS_ACCOUNT_BLOCKED = "account_blocked"
STATUS_ENDPOINT_MISSING = "endpoint_missing"
STATUS_HTTP_ERROR = "http_error"
STATUS_NETWORK_ERROR = "network_error"
STATUS_EMPTY = "ok_no_data"

HUMAN = {
    STATUS_OK: "Connected - punch data received.",
    STATUS_EMPTY: "Connected and authenticated, but no punches in that date range.",
    STATUS_AUTH_REJECTED: (
        "Rejected at login. The corporate ID, user ID or password is wrong, "
        "or this login is not permitted to use the API."
    ),
    STATUS_ACCOUNT_BLOCKED: (
        "Credentials were accepted but the vendor's API failed internally. "
        "This usually means API access is not enabled on the subscription. "
        "Ask eTimeOffice support to enable it for this corporate ID."
    ),
    STATUS_ENDPOINT_MISSING: "That endpoint does not exist on this server.",
    STATUS_HTTP_ERROR: "The server returned an unexpected response.",
    STATUS_NETWORK_ERROR: "Could not reach the server.",
}
