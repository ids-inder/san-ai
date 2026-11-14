"""FYERS login helper (API v3).

Two steps, kept dependency-light:

  1. `auth_url()` builds the login URL from your app id + redirect uri using only
     the standard library. You open it, log in, and FYERS redirects to your
     redirect uri with ``?auth_code=...``.

  2. `exchange_code()` swaps that auth_code for an access token. This step uses
     the FYERS SDK (imported lazily) because the token exchange signs
     ``client_id:secret_key`` with SHA-256 and posts it - we defer to the SDK to
     stay correct with their protocol.

The resulting access token goes in FYERS_ACCESS_TOKEN (env or .env). Tokens are
short-lived (typically a trading day), so re-auth daily for live use.
"""

from __future__ import annotations

from urllib.parse import urlencode

from .config import Settings

AUTH_BASE = "https://api-t1.fyers.in/api/v3/generate-authcode"


def auth_url(settings: Settings, state: str = "sanarb") -> str:
    if not (settings.fyers_client_id and settings.fyers_redirect_uri):
        raise RuntimeError("Set FYERS_CLIENT_ID and FYERS_REDIRECT_URI first.")
    params = {
        "client_id": settings.fyers_client_id,
        "redirect_uri": settings.fyers_redirect_uri,
        "response_type": "code",
        "state": state,
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


def exchange_code(settings: Settings, auth_code: str) -> str:
    """Return an access token for the given auth_code (raises on failure)."""
    if not (settings.fyers_client_id and settings.fyers_secret_key
            and settings.fyers_redirect_uri):
        raise RuntimeError("Set FYERS_CLIENT_ID, FYERS_SECRET_KEY, FYERS_REDIRECT_URI.")
    try:
        from fyers_apiv3 import fyersModel  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("fyers-apiv3 not installed; `pip install fyers-apiv3`.") from exc

    session = fyersModel.SessionModel(
        client_id=settings.fyers_client_id,
        secret_key=settings.fyers_secret_key,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    resp = session.generate_token()
    if isinstance(resp, dict) and resp.get("access_token"):
        return resp["access_token"]
    raise RuntimeError(f"Token exchange failed: {resp}")
