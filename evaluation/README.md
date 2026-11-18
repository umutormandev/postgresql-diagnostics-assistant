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
oturum anahtarı alanına girin. Varsayılan yöntem **B1 · SQL şablonları**dır. Sentetik
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

## Canlı Karşılaştırma (PostgreSQL 17)

Depo kökünden çalıştırın. [API SQL kurulumunu](../api/README.md) kullanan
`evaluation.live_lab`, dört ayrı veritabanını oluşturur: `D1` 100 ürüne dengeli
dağıtılmış 10.000 sipariş, `D2` siparişlerin %80'i ilk beş üründe yoğunlaşan
10.000 sipariş; `W1` katalog/sipariş sorguları, `W2` rapor toplamaları ve
eşzamanlı oturumlar. `D1-W2` dışında gerçek bir kilit beklemesi tutulur; her
koşulda uzun bir rapor sorgusu çalışır. `evaluation/output/` içindeki bağlantı
bilgileri ve anahtarlar yerel ve Git tarafından yok sayılır.
Her koşulun operatör, izleyici ve örnekleyici girişleri ayrı rastgele parolalar
alır. `POSTGRES_HOST_AUTH_METHOD=trust` yalnızca bu tek kullanımlık yerel
container örneği içindir; paylaşılan sunucuda parola doğrulamasını etkinleştirin.

```powershell
docker run --rm -d --name diagnostics-live -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55432:5432 postgres:17-alpine -c shared_preload_libraries=pg_stat_statements -c pg_stat_statements.track=all
$env:TEST_POSTGRES_ADMIN_URL = 'postgresql://postgres@127.0.0.1:55432/postgres'
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab setup
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab capture
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab workload --repetitions 100
```

İkinci terminalde `& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab hold`
komutunu açık tutun. Üçüncü terminalde
`& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab sample-loop`
komutunu, dördüncü terminalde
`& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab workload-loop --repetitions 100`
komutunu açık tutun. Dört veritabanında her dakika anlık görüntü ve iş yükü
oluşur. İlk
yakalamadan **en az 15 dakika sonra** `references` çalıştırın. Sağlık görünümü
yalnızca son iki dakikadaki anlık görüntüyü kabul eder.
`references` komutu API görünümleri yerine doğrudan `pg_stat_activity`,
`pg_blocking_pids` ve kaydedilmiş ham sayaç çiftlerini sorgular. Aynı 48
soruyu `evaluation/output/live_cases.jsonl` dosyasına gerçek PID'ler ve
queryid'lerle yazar. Süreler hareketli olduğu için uzun sorgu eşleşmesinde
PID ve sorgu metni, kilitte iki PID karşılaştırılır. İstatistiklerde her satırın
sırası, sorgu kimliği, çağrı sayısı, toplam süresi ve ortalama süresi birlikte
eşleşmelidir. Kimlikler tam duyarlıkla metin olarak döner. Süreler için mutlak
tolerans 0,001 ms'dir.

```powershell
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab capture
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab references
$env:DIAGNOSTICS_MODEL_PROVIDER = 'ollama'
$env:OLLAMA_MODEL = 'qwen2.5-coder:3b'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab tokens | Invoke-Expression
```

Başka bir terminalde aynı üç model değişkenini ayarlayıp
`& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab serve` komutunu açık
tutun. Dört API adresi `evaluation/output/targets.json` dosyasına yazılır.
`serve` tüm sunuculara aynı model ayarlarını ve imza anahtarını verir.

Önce küçük koşuda beş yöntemin tamamını deneyin. Ardından ortam sağlıklıysa
tam koşuyu çalıştırın:

```powershell
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab smoke-case
& 'api/.venv/Scripts/python.exe' -m evaluation.run --targets evaluation/output/targets.json --cases evaluation/output/small_cases.jsonl --repetitions 1 --model 'qwen2.5-coder:3b f72c60cabf6237b07f6e632b2c48d533cef25eda2efbd34bed21c5e9c01e6225' --output evaluation/output/small.jsonl
.\api\.venv\Scripts\python.exe -m evaluation.run --targets evaluation/output/targets.json --cases evaluation/output/live_cases.jsonl --live-reference-config evaluation/output/live_lab.json --model "qwen2.5-coder:3b (<model digest ve Ollama sürümü>)" --output evaluation/output/live.jsonl
.\api\.venv\Scripts\python.exe -m evaluation.report evaluation/output/live.jsonl --output-dir evaluation/output/live-summary
```

