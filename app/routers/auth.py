from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import upstox_auth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/upstox/login")
def upstox_login():
    """Redirect the browser to Upstox's OAuth2 authorization page."""
    url = upstox_auth.get_authorization_url()
    return RedirectResponse(url=url)


@router.get("/upstox/callback")
def upstox_callback(code: str, db: Session = Depends(get_db)):
    """
    Upstox redirects here after the user authorizes.
    Exchanges the authorization code for an access token and stores it.
    """
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")
    try:
        token = upstox_auth.exchange_code_for_token(code, db)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}") from exc

    return {
        "message": "Upstox connected successfully.",
        "expires_at": token.expires_at.isoformat(),
    }


@router.get("/upstox/status")
def upstox_status(db: Session = Depends(get_db)):
    """Check whether a valid Upstox token is stored."""
    token = upstox_auth.get_valid_token(db)
    if token:
        return {"connected": True}
    return {"connected": False, "action": "Visit /auth/upstox/login to connect."}
