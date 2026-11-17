# Lock diagnostics API

`POST /diagnose` accepts `{"question":"Hangi oturum hangisini engelliyor?"}`. A valid,
server-signed operator bearer token is required. A signed viewer token returns HTTP 403
before any Hugging Face or PostgreSQL call. The role is read only from the verified JWT,
never from the request body or an untrusted role header.

The response contract is:

```json
{"status":"ok","intent":"lock_waits","rows":[{"waiting_pid":7426,"blocking_pid":7412,"wait_seconds":18}],"source":"pg_blocking_pids + pg_locks"}
```

When there are no waits, `rows` is `[]`. Model failures, unsafe SQL, and database
failures return an HTTP error; no partial data is returned.

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

The JWT issuer must sign HS256 tokens with the same secret. Each token needs `sub`,
`role`, `iss`, `aud`, `iat`, and `exp` claims. Supported roles are `operator` and
`viewer`; any other role is forbidden. The default issuer and audience are shown
above. Keep the secret and database credentials on the server. For a local smoke
test only, this command makes a short-lived operator token using the configured
secret:

```powershell
python -c "import os,time,jwt; n=int(time.time()); print(jwt.encode({'sub':'local-test','role':'operator','iss':os.getenv('DIAGNOSTICS_AUTH_ISSUER','postgresql-diagnostics-auth'),'aud':os.getenv('DIAGNOSTICS_AUTH_AUDIENCE','postgresql-diagnostics-api'),'iat':n,'exp':n+300},os.environ['DIAGNOSTICS_AUTH_SECRET'],algorithm='HS256'))"
```

Then send `POST http://127.0.0.1:8000/diagnose` with `Authorization: Bearer <token>`
and a JSON body containing `question`.

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
single `SELECT` of exactly those three columns from that view. The API sends
its own fixed statement to PostgreSQL after validation.

When Yusuf provides the final view, coordinate its name, columns, value types,
and privileges before replacing the temporary contract. Bedo can consume the
response shape above now.

## Tests

```powershell
cd api
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

The tests stub Hugging Face and PostgreSQL. They cover signed operator access,
viewer rejection before either adapter is called, empty results, unsafe SQL
rejection before database access, invalid signatures, and the database adapter's
read-only transaction and timeout setup. Live Hugging Face and PostgreSQL
integration requires configured services and credentials.
