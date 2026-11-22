# Yeni canlı PostgreSQL deneylerinin kayıtları

PostgreSQL 17.12, Ollama 0.40.16 (CPU), qwen2.5-coder:3b. Model manifesti SHA-256
`f72c60cabf6237b07f6e632b2c48d533cef25eda2efbd34bed21c5e9c01e6225`.
Önceden yüklenen model, 381 tohumu ve üç tekrar. Veri dağılımları ve iş
yükleri [deney rehberinde](../README.md) listelenir.
Python 3.12.14 · FastAPI 0.119.1 · SQLGlot 29.0.1 · psycopg 3.3.9 · httpx 0.28.1

| Çalışma | Geçerli / planlanan ölçüm | Ayrıntı |
| --- | ---: | --- |
| 48 vakanın yeni koşusu | 720 / 720 | [Özet ve istatistikler](rerun-summary.md) |
| 12 yeni soru ifadesi | 72 / 72 | [Özet ve istatistikler](followup-summary.md) |

İki koşu, [ilk deneyin](../live_run_results.md) sonuçlarından ayrı raporlanır.
İlk deneyde sorgu istatistikleri sıralı kimlikle değerlendirildi. Yeni koşular
sıralamayı ve tüm sayısal sonuç alanlarını değerlendirir. Farklı yerel çalışma
ortamları sebebiyle gecikmeler her koşunun kendi içinde karşılaştırılmalıdır.

Tam koşuda 420 ölçüm, istatistik penceresi oluşurken
çalıştırılan geçerli kilit, aktif sorgu ve yetki kayıtlarından devam ettirildi.

[Canlı deney arşivi](live-evidence.zip) · SHA-256
`5be2fed903cb3bc36d9077c95af67a86a6c45a1bc12a802c3b2eeb8347eb0a6d`

Arşiv ölçüm ve vaka kayıtlarını, anlık görüntü kimliklerini, sorgu metinlerinin
özetlerini ve her dosya için SHA-256 bilgisi içerir. Paylaşım sürümü seçilen
kanıt alanlarını taşır. Ölçümlerin ilk koşudaki dosyaları yerelde kaldı.

Depo kökünden, deney rehberindeki Python ortamını kullanarak, arşivi açıp
raporu yeniden üretebilirsiniz.

```sh
python -m zipfile -e evaluation/evidence/live-evidence.zip evaluation/output/reproduced
python -m evaluation.report evaluation/output/reproduced/rerun/measurements.jsonl --output-dir evaluation/output/reproduced/rerun-summary
python -m evaluation.report evaluation/output/reproduced/followup/measurements.jsonl --output-dir evaluation/output/reproduced/followup-summary
```
