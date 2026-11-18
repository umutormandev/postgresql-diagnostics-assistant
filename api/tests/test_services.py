from app import services
from app.catalog import CATALOG
from app.sql_guard import SAFE_SQL


def test_model_adapter_uses_configured_token_and_model(monkeypatch):
    monkeypatch.delenv("DIAGNOSTICS_MODEL_PROVIDER", raising=False)
    observed = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": SAFE_SQL}}]}

    class FakeClient:
        def __init__(self, timeout):
            assert timeout == 15.0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, headers, json):
            observed.update(url=url, headers=headers, body=json)
            return FakeResponse()

    monkeypatch.setenv("HF_TOKEN", "test-hf-token")
    monkeypatch.setenv("HF_MODEL", "test/model")
    monkeypatch.setattr(services.httpx, "Client", FakeClient)
    assert services.generate_candidate_sql("locks?") == SAFE_SQL
    assert observed["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert observed["headers"]["Authorization"] == "Bearer test-hf-token"
    assert observed["body"]["model"] == "test/model"
    assert observed["body"]["messages"][-1]["content"] == "locks?"


def test_ollama_adapter_uses_local_model_and_240_second_response_timeout(monkeypatch):
    observed = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": SAFE_SQL}}

    class FakeClient:
        def __init__(self, timeout, trust_env):
            assert timeout.read == 240.0
            assert timeout.connect == 10.0
            assert trust_env is False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, json):
            observed.update(url=url, body=json)
            return FakeResponse()

    monkeypatch.setenv("DIAGNOSTICS_MODEL_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(services.httpx, "Client", FakeClient)
    assert services.generate_method_sql("Kilit bekleyen oturumlar?", "b2", CATALOG["lock_waits"]) == SAFE_SQL
    assert observed["url"] == "http://127.0.0.1:11434/api/chat"
    assert observed["body"]["model"] == "qwen2.5-coder:3b"
    assert observed["body"]["stream"] is False
    assert observed["body"]["options"] == {"temperature": 0, "num_predict": 128}


def test_database_adapter_sets_read_only_transaction_and_timeout(monkeypatch):
    events = []

    class FakeResult:
        def fetchall(self):
            return []

    class FakeTransaction:
        def __enter__(self):
            events.append("transaction started")

        def __exit__(self, *_):
            events.append("transaction ended")

    class FakeConnection:
        read_only = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def transaction(self):
            assert self.read_only is True
            return FakeTransaction()

        def execute(self, sql, params=None):
            events.append((sql, params))
            return FakeResult()

    def connect(url, **kwargs):
        assert url == "postgresql://reader:secret@localhost/diagnostics"
        assert kwargs["autocommit"] is True
        assert kwargs["connect_timeout"] == 5
        return FakeConnection()

    monkeypatch.setenv(
        "DIAGNOSTICS_DATABASE_URL", "postgresql://reader:secret@localhost/diagnostics"
    )
    monkeypatch.setattr(services.psycopg, "connect", connect)
    assert services.fetch_lock_waits(SAFE_SQL) == []
    assert events == [
        "transaction started",
        ("SELECT set_config('statement_timeout', %s, true)", ("3000ms",)),
        (SAFE_SQL, None),
        "transaction ended",
    ]
