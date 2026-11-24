import os
from functools import lru_cache

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from fastapi import HTTPException, Request


@lru_cache(maxsize=1)
def admin_user_ids():
    return frozenset(value.strip() for value in os.getenv("ADMIN_CLERK_USER_IDS", "").split(",") if value.strip())


def require_user(request: Request) -> str:
    secret_key = os.getenv("CLERK_SECRET_KEY", "")
    authorized_parties = [value.strip() for value in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",") if value.strip()]
    if not secret_key or not authorized_parties:
        raise HTTPException(503, "Clerk authentication is not configured")
    try:
        state = authenticate_request(
            request,
            AuthenticateRequestOptions(
                secret_key=secret_key,
                jwt_key=os.getenv("CLERK_JWT_KEY") or None,
                authorized_parties=authorized_parties,
                accepts_token=["session_token"],
            ),
        )
    except Exception:
        raise HTTPException(401, "Invalid or expired session") from None
    if not state.is_signed_in or not state.payload.get("sub"):
        raise HTTPException(401, "Authentication required")
    return state.payload["sub"]


def is_admin(user_id: str) -> bool:
    return user_id in admin_user_ids()
