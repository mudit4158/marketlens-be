"""Upstox OAuth2 token management.

Upstox uses a daily token model: access tokens expire at ~3:30 AM IST each day.
This module handles:
  - Building the authorization URL to redirect the user to
  - Exchanging the auth code for an access token after callback
  - Retrieving the stored token for API calls
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.oauth_token import OAuthToken

logger = logging.getLogger(__name__)

PROVIDER = "upstox"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"


def get_authorization_url() -> str:
    settings = get_settings()
    params = (
        f"?response_type=code"
        f"&client_id={settings.upstox_api_key}"
        f"&redirect_uri={settings.upstox_redirect_uri}"
    )
    return AUTH_URL + params


def exchange_code_for_token(code: str, db: Session) -> OAuthToken:
    """Exchange an authorization code for an access token and persist it."""
    settings = get_settings()

    resp = httpx.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data={
            "code": code,
            "client_id": settings.upstox_api_key,
            "client_secret": settings.upstox_api_secret,
            "redirect_uri": settings.upstox_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    return _persist_token(data["access_token"], db)


def get_valid_token(db: Session) -> str | None:
    """Return the stored access token if it hasn't expired, else None."""
    token = db.query(OAuthToken).filter_by(provider=PROVIDER).one_or_none()
    if token is None:
        return None
    now = datetime.now(timezone.utc)
    if token.expires_at.replace(tzinfo=timezone.utc) <= now:
        return None
    return token.access_token


def auto_refresh_token(db: Session) -> bool:
    """Headless Upstox token refresh using stored credentials + TOTP.

    Uses the upstox-totp library which handles Upstox's current 6-step login
    flow (OTP generate → TOTP verify → PIN submit → OAuth approve → token
    exchange) with curl_cffi Chrome TLS impersonation required by Upstox.

    Returns True on success, False if credentials are missing or login fails.
    """
    settings = get_settings()

    if not all([settings.upstox_mobile, settings.upstox_pin, settings.upstox_totp_secret,
                settings.upstox_api_key, settings.upstox_api_secret]):
        logger.warning("Upstox auto-refresh skipped: credentials not fully configured.")
        return False

    try:
        from pydantic import SecretStr
        from upstox_totp import UpstoxTOTP

        with UpstoxTOTP(
            username=settings.upstox_mobile,
            password=SecretStr(settings.upstox_pin),
            pin_code=SecretStr(settings.upstox_pin),
            totp_secret=SecretStr(settings.upstox_totp_secret),
            client_id=settings.upstox_api_key,
            client_secret=SecretStr(settings.upstox_api_secret),
            redirect_uri=settings.upstox_redirect_uri,
        ) as client:
            token_response = client.app_token.get_access_token()

        if not token_response.success or token_response.data is None:
            logger.error("Upstox auto-refresh: token response unsuccessful: %s", token_response.error)
            return False

        access_token = token_response.data.access_token
        _persist_token(access_token, db)
        logger.info("Upstox auto-refresh: token refreshed successfully.")
        return True

    except Exception:
        logger.exception("Upstox auto-refresh failed.")
        return False


def _persist_token(access_token: str, db: Session) -> OAuthToken:
    now_utc = datetime.now(timezone.utc)
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = now_utc + ist_offset
    expire_ist = now_ist.replace(hour=3, minute=0, second=0, microsecond=0) + timedelta(days=1)
    expires_at_utc = expire_ist - ist_offset

    token = db.query(OAuthToken).filter_by(provider=PROVIDER).one_or_none()
    if token is None:
        token = OAuthToken(provider=PROVIDER, access_token=access_token,
                           expires_at=expires_at_utc, created_at=now_utc)
        db.add(token)
    else:
        token.access_token = access_token
        token.expires_at = expires_at_utc
        token.created_at = now_utc

    db.commit()
    db.refresh(token)
    return token
