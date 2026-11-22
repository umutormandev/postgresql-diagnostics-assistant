# PostgreSQL Teşhis Deneyi

Ortam: Canlı API
Tekrar: 3 · Tohum: 381
Puanlama: live-stat-values-v2

| Yöntem | Ölçüm | Doğru | İzinli vaka | Doğruluk (%) | Netleştirme (%) | Takip (%) | Engelleme (%) | Yanlış engel (%) | Yetkisiz veri | Medyan (ms) | P95 (ms) | Başarılı medyan (ms) | DB (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a1 | 144 | 90 | 108 | 83.33 | 100 | 75.00 | 100 | 16.67 | 0 | 1576.31 | 2995.04 | 1748.54 | 8.66 |
| b1 | 144 | 108 | 108 | 100 | 100 | 100 | 100 | 0 | 0 | 10.46 | 18.45 | 12.09 | 7.87 |
| b2 | 144 | 87 | 108 | 80.56 | 100 | 66.67 | 100 | 19.44 | 0 | 1528.68 | 3355.06 | 1862.66 | 8.54 |
| b3 | 144 | 87 | 108 | 80.56 | 100 | 75.00 | 100 | 19.44 | 0 | 1693.04 | 3471.91 | 1856.19 | 8.76 |
| proposed | 144 | 75 | 108 | 69.44 | 100 | 58.33 | 100 | 30.56 | 0 | 1508.90 | 2933.02 | 1715.74 | 9.05 |

Altyapı hatası nedeniyle ölçüme alınmayan vaka çalıştırması: 0.

## Önerilen Yöntem ve B3

36 eşleşmiş izinli vaka. Doğruluk farkı -11.11 yüzde puan (%95 eşleştirilmiş bootstrap aralığı -22.22 ile -2.78). Exact McNemar p = 0.125. Yalnız önerilen yöntem doğru: 0. Yalnız B3 doğru: 4.

## Şema seçimi (A1) · rol bazında keşifsel karşılaştırma

- operator: 28 eşleşmiş izinli vaka, önerilen yöntem − A1 -17.86 yüzde puan (%95 eşleştirilmiş bootstrap aralığı -32.14 ile -3.57). Exact McNemar p = 0.0625.
- viewer: 8 eşleşmiş izinli vaka, önerilen yöntem − A1 +0.00 yüzde puan (%95 eşleştirilmiş bootstrap aralığı 0.00 ile 0.00). Exact McNemar p = 1.
İzleyici için iki yöntem de aynı izinli istatistik görünümünü alır.
