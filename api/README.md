# Lock diagnostics API

`POST /diagnose` accepts `{"question":"Hangi oturum hangisini engelliyor?"}`. A valid,
server-signed operator bearer token is required. Viewer requests for lock or active-session details return HTTP 403.
Both roles can request query statistics. The API derives the role from the
verified JWT before making model or database calls.

Optional request fields are `method` (`proposed`, `b1`, `b2`, `b3`, or `a1`; default
`proposed`) and `metric` (`total` or `mean` for expensive-query questions).

The response contract is:

```json
{"status":"ok","intent":"lock_waits","rows":[{"waiting_pid":7426,"blocking_pid":7412,"wait_seconds":18}],"source":"pg_blocking_pids + pg_locks"}
```

When there are no waits, `rows` is `[]`. Model failures, unsafe SQL, and database
failures return an HTTP error; no partial data is returned.

## Schema catalog and methods

The catalog in `app/catalog.py` assigns every view its columns, source, and
permitted role. The operator can see all three schemas. The viewer sees the
statistics schema. Turkish question
terms select `lock_waits`, `long_queries`, or `query_stats`; an unsupported
question returns HTTP 422. SQLGlot validates one `SELECT` over the selected
view. Explicit catalog columns are accepted; a lone `SELECT *` over exactly
that view is expanded to the fixed catalog projection, including when the
model wraps it in a single SQL code fence. Extra clauses, tables, statements,
or expressions are rejected. The raw model SQL is never sent to PostgreSQL.

| Method | Prompt schema | Examples | Execution |
| --- | --- | --- | --- |
| `b1` | Predefined SQL for the selected view | None | Selected view only |
| `b2` | Fixed catalog of role-permitted views | None (zero-shot) | Selected view only |
| `b3` | Same fixed catalog | Three question/SQL examples (few-shot) | Selected view only |
| `a1` | Same fixed catalog | Same three development examples as proposed | Selected view only |
| `proposed` | Only the question-selected view | Same three development examples as A1 | Selected view only |

All model-based methods use the same selected provider and generation settings.
Hugging Face is the default (`HF_TOKEN`, `HF_MODEL`, 15-second HTTP timeout,
`temperature=0`, `max_tokens=128`). Set
`DIAGNOSTICS_MODEL_PROVIDER=ollama` to use local Ollama instead; the defaults
are `OLLAMA_MODEL=qwen2.5-coder:3b` and
`OLLAMA_BASE_URL=http://127.0.0.1:11434`. Ollama uses non-streaming
`POST /api/chat`, `temperature=0`, `num_predict=128`, and a **240-second read
timeout for the first response**. Proposed and A1 use the same prompt
builder for every intent, including locks; only the schema context changes.
The lock view is live PostgreSQL. The
`diagnostics.long_queries(pid, duration_seconds, query_text)` and
`diagnostics.query_stats(queryid, total_exec_time_ms, mean_exec_time_ms, calls)`
catalog entries are **provisional**. In normal API mode, after any needed metric
clarification, either intent returns HTTP 503 with
`{"status":"unavailable","intent":"long_queries","rows":[],"source":"unavailable","detail":"Live diagnostic view is not available"}`
(`intent` is `query_stats` for statistics). No model or PostgreSQL call occurs.
Controlled fixture rows carry `source: "controlled_fixture"`. They are available
through the [local B1 demo](../evaluation/README.md) or by setting
`DIAGNOSTICS_DEMO_MODE=1` for the two provisional views. In demo mode the
statistical rows represent a 15-minute window. Queries that name a different
window return `status: "insufficient_data"`. Live lock requests include database
call time in the `Server-Timing` response header.

`{"question":"En pahalı sorgu hangisi?"}` first returns
`{"status":"clarification_required","intent":"query_stats","rows":[],"source":"schema_catalog","clarification":{"question":"Toplam çalışma süresi mi, çağrı başına ortalama süre mi?","options":["total","mean"]}}`.
Repeat the request with `"metric":"total"` or `"metric":"mean"`. Questions
that explicitly say “toplam süre” or “çağrı başına ortalama” use that metric
without a follow-up. A conflicting `metric` field returns HTTP 422. In demo
mode, the controlled fixture ranks different query IDs for the two metrics.

