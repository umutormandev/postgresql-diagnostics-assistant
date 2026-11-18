"""Serve the real API on localhost with clearly labelled B1 demo data."""

import argparse
import os
import secrets

import uvicorn

from api.app.main import app
from .fixtures import ROLES, load_snapshots, signed_test_token


def main() -> None:
    snapshots = load_snapshots()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=snapshots, default="D1-W1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    secret = secrets.token_hex(32)
    os.environ["DIAGNOSTICS_AUTH_SECRET"] = secret
    app.state.controlled_fixture = snapshots[args.condition]
    print(f"Sentetik demo {args.condition} · arayüzden B1 yöntemini seç.", flush=True)
    for role in ROLES:
        print(f"{role}: {signed_test_token(role, secret, minutes=60)}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
