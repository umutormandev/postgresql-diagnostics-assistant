"""Local snapshots and short-lived demo credentials."""

import json
import os
from pathlib import Path
import time

import jwt


SNAPSHOTS = Path(__file__).with_name("controlled_snapshots.json")
CASES = Path(__file__).with_name("controlled_cases.jsonl")
ROLES = ("operator", "viewer")


def load_snapshots() -> dict:
    return json.loads(SNAPSHOTS.read_text(encoding="utf-8"))


def signed_test_token(role: str, secret: str, minutes: int = 30) -> str:
    now = int(time.time())
    return jwt.encode({
        "sub": f"evaluation-{role}", "role": role, "iat": now, "exp": now + minutes * 60,
        "iss": os.getenv("DIAGNOSTICS_AUTH_ISSUER", "postgresql-diagnostics-auth"),
        "aud": os.getenv("DIAGNOSTICS_AUTH_AUDIENCE", "postgresql-diagnostics-api"),
    }, secret, algorithm="HS256")
