# A Read-Only Natural-Language Assistant for PostgreSQL Diagnostics

**Umut Orman · Sami Yusuf Özdemir · Bedirhan Bozkurt**

Department of Computer Engineering, Doğuş University · COME 381 · Fall 2026

## Abstract

We evaluated a Turkish-language PostgreSQL assistant for lock waits, long-running
queries, and statement cost. The system checks role permissions, validates model
candidates, and runs a restricted diagnostic query. In the first live experiment
with 48 cases, four workload conditions, five methods, and three repeats, the SQL
template baseline B1 matched all 108 permitted measurements at a median API
latency of 22.92 ms. The fixed-schema ablation A1 matched 91 and the proposed
method matched 71. All 180 unauthorized trials were blocked. The results support
using templates as the default for the three diagnostics studied.

## Introduction

PostgreSQL exposes dynamic and accumulated activity through monitoring views
[1]. Operators use that information to identify blocked sessions, active queries
that exceed a duration threshold, and costly queries in a measurement window. The prototype accepts these requests in Turkish and
uses a restricted diagnostics schema. The study examines the effect of prompt
examples and question-specific schema context on result accuracy and latency,
and measures how the application validator handles unauthorized candidates.

The study addresses three questions.

1. How do verified SQL generation, templates, zero-shot prompting and few-shot
   prompting differ in observed accuracy and latency?
2. How does question-specific schema context affect execution accuracy when
   examples and validation are held fixed?
3. How often do the role policy and SQL validator block unauthorized requests
   before execution, and how often do they block permitted requests?

Taken together, these questions measure the trade-off between automation,
correctness and execution safety through accuracy, false blocks and API
latency. The prototype gives the user a summary of the matching diagnostic rows.

## Related Work

Stratica et al. use semantic templates to map natural-language requests to
database queries [2]. Our template baseline uses a fixed diagnostics catalog
and extracts duration and ranking parameters. Gao et al. examine how prompt
representation and example selection affect LLM text-to-SQL benchmarks [3].
The proposed method tests a small, role-scoped form of schema selection,
motivated by work on finding relevant schema elements for a question [4].
We score returned rows against independently established results, following
the distinction between SQL wording and query semantics in Zhong et al. [5].

## Methods

B1 selects a predefined SQL projection and extracts supported parameters from
the question. B2 supplies a fixed allowed schema to the model. B3 adds examples.
A1 supplies a fixed allowed schema with the proposed prompt and examples. The
proposed method supplies the schema selected for the question in the same
prompt. The API validates access before model or database calls, and executes
its own read-only projection. A2 replays the same labeled SQL candidates
through the validator and records its authorization decisions offline.

The benchmark uses equal questions, credentials and data for every method in
each condition. Four isolated PostgreSQL 17.12 databases with
`pg_stat_statements` 1.11 contain 10,000 orders
each. D1 distributes orders evenly across 100 products; D2 places 80% of orders
on five products. W1 runs catalog and order lookups; W2 runs grouped reports
with concurrent sessions. The 48 prepared questions are assigned to these four
combinations. Expected lock and active-query rows come from independent
`pg_stat_activity` and `pg_blocking_pids` queries; expected statistics rankings
come from separately queried raw counter snapshots. The model methods use Ollama
0.40.16 with `qwen2.5-coder:3b` (digest
`f72c60cabf6237b07f6e632b2c48d533cef25eda2efbd34bed21c5e9c01e6225`)
and the same API settings. Because the statistics window advances during
the run, the reference is recalculated from raw snapshots just before and after
each request. The first live run graded the ordered sequence of query identities.
The updated evaluator requires the ordered identities, call counts, total times
and mean times to match a single complete reference, with a 0.001 ms tolerance
for the times. Each run uses three repetitions.
Accuracy is measured against reference results obtained independently. The
run also records clarification success, authorization outcomes, end-to-end
latency and database call time. The primary comparison uses per-case
majority outcomes for proposed and B3, an exact McNemar test, and a paired 95%
bootstrap interval for the percentage-point difference.

## Results

The live lock pilot used Ollama with `qwen2.5-coder:3b` and an isolated PostgreSQL
database. Four language-model methods returned the known waiting and blocking
PIDs while the lock was held. After release, every method returned an empty
result. B2 produced a wildcard projection for both states; the validator
expanded it to the three authorized columns. The eight recorded candidates
passed offline replay. These observations describe one question and two lock
states. [Pilot records](../api/integration/ollama_pilot_results.md)

In the controlled B1 verification, 36 permitted cases and 12 forbidden cases
were each repeated three times. All 108 permitted observations matched the
reference result, and all 36 forbidden observations were blocked. The
controlled A2 list contained four authorized and four unauthorized candidates.
The validator accepted the four authorized candidates and rejected the other
four. The offline unfiltered comparison admits all eight candidates by
definition. The replay compares validation decisions on those labelled
candidates without executing their SQL.

The original live run contains 720 unique valid measurements: 48 questions,
five methods, and three repetitions. Each of D1-W1, D1-W2, D2-W1, and D2-W2
contributed 180 measurements. Each method saw 108 permitted and 36 denied
requests. All 36 denied requests per method were blocked, with no unauthorized
rows returned. These 180 trials represent 12 distinct denied cases evaluated
three times under each of five methods. Permitted-result accuracy and
end-to-end latency were:

| Method | Correct / permitted | Accuracy | Median latency (ms) | False blocks / permitted |
| --- | ---: | ---: | ---: | ---: |
| B1 | 108 / 108 | 100.00% | 22.92 | 0 / 108 |
| B2 | 90 / 108 | 83.33% | 982.76 | 18 / 108 |
| B3 | 87 / 108 | 80.56% | 1121.21 | 21 / 108 |
| A1 | 91 / 108 | 84.26% | 1098.40 | 17 / 108 |
| Proposed | 71 / 108 | 65.74% | 1128.66 | 37 / 108 |

For the 36 matched permitted cases, majority outcomes favored B3 over the
proposed method in six cases and favored the proposed method in none. The
difference was -16.67 percentage points (paired 95% bootstrap interval -30.56
to -5.56; exact McNemar p = 0.03125). The result is evidence against a benefit
from the current dynamic schema prompt in this local pilot; it should not be
generalized to other models or workloads.

The local PostgreSQL connection and snapshot sampler had temporary gaps. We
retained 420 valid non-statistics measurements and 274 valid statistics
measurements, then reran only 26 invalid infrastructure observations when all
four health views were ready. Wrong model answers and validator rejections
were preserved as outcomes. The final 720-record file has no infrastructure
exclusions. [Run summary](../evaluation/live_run_results.md)

### Additional live experiments

The 48-case rerun has 720 valid measurements of 720 with full-field grading.

| Method | Correct / permitted | Median API ms | P95 API ms | Expected permission denial |
| --- | ---: | ---: | ---: | ---: |
| B1 | 108 / 108 | 10.46 | 18.45 | 36 / 36 |
| B2 | 87 / 108 | 1528.68 | 3355.06 | 36 / 36 |
| B3 | 87 / 108 | 1693.04 | 3471.91 | 36 / 36 |
| A1 | 90 / 108 | 1576.31 | 2995.04 | 36 / 36 |
| Proposed | 75 / 108 | 1508.90 | 2933.02 | 36 / 36 |

[Detailed results, role summaries and paired tests](../evaluation/evidence/rerun-summary.md).

The 12-case evaluation of additional wording has 72 valid measurements of 72 with full-field grading.

| Method | Correct / permitted | Median API ms | P95 API ms | Expected permission denial |
| --- | ---: | ---: | ---: | ---: |
| B1 | 21 / 30 | 9.57 | 16.62 | 3 / 6 |
| Proposed | 9 / 30 | 1128.00 | 3338.35 | 3 / 6 |

[Detailed results, role summaries and paired tests](../evaluation/evidence/followup-summary.md).

The two live evaluations report their results separately from the original run. Timing comparisons apply within each run.

[Shareable evidence and report commands](../evaluation/evidence/README.md).

## Discussion

B1 had the highest original live accuracy and lowest API latency. Its result
reflects a closed catalogue whose templates and benchmark share the same
diagnostic intents. A1 and Proposed use the same prompt builder and examples.
Operators receive different schema contexts, while viewers see the same single
permitted view in both methods. The comparison against B3 also reflects other
prompt differences. The offline replay supports the validator on eight candidate
examples, and the authorization trials cover the benchmark's role policy. These
results are conditional on one local model, synthetic orders, and three read-only
diagnostics. They leave generalization to broader PostgreSQL administration,
other phrasing, and production workloads open.

## Reproduction

The original code and summary are pinned at commit `8017e9b`. The original
measurement files stayed in the experiment environment. Use the
[evaluation guide](../evaluation/README.md) to generate the datasets and
workloads, run the API against PostgreSQL, and rebuild the summary from
shareable records. The revised evaluator and first run use distinct scoring
rules and retain separate results.

## References

[1] PostgreSQL Global Development Group, “The cumulative statistics system,”
PostgreSQL 17 Documentation. <https://www.postgresql.org/docs/17/monitoring-stats.html>

[2] N. Stratica, L. Kosseim, and B. C. Desai, “Using semantic templates for a
natural language interface to the CINDI virtual library,” Data & Knowledge Engineering,
vol. 55, pp. 4–19, 2005. <https://doi.org/10.1016/j.datak.2004.12.002>

[3] D. Gao et al., “Text-to-SQL empowered by large language models: A benchmark
evaluation,” Proc. VLDB Endowment, vol. 17, no. 5, 2024.
<https://doi.org/10.14778/3641204.3641221>

[4] M. M. H. Nahid, D. Rafiei, W. Zhang, and Y. Zhang, “Rethinking schema
linking: A context-aware bidirectional retrieval approach for Text-to-SQL,”
in Findings of EACL, 2026, pp. 4516–4546.
<https://doi.org/10.18653/v1/2026.findings-eacl.236>

[5] R. Zhong, T. Yu, and D. Klein, “Semantic evaluation for Text-to-SQL with
distilled test suites,” in Proc. EMNLP, 2020, pp. 396–411.
<https://aclanthology.org/2020.emnlp-main.29/>

## Demo Akışı

1. Operatör anahtarıyla kilit teşhisini çalıştır. Bekleyen PID, engelleyen PID
   ve süreyi göster. Canlı ortamda kilidi kaldırıp boş sonucu doğrula.
2. İzleyici anahtarıyla aynı soruyu gönder ve yetki reddini göster.
3. Operatör olarak 25 saniyeyi aşan aktif sorguları listele.
4. İzleyici olarak en pahalı sorguyu sor. Netleştirme ekranında toplam süreyi
   ve çağrı başına ortalamayı ayrı çalıştır.
5. B1, B2, B3, A1 ve önerilen yöntemin farkını ve A2 doğrulamasını anlat.
