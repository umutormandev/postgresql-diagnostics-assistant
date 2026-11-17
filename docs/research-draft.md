# A Read-Only Natural-Language Assistant for PostgreSQL Diagnostics

## Introduction

PostgreSQL operators use database monitoring information to identify blocked
sessions, active queries that exceed a duration threshold, and costly queries
in a measurement window. The prototype accepts these requests in Turkish and
uses a restricted diagnostics schema. The study examines the effect of prompt
examples and question-specific schema context on result accuracy and latency,
and measures how the application validator handles unauthorized candidates.

## Methods

B1 selects a predefined SQL projection and extracts supported parameters from
the question. B2 supplies a fixed allowed schema to the model. B3 adds examples.
A1 supplies a fixed allowed schema with the proposed prompt and examples. The
proposed method supplies the schema selected for the question in the same
prompt. The API validates access before model or database calls, and executes
its own read-only projection. A2 replays the same labeled SQL candidates
through the validator and records its authorization decisions offline.

The benchmark uses equal questions, credentials and data for every method in
each condition. The frozen question set contains 48 cases, distributed over
two data distributions and two workloads, with three repetitions per method.
Accuracy is measured against reference results obtained independently. The
run also records clarification success, authorization outcomes, end-to-end
latency and database call time. The planned primary comparison uses per-case
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
four. These results measure the benchmark contract with synthetic snapshots.
The live five-method table and paired significance test require the actual
four workload conditions and their reference results.

## Discussion

The lock pilot establishes that the model, validator, API and PostgreSQL
can execute the same diagnosis end to end. The broader experiment will test
whether this behavior holds across Turkish phrasing, privileges, query
duration and aggregate statistics. Controlled demonstrations and live
measurements will be reported with their corresponding data sources.

## Demo Akışı

1. Operatör anahtarıyla kilit teşhisini çalıştır. Bekleyen PID, engelleyen PID
   ve süreyi göster. Canlı ortamda kilidi kaldırıp boş sonucu doğrula.
2. İzleyici anahtarıyla aynı soruyu gönder ve yetki reddini göster.
3. Operatör olarak 25 saniyeyi aşan aktif sorguları listele.
4. İzleyici olarak en pahalı sorguyu sor. Netleştirme ekranında toplam süreyi
   ve çağrı başına ortalamayı ayrı çalıştır.
5. B1, B2, B3, A1 ve önerilen yöntemin farkını ve A2 doğrulamasını anlat.