## Setup (PowerShell, Python 3.11+)

From the repository root:

```powershell
cd api
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Use any Python 3.11+ interpreter if `py -3.12` is unavailable. Set the following
environment variables in the API process:

```powershell
$env:DIAGNOSTICS_AUTH_SECRET = "<random secret of at least 32 characters>"
$env:DIAGNOSTICS_AUTH_ISSUER = "postgresql-diagnostics-auth"
$env:DIAGNOSTICS_AUTH_AUDIENCE = "postgresql-diagnostics-api"
$env:HF_TOKEN = "<Hugging Face inference token>"
$env:HF_MODEL = "<chat-completion-capable model ID>"
$env:DIAGNOSTICS_DATABASE_URL = "postgresql://lock_diagnostics_reader:<password>@<host>:5432/<database>"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For an Ollama-backed API process, set these before starting `uvicorn` instead
of configuring `HF_TOKEN` and `HF_MODEL`:

```powershell
$env:DIAGNOSTICS_MODEL_PROVIDER = "ollama"
$env:OLLAMA_MODEL = "qwen2.5-coder:3b"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
```

If `DIAGNOSTICS_MODEL_PROVIDER` is unset, Hugging Face remains the provider.

The JWT issuer must sign HS256 tokens with the same secret. Each token needs `sub`,
`role`, `iss`, `aud`, `iat`, and `exp` claims. Supported roles are `operator` and
`viewer`; any other role is forbidden. The default issuer and audience are shown
above. Keep the secret and database credentials on the server. For a local smoke
test only, this command makes a short-lived operator token using the configured
secret:

```powershell
$token = python -c "import os,time,jwt; n=int(time.time()); print(jwt.encode({'sub':'local-test','role':'operator','iss':os.getenv('DIAGNOSTICS_AUTH_ISSUER','postgresql-diagnostics-auth'),'aud':os.getenv('DIAGNOSTICS_AUTH_AUDIENCE','postgresql-diagnostics-api'),'iat':n,'exp':n+300},os.environ['DIAGNOSTICS_AUTH_SECRET'],algorithm='HS256'))"
```

Then send `POST http://127.0.0.1:8000/diagnose` with `Authorization: Bearer <token>`
and a JSON body containing `question`.

Bedo can invoke every online method with the same authenticated endpoint:

```powershell
$headers = @{ Authorization = "Bearer $token" }
foreach ($method in @('b2', 'b3', 'a1', 'proposed')) {
  $body = @{ question = 'Hangi oturum hangisini engelliyor?'; method = $method } | ConvertTo-Json
  Invoke-RestMethod -Uri 'http://127.0.0.1:8000/diagnose' -Method Post -Headers $headers -ContentType 'application/json' -Body $body
}
$body = @{ question = 'En pahalı sorgu hangisi?' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/diagnose' -Method Post -Headers $headers -ContentType 'application/json' -Body $body
$body = @{ question = 'En pahalı sorgu hangisi?'; metric = 'mean'; method = 'proposed' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/diagnose' -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

The ambiguous cost request above first returns the clarification in any mode.
After a metric is chosen, normal mode returns the documented HTTP 503 until
Yusuf's statistics view is ready. Run the controlled demo command below to see
metric results now.

For an immediately runnable example without HF or PostgreSQL credentials, use
`python -m examples.bedo_demo`. It sends the same requests through FastAPI's
test client with controlled model output and a controlled lock row. Successful
responses contain `status`, `intent`, `rows`, and `source`; ambiguous cost
questions also contain `clarification`.

**A2 offline replay:** Put recorded candidate SQL from any method in JSONL
records with `candidate_id`, `method`, `role`, `question`, `sql`, and optional
`metric`. The role in this offline file is evaluation input, never an API
authentication claim. Run:

```powershell
python -m app.offline_validator examples/recorded_candidates.jsonl
```

Each output JSONL record contains `candidate_id`, `method`, `accepted`, and
`intent` when known. Accepted records include `normalized_sql`; rejected
records include `reason`. The example replays identical saved lock SQL for B2,
B3, A1, and proposed, and an unsafe candidate. A2 makes no model or database
call.

## Database contract

Yusuf's view contract is not yet present in this repository. Until it arrives,
run [`sql/001_lock_waits.sql`](sql/001_lock_waits.sql) as a trusted view owner
on PostgreSQL 14 or newer (`pg_locks.waitstart` is required).
It creates `diagnostics.lock_waits(waiting_pid integer, blocking_pid integer,
wait_seconds integer)` using `pg_blocking_pids`, `pg_locks`, and
`pg_stat_activity`. A DBA should provision a login role that can only connect
and read this view, for example:

```sql
CREATE ROLE lock_diagnostics_reader LOGIN PASSWORD '<generated password>';
GRANT CONNECT ON DATABASE your_database TO lock_diagnostics_reader;
GRANT USAGE ON SCHEMA diagnostics TO lock_diagnostics_reader;
GRANT SELECT ON diagnostics.lock_waits TO lock_diagnostics_reader;
```

Do not grant base table access or write privileges to that role. The API also
sets the PostgreSQL transaction to read only and applies a local 3-second
statement timeout. The model's output is parsed with SQLGlot and must be a
single `SELECT` of exactly the selected catalog columns from the selected
view. The API sends its own fixed statement to PostgreSQL after validation.

When Yusuf provides the final view, coordinate its name, columns, value types,
and privileges before replacing the temporary contract. Bedo can consume the
response shape above now.

## Tests

```powershell
cd api
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