Koşu varsayılan olarak her vakayı beş yöntemle üç defa, kaydedilen tohumla
karıştırarak çağırır. Belirsiz sorularda önceden sabitlenmiş ölçütü ikinci
çağrıda gönderir ve iki çağrının gecikmesini toplar. DB süresi API'nin
`Server-Timing` başlığından, API gecikmesi ise ölçüm istemcisinden alınır.
İstatistik sıralaması örnekleme boyunca değişebildiği için canlı koşucu, her
istatistik isteğinin hemen öncesinde ve sonrasında bağımsız ham sayaç sorgusunu
tekrarlar. Bütün yanıt, önceki veya sonraki yakalamanın tek bir eksiksiz
sonucuyla eşleşirse doğru sayılır. Her iki referansın örnek kimlikleri ölçüm
kaydına eklenir.
Yerel PostgreSQL kısa süreli erişilemezse `sample-loop` hatayı kaydedip sonraki
dakikada tekrar dener. `insufficient_data` olan ölçümler altyapı hatası sayılır.
Kesilen koşuyu, sağlıklı pencere geri geldikten sonra yalnızca geçerli kayıtları
yeniden kullanarak tamamlamak için farklı bir çıktı dosyası verin:

```powershell
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab refresh-activity
& 'api/.venv/Scripts/python.exe' -m evaluation.run --targets evaluation/output/targets.json --cases evaluation/output/live_cases.jsonl --live-reference-config evaluation/output/live_lab.json --resume-from evaluation/output/live.jsonl --model 'qwen2.5-coder:3b (<model digest ve Ollama sürümü>)' --output evaluation/output/live-resumed.jsonl
```

`refresh-activity`, oturum tutucuları yeniden başlatıldıysa yeni PID'leri
bağımsız PostgreSQL sorgularıyla günceller. Raporu yalnızca 720 kayıtlık tamamlanmış
dosyada çalıştırın.
İstatistik penceresi yeniden oluşurken diğer teşhisleri tamamlamak için
`--exclude-intents query_stats` kullanabilirsiniz; daha sonra birden çok
`--resume-from` dosyasını sırayla vererek tamamlanmış ölçümleri birleştirin.
Doğrulayıcı tarafından reddedilen veya yanlış yanıtlanan izinli vakalar geçerli
deney sonuçlarıdır ve `--resume-from` bunları yeniden çalıştırmaz; yalnızca
taşıma/model/veritabanı kesintileri ile `insufficient_data` kayıtları tekrarlanır.
İlk 720 kayıtlık koşunun özeti [canlı sonuçlarda](live_run_results.md).
Yeni koşuların [ölçümleri ve raporları](evidence/README.md) yayımlanmıştır.
İlk koşu sorgu istatistiklerini sıralı kimlik üzerinden değerlendirdi.
Güncel koşucunun ölçüm meta verisi `live-stat-values-v2` olarak kaydedilir.
Model, veritabanı bağlantısı, oturum anahtarı ve taşıma hataları ham kayıtta
görünür ve doğruluk paydasından çıkarılır. Önerilen yöntem ile B3 karşılaştırması,
üç ölçümü tamamlanmış izinli vakalarda çoğunluk sonucuna dayanır.
Rapor `summary.csv` dosyasındaki `role-operator` ve `role-viewer`
satırlarında A1 ve Proposed dâhil her yöntemi ayrı gösterir.

### Yeni sorularla kontrol ve paylaşılabilir deney verisi

`followup_cases.jsonl`, aynı dört koşulda farklı ifadeler kullanan 12 ek vaka
içerir. B1 ve Proposed yöntemlerini ilk deneyden bağımsız bir tabloda raporlayın.

```powershell
& 'api/.venv/Scripts/python.exe' -m evaluation.live_lab references --cases evaluation/followup_cases.jsonl --references-output evaluation/output/followup_live_cases.jsonl
& 'api/.venv/Scripts/python.exe' -m evaluation.run --targets evaluation/output/targets.json --cases evaluation/output/followup_live_cases.jsonl --live-reference-config evaluation/output/live_lab.json --methods b1 proposed --model 'qwen2.5-coder:3b (<model digest ve Ollama sürümü>)' --output evaluation/output/followup.jsonl
& 'api/.venv/Scripts/python.exe' -m evaluation.export_evidence evaluation/output/followup.jsonl evaluation/output/followup_live_cases.jsonl evaluation/output/followup-evidence
& 'api/.venv/Scripts/python.exe' -m evaluation.report evaluation/output/followup-evidence/measurements.jsonl --output-dir evaluation/output/followup-summary
```

Tam deney için aynı dışa aktarma komutuna 720 kaydın dosyasını ve ilgili
`live_cases.jsonl` dosyasını verin. Araç vaka, yöntem ve tekrar kombinasyonlarını
doğrular. Seçili sonuç alanları, vakalar, ölçüm meta verisi ve SHA-256 kayıtları
ile yeniden raporlamak mümkündür. Sorgu metni özeti alınarak yayımlanır.

Veritabanı planlama ve yürütme sürelerini ayrı kaydetmek için, salt okunur
`DIAGNOSTICS_DATABASE_URL` ayarlı ortamda aşağıdaki komutu çalıştırın. Bu ölçüm
yalnızca izinli katalog `SELECT` sorgusunu kullanır.

```powershell
.\api\.venv\Scripts\python.exe -m evaluation.measure_db lock_waits
```
