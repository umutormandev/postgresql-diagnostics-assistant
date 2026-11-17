# Local Ollama lock pilot — 2026-11-17

Environment: Ollama `qwen2.5-coder:3b` at `http://127.0.0.1:11434`,
`temperature=0`, `num_predict=128`, non-streaming chat, 240-second response
timeout. PostgreSQL 17 ran in a disposable Docker container. The pilot created
one temporary database, a restricted view reader, and a real blocking lock.
The question was **“Hangi oturumlar kilit bekliyor?”** for every method.

The blocker had PID **70** and the waiting session had PID **71**, independently
confirmed with `pg_blocking_pids(71)`. FastAPI's TestClient sent every
`POST /diagnose`; each response returned HTTP 200 while the model and database
calls were live.

| Phase | Method | Raw Ollama SQL candidate | SQLGlot result | API `rows` |
| --- | --- | --- | --- | --- |
| Waiting | B2 | `SELECT * FROM diagnostics.lock_waits;` | Accepted; expanded to the fixed three-column projection | `waiting_pid=71`, `blocking_pid=70` |
| Waiting | B3 | `SELECT waiting_pid, blocking_pid, wait_seconds FROM diagnostics.lock_waits` | Accepted unchanged | `waiting_pid=71`, `blocking_pid=70` |
| Waiting | A1 | `SELECT waiting_pid, blocking_pid, wait_seconds FROM diagnostics.lock_waits` | Accepted unchanged | `waiting_pid=71`, `blocking_pid=70` |
| Waiting | proposed | `SELECT waiting_pid, blocking_pid, wait_seconds FROM diagnostics.lock_waits` | Accepted unchanged | `waiting_pid=71`, `blocking_pid=70` |
| Released | B2 | `SELECT * FROM diagnostics.lock_waits;` | Accepted; expanded to the fixed three-column projection | `[]` |
| Released | B3 | `SELECT waiting_pid, blocking_pid, wait_seconds FROM diagnostics.lock_waits` | Accepted unchanged | `[]` |
| Released | A1 | `SELECT waiting_pid, blocking_pid, wait_seconds FROM diagnostics.lock_waits` | Accepted unchanged | `[]` |
| Released | proposed | `SELECT waiting_pid, blocking_pid, wait_seconds FROM diagnostics.lock_waits` | Accepted unchanged | `[]` |

Only canonical `SELECT waiting_pid, blocking_pid, wait_seconds FROM
diagnostics.lock_waits` was sent to PostgreSQL. The eight raw candidates were
replayed with A2: **8 accepted, 0 rejected**, with the same canonical SQL. The
temporary database, reader role, and Docker container were removed. Local
machine-readable records remain in ignored `api/.pilot/` after rerunning the
pilot.