The controlled tests cover the original authentication, lock, SQL guard,
and adapter checks plus Turkish intent/role selection, B2/B3/A1/proposed
prompts and API calls, cost clarification and metric ranking, demo-only rows,
normal-mode unavailability, and A2 replay. They do not contact Hugging Face or
PostgreSQL. Live Hugging Face inference remains untested when no token/model is
configured.

The two-database PostgreSQL regression test is separate from the controlled
tests. Run it against an isolated PostgreSQL 14+ instance with a role that can
create databases and roles:

```powershell
docker run --rm -d --name lock-diag-test -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55432:5432 postgres:17-alpine
$env:TEST_POSTGRES_ADMIN_URL = "postgresql://postgres@127.0.0.1:55432/postgres"
python -m pytest integration/test_lock_waits_postgres.py -q
docker stop lock-diag-test
```

The test creates two temporary databases and a restricted API role, then drops
them. It verifies the actual API response while a lock exists in the connected
database and while a lock exists only in the other database. Hugging Face SQL
generation is stubbed; the PostgreSQL view and API database query are live.
Use a free localhost port in the command and URL if 55432 is occupied.

## Local Ollama lock pilot

Start Ollama and make sure `qwen2.5-coder:3b` appears in
`http://127.0.0.1:11434/api/tags` (pull it with
`ollama pull qwen2.5-coder:3b` if needed). From `api/`, run:

```powershell
$env:DIAGNOSTICS_MODEL_PROVIDER = "ollama"
$env:OLLAMA_MODEL = "qwen2.5-coder:3b"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
docker run --rm -d --name lock-ollama-pilot -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55433:5432 postgres:17-alpine
$env:TEST_POSTGRES_ADMIN_URL = "postgresql://postgres@127.0.0.1:55433/postgres"
python -m integration.pilot_ollama_lock
python -m app.offline_validator .pilot/ollama_candidates.jsonl | Tee-Object -FilePath .pilot/ollama_a2_results.jsonl
docker stop lock-ollama-pilot
```

The pilot creates a temporary database, a restricted view reader, and a real
blocking lock. It sends `POST /diagnose` for B2, B3, A1, and proposed while
the lock is held and after it is released. It asserts the actual waiting and
blocking PIDs, then checks empty `rows`. Raw candidate SQL, SQLGlot verdicts,
and API responses are written to ignored `api/.pilot/`; the A2 command
revalidates those exact candidates offline. See the [recorded local result](integration/ollama_pilot_results.md).
