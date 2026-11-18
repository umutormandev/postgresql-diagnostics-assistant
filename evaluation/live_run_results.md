# Live PostgreSQL 17 experiment (2026-11-18)

The isolated run used PostgreSQL 17.12, `pg_stat_statements` 1.11, Ollama
0.40.16, and `qwen2.5-coder:3b` digest
`f72c60cabf6237b07f6e632b2c48d533cef25eda2efbd34bed21c5e9c01e6225`.
Four databases held 10,000 orders each: D1 balanced (500 orders among the first
five products), D2 concentrated (8,000 among the first five). W1 executed
catalog/order lookups; W2 executed report aggregates with concurrent sessions.
Real lock waits and long queries were held while the API ran. The 48 prepared
questions were checked against independent PostgreSQL activity and raw counter
queries. The runner refreshed statistics rankings around every API request.

The completed JSONL has **720 unique, valid** `(case_id, method, repetition)`
records: 180 per condition and 144 per method. Each method has 108 permitted
and 36 denied observations. All denied requests were blocked without returning
unauthorized rows. A transient local connection/sampling gap invalidated 26
intermediate statistics observations; these alone were repeated. The final run
preserved 420 non-statistics and 274 statistics observations from valid
checkpoints, including all 93 wrong model outputs and validator rejections.
An exact key/content audit confirmed those 694 records were unchanged and the
26 invalid observations alone were replaced.

| Condition | Valid records |
| --- | ---: |
| D1-W1 | 180 |
| D1-W2 | 180 |
| D2-W1 | 180 |
| D2-W2 | 180 |

| Method | Correct / permitted | Accuracy | Denied blocked | False block rate | Median API ms | P95 API ms | Median DB ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B1 | 108 / 108 | 100.00% | 36 / 36 | 0.00% | 22.92 | 62.48 | 19.81 |
| B2 | 90 / 108 | 83.33% | 36 / 36 | 16.67% | 982.76 | 2477.32 | 22.32 |
| B3 | 87 / 108 | 80.56% | 36 / 36 | 19.44% | 1121.21 | 2357.48 | 25.97 |
| A1 | 91 / 108 | 84.26% | 36 / 36 | 15.74% | 1098.40 | 2492.29 | 24.81 |
| Proposed | 71 / 108 | 65.74% | 36 / 36 | 34.26% | 1128.66 | 2082.27 | 21.96 |

Among 36 matched permitted cases, B3 alone was correct in six and proposed
alone in zero. Proposed minus B3 was -16.67 percentage points (paired 95%
bootstrap interval -30.56 to -5.56; exact McNemar p = 0.03125). This is one
local model and workload pilot, with a checkpointed run across temporary
connection interruptions.

The raw local files are ignored by Git because the lab configuration contains
generated credentials. `evaluation/output/live-complete.jsonl` SHA-256:
`665D410F73EBA438C4708BA5ECD0D7794833BA311CFDC67594617D4297C04320`.
The independent `evaluation/output/live_cases.jsonl` SHA-256:
`6B7C514ABF6454AE0427AB01D6A9195EE3F7A6878B8392330BD97A8F9297DA59`.
To regenerate the report locally:

```powershell
& 'api/.venv/Scripts/python.exe' -m evaluation.report evaluation/output/live-complete.jsonl --output-dir evaluation/output/live-complete-summary
```

See [live setup and recovery commands](README.md) for reproducing the run.
