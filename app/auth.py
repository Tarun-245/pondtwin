"""Verified Supabase authentication for private API endpoints."""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import Store

bearer = HTTPBearer(auto_error=False)


def require_store(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Sign in to access your ponds.", headers={"WWW-Authenticate": "Bearer"})
    store = Store(credentials.credentials)
    try:
        yield store
    finally:
        store.close()
