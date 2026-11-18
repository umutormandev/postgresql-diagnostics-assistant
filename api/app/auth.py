"""Validate the caller's signed identity before any diagnostic work."""

import os

import jwt
from fastapi import HTTPException, Request


def require_role(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer token required")

    secret = os.getenv("DIAGNOSTICS_AUTH_SECRET")
    if not secret or len(secret) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=os.getenv("DIAGNOSTICS_AUTH_AUDIENCE", "postgresql-diagnostics-api"),
            issuer=os.getenv("DIAGNOSTICS_AUTH_ISSUER", "postgresql-diagnostics-auth"),
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc

    role = claims.get("role")
    if role not in ("operator", "viewer"):
        raise HTTPException(status_code=403, detail="forbidden")
    return role
