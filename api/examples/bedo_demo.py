"""Controlled, self-contained Bedo API example; no HF or PostgreSQL calls."""

import json
import os
import time

import jwt
from fastapi.testclient import TestClient

from app import main
from app.catalog import CATALOG


def run() -> None:
    secret = "local-demo-secret-with-at-least-32-characters"
    os.environ["DIAGNOSTICS_AUTH_SECRET"] = secret
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "bedo-demo", "role": "operator", "iss": "postgresql-diagnostics-auth",
            "aud": "postgresql-diagnostics-api", "iat": now, "exp": now + 300,
        },
        secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}

    main.generate_candidate_sql = lambda question: CATALOG["lock_waits"].safe_sql
    main.generate_method_sql = (
        lambda question, method, schema, metric=None: schema.safe_sql
    )
    main.fetch_lock_waits = lambda sql: [
        {"waiting_pid": 7426, "blocking_pid": 7412, "wait_seconds": 18}
    ]

    with TestClient(main.app) as client:
        for method in ("b2", "b3", "a1", "proposed"):
            response = client.post(
                "/diagnose",
                json={"question": "Hangi oturum hangisini engelliyor?", "method": method},
                headers=headers,
            )
            print(method, response.status_code, json.dumps(response.json(), ensure_ascii=False))
        for metric in (None, "total", "mean"):
            payload = {"question": "En pahalı sorgu hangisi?"}
            if metric:
                payload["metric"] = metric
            response = client.post("/diagnose", json=payload, headers=headers)
            print(metric or "clarify", response.status_code,
                  json.dumps(response.json(), ensure_ascii=False))


if __name__ == "__main__":
    run()
