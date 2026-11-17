# React Demosu ve Deney

## Arayüzü Açma

Depo kökünde Python 3.11+ ve Node.js ile çalıştırın. İlk terminal:

```powershell
py -3.12 -m venv api/.venv
.\api\.venv\Scripts\python.exe -m pip install -r api/requirements.txt
.\api\.venv\Scripts\python.exe -m evaluation.demo
```

İkinci terminal:

```powershell
npm --prefix ui ci
npm --prefix ui run dev
```

Vite'ın gösterdiği adresi açın. İlk terminaldeki geçici **operator** anahtarını
oturum anahtarı alanına girin ve **B1 · SQL şablonları** yöntemini seçin. Sentetik
demo üç teşhisin de veri kaynağını arayüzde etiketler. **viewer** anahtarıyla
kilit sorgusu yetki bildirimi döndürür, sorgu istatistikleri ise kullanılabilir.

Canlı API için [API kurulumunu](../api/README.md) izleyin ve arayüzü aynı şekilde
başlatın. Vite, `/diagnose` isteklerini varsayılan olarak
`http://127.0.0.1:8000` adresine aktarır. Gerekirse **Vite'ı başlatmadan önce**
`DIAGNOSTICS_API_URL` ortam değişkenine canlı API adresini verin. Yerel demo
anahtarları demo sürecinde oluşturulur ve bir saat geçerlidir.

## Sentetik Kontroller

Depo kökünde, yukarıda kurulan Python ortamıyla:

```powershell
.\api\.venv\Scripts\python.exe -m evaluation.run --fixture
.\api\.venv\Scripts\python.exe -m evaluation.report evaluation/output/benchmark.jsonl
.\api\.venv\Scripts\python.exe -m evaluation.a2 evaluation/controlled_a2.jsonl
```

`controlled_cases.jsonl` dört koşulun her biri için altı açık, üç belirsiz
ve üç yetki dışı soru içerir. Beklenen veri `controlled_snapshots.json` ile
birlikte sabitlenmiştir. Bu mod yalnızca B1 ile API'nin, netleştirmenin ve
karşılaştırma kurallarının tekrarlanabilirliğini kontrol eder.

Ham yanıt, vaka kimliği, yöntem, tekrar, koşul, rol, süre ve engelleme aşaması
`output/benchmark.jsonl` içinde yer alır. `summary.csv` yöntem ve koşul
bazında ölçümleri, `summary.md` genel tabloyu ve yeterli eşleşmiş gözlem
varsa istatistiksel karşılaştırmayı içerir. `a2.json` aynı etiketli aday
listesinde çevrimdışı doğrulayıcının verdiği kararları içerir. A2, kayıtları SQL
olarak çalıştırmaz.

## Canlı Karşılaştırma

Yusuf'un dört veri ve iş yükü koşulunda aynı şema ve yetkilerle hazırladığı
API adreslerini bir JSON nesnesinde `D1-W1`, `D1-W2`, `D2-W1`, `D2-W2`
anahtarlarına bağlayın. Beklenen satırları PostgreSQL'den bağımsız referans
sorgularıyla doğrulayıp sentetik vaka dosyasıyla aynı biçimde yeni bir JSONL
olarak kaydedin. Canlı kayıtlarda `environment` alanı `live` olmalı.
İstemcinin kullanacağı imzalı anahtarları `DIAGNOSTICS_OPERATOR_JWT` ve
`DIAGNOSTICS_VIEWER_JWT` ortam değişkenlerine koyun. Her hedef sunucu,
aynı anahtarları doğrulayacak biçimde ve aynı modelle yapılandırılmalı.

```powershell
.\api\.venv\Scripts\python.exe -m evaluation.run --targets evaluation/output/targets.json --cases evaluation/output/live_cases.jsonl --model "qwen2.5-coder:3b (<model digest ve Ollama sürümü>)" --output evaluation/output/live.jsonl
.\api\.venv\Scripts\python.exe -m evaluation.report evaluation/output/live.jsonl --output-dir evaluation/output/live-summary
```

Koşu varsayılan olarak her vakayı beş yöntemle üç defa, kaydedilen tohumla
karıştırarak çağırır. Belirsiz sorularda önceden sabitlenmiş ölçütü ikinci
çağrıda gönderir ve iki çağrının gecikmesini toplar. DB süresi API'nin
`Server-Timing` başlığından, API gecikmesi ise ölçüm istemcisinden alınır.
Model, veritabanı bağlantısı, oturum anahtarı ve taşıma hataları ham kayıtta
görünür ve doğruluk paydasından çıkarılır. Önerilen yöntem ile B3 karşılaştırması,
üç ölçümü tamamlanmış izinli vakalarda çoğunluk sonucuna dayanır.

Veritabanı planlama ve yürütme sürelerini ayrı kaydetmek için, salt okunur
`DIAGNOSTICS_DATABASE_URL` ayarlı ortamda aşağıdaki komutu çalıştırın. Bu ölçüm
yalnızca izinli katalog `SELECT` sorgusunu kullanır.

```powershell
.\api\.venv\Scripts\python.exe -m evaluation.measure_db lock_waits
```
