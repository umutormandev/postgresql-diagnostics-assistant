# PostgreSQL Teşhis Deneyi

Ortam: Canlı API
Tekrar: 3 · Tohum: 381
Puanlama: live-stat-values-v2

| Yöntem | Ölçüm | Doğru | İzinli vaka | Doğruluk (%) | Netleştirme (%) | Takip (%) | Engelleme (%) | Yanlış engel (%) | Yetkisiz veri | Medyan (ms) | P95 (ms) | Başarılı medyan (ms) | DB (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| b1 | 36 | 21 | 30 | 70.00 | 100 | 100 | 50.00 | 0 | 0 | 9.57 | 16.62 | 11.78 | 7.13 |
| proposed | 36 | 9 | 30 | 30.00 | 100 | 50.00 | 50.00 | 50.00 | 0 | 1128.00 | 3338.35 | 1638.31 | 9.27 |

Altyapı hatası nedeniyle ölçüme alınmayan vaka çalıştırması: 0.

## Önerilen Yöntem ve B1

10 eşleşmiş izinli vaka. Doğruluk farkı -40.00 yüzde puan (%95 eşleştirilmiş bootstrap aralığı -70.00 ile -10.00). Exact McNemar p = 0.125. Yalnız önerilen yöntem doğru: 0. Yalnız B1 doğru: 4.
