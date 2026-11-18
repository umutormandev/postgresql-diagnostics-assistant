# PostgreSQL Diagnostics Assistant

Türkçe teşhis sorularını PostgreSQL sonuçlarına dönüştüren COME 381 prototipi.
FastAPI; kilit beklemeleri, aktif uzun sorgular ve sorgu istatistikleri için
rol denetimi, şablon veya model tabanlı SQL üretimi ve SQL doğrulaması uygular.
Arayüz B1 şablon yöntemiyle açılır. Deney yöntemlerine aynı arayüzden geçilebilir.

- [FastAPI, PostgreSQL ve Ollama](api/README.md)
- [React demosu ve ölçüm komutları](evaluation/README.md)
- [Araştırma makalesi ve demo akışı](docs/research-draft.md)
- [İlk canlı PostgreSQL deneyinin sonuçları](evaluation/live_run_results.md)
- [Yeni canlı deneyler ve yeniden raporlama verileri](evaluation/evidence/README.md)

## Araştırma tasarımı

| COME 381 ölçütü | Projedeki karşılığı |
| --- | --- |
| Baseline | SQL şablonları (B1), zero-shot (B2), few-shot (B3) |
| Önerilen yöntem | Soruya ve role göre seçilen şema bağlamı, SQL doğrulama ve kısıtlı yürütme |
| Gerçek uygulama | React, FastAPI ve PostgreSQL üzerinde üç salt okunur teşhis |
| Ablasyon | Sabit şema bağlamı (A1) ve çevrimdışı aynı adaylarla doğrulama kontrolü (A2) |
| Veri ve iş yükleri | İki veri dağılımı × iki PostgreSQL iş yükü |
| Ölçümler | Doğruluk, yetki engeli, yanlış engel, API medyanı ve p95, DB süresi |
| İstatistik | Vaka düzeyinde eşleştirilmiş exact McNemar ve bootstrap güven aralığı |
| Tekrar üretim | Deney ve veri üretim komutları, vaka tanımları ve ölçüm arşivi |
