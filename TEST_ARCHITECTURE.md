# LLM Council Web — Test Mimarisi ve Sürüm Kabul Standardı

Durum: Önerilen hedef mimari; uygulama ve kabul testleri henüz tamamlanmış sayılmaz.
Tarih: 24 Kasım 2026.
İlgili belge: [STABILIZATION.md](STABILIZATION.md), özellikle Test ve sürüm yönetimi, P0, P1 ve P2.
İncelenen başlangıç referansı: main `56782a17bcafc39b86b9787557daf8bc99896b10` (denetim referansı `c445a64`); Amiable Core 0.53.0.

## 1. Amaç ve sınırlar

Amaç, uygulamanın gerçekte çalıştığını, hata durumlarını doğru yönettiğini ve dağıtılan Windows paketinin test edilen uygulamayla aynı olduğunu kanıtlamaktır. Test sayısı veya yeşil CI tek başına başarı değildir. Testler ilgili işlev bozulduğunda başarısız olmalıdır.

- Karpathy arayüzü, Amiable'ın native Council algoritması ve dört native profil (Quick, Balanced, High, Reasoning) korunur. Free, uygulama düzeyindeki ayrı profildir.
- Motoru yeniden yazmak, yeni AI ajanları veya özel bir test framework'ü geliştirmek hedef değildir.
- Mevcut faydalı Pytest testleri silinmez; gerçek kapsamlarına göre sınıflandırılır. Eksik işlevsel testler eklenir, yanıltıcı başarı kriterleri değiştirilir.
- Testler yeni özellik tasarımını dayatmaz. Beklenen hata, retry, fallback ve eşzamanlılık davranışları stabilizasyon kararlarıyla uyumlu olmalıdır.
- Bu belge bir uygulama/kabul standardıdır; testlerin gerçekten çalıştırıldığına veya sorunların çözüldüğüne ilişkin rapor değildir.

## 2. Mevcut testlerin kapsam denetimi

| Mevcut test veya kontrol | Gerçekte doğruladığı | Kanıtlamadığı |
| --- | --- | --- |
| `tests/test_backend_integration.py` | FastAPI sözleşmesi, sahte sonuçların REST/SSE üzerinden aktarımı ve saklanması | Amiable'ın gerçek üç aşamasının çalışması |
| SSE ilerleme testi | Test tarafından üretilen olayların sırayla taşınması | Native motorun gerçek aşama geçişleri ve hata durumları |
| `tests/test_free_profile.py` | Free ayarları, belirli guard/fallback davranışları ve uygulama sınırındaki mock'lar | Güncel sağlayıcı erişimi, gerçek model fiyatı, tüm ağ çağrılarının ücretsizliği |
| `tests/test_conversation_context.py` | Hafıza işlevi ve sahte özet/model sonuçları | Gerçek sağlayıcıyla uzun konuşma davranışı |
| `tests/test_engine_runtime.py` | Hook ve kalite ölçümü davranışlarının belirli bölümleri | Bütün native motor akışı ve gerçek hata sözleşmeleri |
| `tests/test_free_profile_notice.py` ve diğer kaynak metni kontrolleri | Belirli JSX/metin ifadelerinin kaynakta bulunması | Kullanıcının butonlara tıklayabilmesi, doğru profilin görünmesi ve ayarların kaydedilmesi |
| Vite build | Kodun derlenebilmesi | Tarayıcıda işlevlerin doğru çalışması |
| Windows EXE smoke testi | Paketlenen uygulamanın HTTP/UI kaynaklarını sunabilmesi | Temiz kurulum, yükseltme, gerçek Council oturumu |
| `tests/test_release_contract.py` | Belirli sürüm, yayın komutu ve not metinlerinin varlığı | Kurulu/paketlenmiş uygulamanın gerçek sürümü, motoru ve commit'i |

Örnek: `run_full_council` yerine başarılı bir tuple döndüren sahte fonksiyon kullanmak API aktarımını test eder; motor çalışmasa bile test geçebilir. Benzer biçimde `emit_progress` çağrılarını testte elle üretmek native aşamaların çalıştığını kanıtlamaz. Bu testler kapsamı doğru adlandırılmış sözleşme/regresyon testleri olarak korunabilir.

Mevcut `.github/workflows/ci.yml` Pytest ve Vite build çalıştırmaktadır. Gerçek tarayıcı E2E ve gerçek sağlayıcı kabulünün ayrıca eklenmesi gerekir. Windows workflow'daki smoke testinin HTTP 200'den sonra Free profil ve anahtar kontrolleri başarısız olsa da başarı durumunu koruyabilmesi STABILIZATION.md'de kayıtlı bir false-positive örneğidir; düzeltme ve test sonucu ayrıca doğrulanmalıdır.

## 3. Dört katmanlı hedef mimari

| Katman | Araçlar | Test edilen sınır |
| --- | --- | --- |
| 1 — Unit & Regression | Pytest | Fonksiyonlar, hata sözleşmeleri, veri bütünlüğü, REST/SSE aktarımı |
| 2 — Real Engine Integration | Pytest + HTTPX veya uygun HTTP sınırı | Gerçek Amiable motoru, kontrollü sağlayıcı ve canlı OpenRouter |
| 3 — Browser End-to-End | Playwright | Gerçek tarayıcı, React UI, FastAPI, SSE, kalıcı kayıtlar |
| 4 — Windows Release Acceptance | PowerShell + Playwright + Pytest gerektiğinde | Gerçek dağıtım paketi, temiz kurulum, yükseltme, geri dönüş, canlı Council |
| Orkestrasyon | GitHub Actions | CI, kabul sonuçları, paket kimliği ve yayın engeli |

Önerilen test grupları: mevcut `tests/` altında unit/regression, controlled engine integration ve live integration; frontend altında Playwright E2E; Windows kabul senaryoları için PowerShell/Playwright. Kesin dizin adları uygulama sırasında mevcut repo düzenine göre seçilir. Pytest marker'ları veya ayrı komutlarla kontrollü ve canlı testler birbirinden ayrılır. Ek framework zorunlu değildir.

### 3.1 Katman 1 — Unit ve regresyon

- Ayar okuma/yazma, validasyon, Free guard, profil geçişi, varsayılanlar ve engine-effective konfigürasyon.
- Başarısız/boş Chairman cevaplarının hafızadan çıkarılması; eski ve geçersiz özet cache'i; özetleme başarısızlığı; başarılı hafıza güncellemesi.
- Stage 2 geçersiz veya boş değerlendirme, Stage 3 boş sentez, kısmi/başarısız sonuçların doğru işaretlenmesi.
- REST/SSE olay sözleşmesi, tamamlanma/hata, kayıt ve yeniden yükleme.
- JSON atomik yazım, bozuk konuşma dosyasının diğer konuşmaları etkilememesi, güvenli silme ve veri kaybının önlenmesi.
- Testler birbirinden izole geçici veri/konfigürasyon dizinleri kullanır; gerçek kullanıcı verisi ve anahtarı değiştirilmez.
- Mock'lar kullanılabilir fakat bu grubun geçmesi motorun veya sağlayıcının çalıştığı anlamına gelmez.

## 4. Katman 2 — Gerçek motor entegrasyonu

### 4.1 Controlled Integration (deterministik)

Gerçek FastAPI, uygulama adaptörü, Amiable `run_full_council`, native Stage 1, Stage 2, Stage 3 ve varsa native Stage 1.5 çalışır. Sadece harici sağlayıcı HTTP sınırında kontrollü cevaplar verilir. HTTPX transport veya motorun kullandığı gerçek HTTP/gateway sınırında fixture seçilir. Motor fonksiyonlarını, değerlendirme algoritmasını veya tamamlanmış Council sonucunu mock'lamak yasaktır (bu tür testler Katman 1'e aittir).

Başarılı sağlayıcı fixture'ları geçerli model cevapları, geçerli peer ranking ve Chairman sentezi üretir; ayrı fixture'lar geçersiz ranking, 404, 429, 5xx, timeout, boş içerik ve bağlantı kesilmesi üretir. Gerçek istemcinin request/response ayrıştırması, timeout ve hata sınıflandırması çalışır. Testler canlı internete veya API anahtarına ihtiyaç duymadan tekrarlanabilir.

İstek kayıtları: model kimliği, gateway/host, sıra, aşama, HTTP durum kodu, retry sayısı; hassas header ve anahtarlar maskelenir. Aşamalar arası ilişkiler, dönen sonuç, metadata, kalıcı konuşma kaydı ve ağ çağrılarının beklenen sayısı denetlenir. Belirli LLM metnini ezberden karşılaştırmak yerine sözleşme ve yapı doğrulanır. Native algoritmanın beklenen davranışı korunur.

### 4.2 Live Integration (gerçek sağlayıcı)

Gerçek Amiable ve gerçek OpenRouter çalışır. Hiçbir model cevabı veya native aşama taklit edilmez. Canlı test, hesap erişimini, güncel katalogdaki modelleri, gerçek üç aşamayı, kullanım verisini ve uygulamanın bu sonucu doğru göstermesini doğrular. Gerekirse önce aynı motor/config ile UI'dan bağımsız küçük bir tanı testi çalıştırılır; ardından uygulamanın REST/SSE yolu ve paketlenmiş EXE doğrulanır.

Canlı testler her PR'da zorunlu değildir: ücret, rate limit ve sağlayıcı istikrarsızlığı nedeniyle beta öncesinde ve gerektiğinde ayrı çalıştırılır. Test anahtarı güvenli ortam değişkeni/CI secret olarak sağlanır; log, artifact veya repoya yazılmaz. Ödenmesi mümkün olan native profillerin canlı çağrıları açık yetki ve belirlenmiş harcama sınırı gerektirir. Kontrollü testlerde tüm profiller doğrulanabilir.

Gerçek LLM sonuçları değişken olduğu için test, belirli cümleyi değil üç aşamanın geçerli verilerini, en az beklenen sayıda kullanılabilir model/peer sonucu, geçerli sentezi, hata yokluğunu ve veri tutarlılığını kontrol eder. Kısmi veya başarısız bir oturum normal başarı kabulünü geçmez.

### 4.3 Motor senaryoları ve kabul kriterleri

| ID | Senaryo | Kabul kriteri |
| --- | --- | --- |
| ENG-01 | Normal Council oturumu | Gerçek motor üç aşamayı tamamlar; geçerli bireysel yanıtlar, geçerli peer ranking ve boş olmayan Chairman sentezi döner; native olaylar ve kalıcı kayıt eşleşir. Controlled + Live. |
| ENG-02 | Tek model HTTP 404 | Sağlayıcı/model hatası doğru tanınır; tasarlanan uygun ücretsiz alternatif veya açık kısmi sonuç davranışı doğrulanır, ücretli yönlendirme olmaz. |
| ENG-03 | Tüm modeller 404 | Başarısız oturum, sıfır kullanılabilir Stage 1 sonucu; sahte final cevap/başarı yoktur; hata tanılanabilir. |
| ENG-04 | Tek model 429 | Retry/fallback tanımlı ve sınırlıdır; istek sayısı ve varsa Retry-After dikkate alınır; hata veya kısmi sonuç doğru gösterilir. |
| ENG-05 | Hesap kotası tükenir | Hesap geneli limit, geçici model limitinden mümkün olduğu ölçüde ayrıştırılır; anlamsız model rotasyonu ve sınırsız API isteği yoktur. |
| ENG-06 | Timeout / bağlantı sorunu / 5xx | Tanımlı timeout ve retry sınırı aşılmaz; oturum sonsuza kadar beklemez; uygun hata veya kısmi sonuç oluşur. |
| ENG-07 | Stage 2 değerlendirmeleri başarısız | Sıfır/geçersiz/eksik ranking, geçerli ranking'den ayrılır; review atlanması ile hatası birbirine karışmaz; sahte peer-validated başarı yoktur. |
| ENG-08 | Chairman başarısız | Stage 1/2 sonuçları saklanır; Stage 3 başarısız gösterilir; hata karar veya hafıza olarak kullanılmaz; uygun Free fallback varsa yalnız sentez yeniden denenir. |
| ENG-09 | Chairman boş yanıt | Boş/whitespace sentez başarı veya geçerli verdict sayılmaz; kalite metriği yanıltıcı olmaz. |
| ENG-10 | Uzun konuşmada özetleme modeli çalışmaz | Tanımlı alternatif veya kontrollü hata/degraded-context davranışı çalışır; eski kayıt kaybolmaz, başarısız özet başarı olarak saklanmaz; bağlam kaybı açıkça bildirilir. |
| ENG-11 | Council sırasında profil/ayar değişikliği | Devam eden oturum ilk güvenlik ve profil sınırını korur veya ayar değişikliği güvenli şekilde reddedilir; Free oturumda hiçbir ücretli istek yoktur. |
| ENG-12 | İki eşzamanlı Council | Ayarlar, model istekleri, progress, cevaplar ve konuşma kayıtları oturumlar arasında karışmaz. |

ENG-02–12 özellikle deterministik HTTP fixture'larıyla çalıştırılır. Gerçek sağlayıcıda yapay olarak kota tüketmeye veya kesinti üretmeye gerek yoktur. Live ENG-01 gerçek hesap ve güncel model erişimi için zorunludur. Native özelliklerin gerçek entegrasyonu ayrıca dört profil, synthesis/binary/tie-breaker, dissent, self-vote exclusion, style normalization, reviewer limiti, timeout, gateway fallback ve quality metrics için ilgili kontrollü senaryolarla doğrulanır; ayarlar yalnız YAML'da görünmekle geçmez.

### 4.4 Free profili — zorunlu güvenlik ve canlı kabul

- [ ] Normal Free oturumunda üç farklı, erişilebilir ücretsiz model seçilir; gerçek katalog ve erişim kontrolü yapılır.
- [ ] Bütün model/gateway çağrıları, ek başlık/özetleme/evaluation çağrıları ve retry/fallback'ler dahil denetlenir.
- [ ] Her modelin güncel OpenRouter kataloğundaki ilgili fiyat alanları kontrol edilir; yalnız `:free` son ekine güvenilmez.
- [ ] Chairman ücretli modele yönlendirilmez.
- [ ] Başlık oluşturma ve hafıza ücretli modele yönlendirilmez.
- [ ] Ücretli gateway fallback veya model alias/routing dönüşümü gerçekleşmez.
- [ ] Council devam ederken ayar değişikliği Free oturumu ücretli hale getirmez.
- [ ] Gerçek kullanım maliyeti sıfırdır; sağlayıcı kullanım/charge bilgisi mevcutsa doğrulanır. Yalnız sıfır toplam maliyet veya sıfır token yeterli değildir.
- [ ] API anahtarı eksik/geçersizken veya Free guard geçersizken ücretli çağrı yapılmaz.
- [ ] 404/429/5xx/timeout durumlarında da yukarıdaki sınırlar geçerlidir.

Güncel katalog fiyatı ve ağdaki gerçek model seçimi birlikte kontrol edilmelidir. Ücretli çağrı güvenliği yalnız sonradan denetim değil, mümkün olan gerçek istek sınırında çağrıdan önce doğrulama gerektirir. Doğrulanamayan model/route kullanıma alınmamalıdır. Live test için anahtar, erişilebilir modeller veya güvenilir kullanım kanıtı yoksa kabul sonucu BLOCKED/FAIL olmalı, PASS olmamalıdır.

## 5. Katman 3 — Playwright tarayıcı E2E

Gerçek Chromium tarayıcısında derlenmiş React UI ve çalışan FastAPI backend kullanılır. Ana deterministik senaryolarda backend veya hazır SSE sonucu taklit edilmez; harici sağlayıcı HTTP sınırı kontrol edilebilir. Sınırlı canlı kabul senaryosu gerçek OpenRouter ile çalışır.

Kaynak kodda JSX metni aramak yerine gerçek DOM, erişilebilir roller, tıklama, görünürlük, ağ istekleri, backend sonucu ve kalıcı kayıt test edilir. Kaynak kontrolleri yalnız statik sözleşme/lint kontrolleri olarak tutulabilir; kullanıcı işlevinin kanıtı sayılmaz.

| ID | Kullanıcı senaryosu | Kabul kriteri |
| --- | --- | --- |
| UI-01 | Uygulamayı aç, yeni konuşma oluştur | UI ve API hazır; yeni kayıt sidebar'da ve backend'de bulunur. |
| UI-02 | OpenRouter API anahtarı gir ve kaydet | Ayarlar kaydedilir, configured durumu değişir, anahtar UI/API cevaplarında veya loglarda ifşa olmaz. |
| UI-03 | Profil değiştir, kaydet, sayfayı yenile | Görünen profil, backend effective ayarları ve diskteki YAML uyumludur; Free bildirimi doğru görünür/gizlenir. |
| UI-04 | Soru gönder, üç aşamayı görüntüle | Gerçek aşama olayları ve sonuçlar görünür; loading biter; UI/REST/SSE/JSON uyumludur. |
| UI-05 | Modeller arasında geçiş yap | Her sekme doğru modelin gerçek bireysel cevabını gösterir. |
| UI-06 | Peer review ve sıralamaları incele | Review, label mapping ve aggregate ranking backend sonucuyla eşleşir. |
| UI-07 | Chairman cevabını incele | Sentez, verdict ve uyarılar doğru; boş/hatalı cevap final başarı olarak görünmez. |
| UI-08 | Takip sorusu sor | İlgili konuşma bağlamı kullanılır; geçmiş ham kayıt değişmez; başka konuşmanın bağlamı sızmaz. |
| UI-09 | API reddi/hata üret | Kullanıcı anlamlı hata görür; loading biter, olmayan cevap veya kaydedilmemiş başarılı mesaj gösterilmez. |
| UI-10 | SSE bağlantısını complete öncesinde kes | Başarı gösterilmez; spinner sonsuza kadar kalmaz; yeniden yüklemede backend ile tutarlı durum görülür. |
| UI-11 | Sayfayı yenile, geçmişi aç | Önceki kullanıcı soruları, cevaplar, hata/partial durumları ve kaydedilen veri eşleşir. |
| UI-12 | Konuşmayı sil | UI ve backend/diskten silinir; diğer konuşmalar korunur. |
| UI-13 | Free'den Quick, Balanced, High, Reasoning profillerine geç | Her profil ayrı ayrı seçilir, kaydedilir ve gerçek effective config doğrulanır; Free bildirimi/özellik sınırları güncellenir. |
| UI-14 | Council sürerken ayar değiştir | UI ve backend güvenli oturum davranışını korur; Free'de ücretli model çağrısı olmaz. |

Ek kontroller: Stage 2 boş/geçersiz sonucu, Stage 3 hata/boş sonucu, kısmi oturum, cache/hafıza uyarıları, synthesis/binary/tie-breaker ve dissent seçimi. Reddedilen istek, kullanıcı mesajının yalnızca gerçekten saklanmışsa kalıcı görünmesi ve SSE'nin EOF/terminal event durumları P2 ile birlikte doğrulanır.

Başarısızlıkta Playwright trace, ekran görüntüsü, video (uygun durumda), console hataları ve maskelenmiş ağ kayıtları artifact olarak saklanır. Testler tek başına sabit bekleme sürelerine değil gerçek UI ve ağ durumlarına dayanır.

## 6. Katman 4 — Windows EXE kabulü

Kaynak kod veya dev server'ın çalışması dağıtılan EXE'nin çalıştığını kanıtlamaz. Windows runner/izole Windows ortamında gerçek release adayı ZIP ve installer kullanılır. Güncelleme testi gerçek önceki yayınlanmış sürüm üzerinden yapılır. Önceki kullanıcı verilerini temsil eden fixture'lar kullanılır; gerçek kullanıcı sırları ve verileri test için kopyalanmaz.

| ID | Senaryo | Kabul kriteri |
| --- | --- | --- |
| WIN-01 | Temiz Windows kurulumu | ZIP doğrulaması, gerçek installer, EXE ve gerekli dosyalar çalışır; doğru varsayılan profil ve anahtarsız başlangıç. |
| WIN-02 | İlk açılış | Uygulama GUI subsystem ile başlar, gerçek UI ve API hazırdır; tarayıcı/sistem tepsisi açma-kapatma davranışı doğrulanır. Headless smoke, GUI kabulünün yerine geçmez. |
| WIN-03 | Önceki gerçek sürümden yükseltme | API anahtarı, konuşma geçmişi ve uyumlu kullanıcı ayarları korunur; yeni sürüm ve motor gerçekten kullanılır. |
| WIN-04 | Eski veya sorunlu YAML ile yükseltme | Güvenli migrasyon ya da açık ret; uyumlu özel/native ayarlar gereksiz sıfırlanmaz, ücretli profile sessiz geçiş yoktur. |
| WIN-05 | Başarısız güncelleme | Yükseltme sonrası readiness başarısızsa eski kurulum ve veri geri yüklenir; eski sürüm tekrar açılabilir. |
| WIN-06 | Port çakışması | Başka servis/önceki süreç yanlışlıkla başarılı kurulum kabul edilmez. |
| WIN-07 | Gerçek Council oturumu | Paketlenmiş EXE ile gerçek Amiable/OpenRouter üç aşamayı tamamlar; Free maliyet/route kabulü ve UI/kayıt doğrulanır. |
| WIN-08 | Kapat ve yeniden aç | Süreç düzgün kapanır; ayarlar ve konuşma verileri kaybolmaz. |
| WIN-09 | Sürüm ve artifact doğrulaması | Gerçek EXE/API About, app version, motor sürümü, commit SHA, release tag ve ZIP hash uyumludur. |

Smoke test yalnız ilk readiness kapısıdır; HTTP 200 tek başına PASS değildir. Doğru süreç, doğru sürüm, UI varlığı, etkin ayarlar, varsayılan Free konfigürasyonunun geçerliliği ve dağıtımda anahtar bulunmaması kontrol edilir. Headless testin doğrulayamadığı tray/browser davranışı gerektiğinde interaktif Windows oturumunda gerçek manuel kabul ile belgelenir. Windows kabulü için kurulu sürümün gerçek kimliği ve yükseltme başlangıç sürümü kaydedilir.

## 7. CI ve release gate

Önerilen zorunlu işlem sırası:

1. Commit ve bağımlılık kurulumunun doğrulanması (sabit motor sürümü; mümkün olduğunda kilitli bağımlılıklar).
2. Unit & Regression Tests.
3. Controlled Engine Integration Tests.
4. Browser E2E Tests ve üretim frontend build.
5. Windows EXE Build.
6. Windows Installation & Upgrade Tests.
7. Live OpenRouter Acceptance Tests (kaynak motor + paketlenmiş EXE; Free güvenlik dahil).
8. Version & Artifact Verification.
9. Bütün zorunlu kapılar geçerse Release Approved; aksi durumda yayın durur.

| Test grubu | Her PR | Beta öncesi |
| --- | --- | --- |
| Unit | Zorunlu | Zorunlu |
| Regression | Zorunlu | Zorunlu |
| Controlled Integration | Zorunlu | Zorunlu |
| Browser E2E | Zorunlu | Zorunlu |
| Windows paket ve kabul | Gerektiğinde (Windows smoke önerilir) | Zorunlu |
| Live OpenRouter | Gerektiğinde / kontrollü çalıştırma | Zorunlu |
| Sürüm ve artifact doğrulaması | Statik tutarlılık kontrolü yapılabilir | Zorunlu |

- GitHub Actions'ta test sonuçları açıkça ayrı job/check olarak gösterilir. PR'da canlı testlerin olmaması, canlı kabulün geçmiş olduğu anlamına gelmez.
- Yayın adımı tüm zorunlu job'lara bağımlı olur. Workflow dispatch veya commit marker'ı yayın talebi olabilir, kabul koşullarını atlama yolu olamaz.
- Bir zorunlu test başarısız, skipped, cancelled, eksik anahtar nedeniyle blocked veya sonucu belirsiz ise beta yayımlanmaz. Canlı sağlayıcı arızası ürün hatasıyla karıştırılmaz fakat yayın kabulü de geçilmiş sayılmaz.
- Paket bir kez oluşturulur. Test edilen ZIP/EXE ile yayımlanan ZIP/EXE birebir aynı olmalı; yayın öncesi tekrar derlenmemeli. SHA256 ve commit doğrulanır; sonuçlar aynı artifact kimliğine bağlanır.
- Test artifact'ları: commit SHA, app/core sürümleri, test kimlikleri ve sonuçları, çalıştırma ortamı, maskelenmiş loglar, Playwright trace ve ekran görüntüsü, ZIP SHA256, gerçek kullanım kanıtı. API anahtarı veya kişisel veri artifact'e girmez.
- Stabilizasyon ve kabul testleri tamamlanmadan yeni beta yayımlanmaz. Mevcut release'in varlıkları sessizce değiştirilmez.

## 8. Sürüm tutarlılığı ve izlenebilirlik

İnceleme sırasında görülen başlangıç durumu (kurulu Windows EXE ölçülmemiştir):

| Kaynak | Sürüm |
| --- | --- |
| `pyproject.toml` | 1.0.0 |
| Backend `APP_VERSION` | 1.1.0-beta.5 |
| Windows installer | v1.1.0-beta.5 |
| Windows release workflow | v1.1.0-beta.5 |
| Amiable Core bağımlılığı | 0.53.0 |
| Kullanıcının Windows'ta kurulu EXE'si | Doğrulanmadı |

Uygulama sürümü tek yetkili kaynaktan yönetilir; Python paket metadata'sı, API About/health, installer, release tag ve release notes bu kaynaktan türetilir veya eşitlikleri doğrulanır. Python/PEP 440 ve Git tag gösterimleri (ör. `1.1.0b5` ve `v1.1.0-beta.5`) aynı sürümün kanonik eşlemesi olarak ele alınabilir; sadece metin eşitliği yeterli değildir. Amiable sürümü bağımsız, pinlenmiş motor bağımlılığıdır; gerçek yüklenen paket metadata'sından ölçülür. Derleme commit SHA'sı pakete kaydedilir ve uygulamanın runtime bilgisi üzerinden denetlenir.

Karşılaştırma: güncel main commit, release tag'in hedef commit'i, paket manifesti/hash'i, backend/EXE runtime sürümü, gerçek motor sürümü ve kullanıcının kurulu Windows sürümü. Sürüm metnini README'de aramak bu karşılaştırmanın yerine geçmez.

## 9. Testlerin hataları yakaladığını doğrulama

Kontrollü hata enjeksiyonu ve gerektiğinde geçici mutation uygulanarak testin beklenen hatada gerçekten kırıldığı gösterilir. Değişiklikler kalıcı üretim koduna veya main'e taşınmaz. Önce normal durum PASS, sonra bilinen kusur FAIL, kusur kaldırılınca tekrar PASS olmalıdır.

| Kasıtlı kusur | Yakalanması gereken kontrol |
| --- | --- |
| Stage 2 boş veya geçersiz ranking'i başarı sayar | ENG-07, UI-06 |
| Chairman boş cevabını başarı sayar | ENG-09, UI-07 |
| Chairman veya hafıza ücretli modele geçirilir / ücretli fallback açılır | Free güvenlik kabulü, ENG-11 |
| SSE terminal complete olayı kaldırılır veya bağlantı erken kapanır | UI-10 ve REST/SSE regresyonu |
| Konuşma verisi bozulur/kaybolur | Veri bütünlüğü regresyonu, UI-11, WIN-08 |
| Windows HTTP 200 döndürür fakat profil/anahtar kontrolü başarısızdır | WIN-01, WIN-06, smoke kabul kontrolü |
| App/core/commit/release kimlikleri uyuşmaz | WIN-09, sürüm ve artifact gate |
| Bir zorunlu test skipped/blocked iken yayın adımı çalışır | Release gate negatif testi |

Bu yaklaşımın amacı yüzde 100 satır kapsamı iddia etmek değil, kritik işlev ve risk senaryolarının kapsandığını ve testlerin yanlış başarı üretmediğini kanıtlamaktır. Her yeni düzeltilen hata için mümkün olduğunda önce hatayı yeniden üreten regresyon testi eklenir.

## 10. Uygulama sırası ve tamamlanma kriteri

Öncelik sırası:
1. Mevcut testleri gerçek kapsamlarına göre sınıflandır; kritik yanlış başarı kontrollerini düzelt.
2. Native motoru gerçekten çalıştıran kontrollü entegrasyon testlerini kur; P0/P1/P2 hata senaryolarını deterministik kapsa.
3. Playwright tarayıcı E2E senaryolarını ekle; kaynak metni testlerini işlevsel testlerle tamamla/değiştir.
4. Windows temiz kurulum, upgrade, rollback ve paketli uygulama kabulünü kur.
5. Canlı OpenRouter Free kabulünü güvenli şekilde çalıştır; ücretli profil testleri için gerekli izin/bütçe ayrı değerlendirilir.
6. Tek sürüm kaynağı, commit/artifact kimliği ve zorunlu release gate'i bağla; negatif testlerle atlanamadığını kanıtla.

Tamamlanma ölçütleri:
- [ ] Güncel commit, gerçekten kurulu Windows release ve motor sürümü karşılaştırıldı.
- [ ] Gerçek Amiable üç aşama, model erişimi ve Free sıfır ücretli çağrı/maliyet kabulü kanıtlandı.
- [ ] Controlled Integration ve Browser E2E testleri gerçek işlevleri ve hata durumlarını kapsıyor.
- [ ] Windows temiz kurulum, eski sürümden yükseltme, rollback, yeniden açma ve paketli gerçek Council kabulü geçti.
- [ ] Sürüm kaynaklarının tutarlılığı ve test edilen artifact'in yayınlananla aynı olması doğrulandı.
- [ ] Bilinen kritik kusurlar kontrollü olarak üretildiğinde ilgili testlerin başarısız olduğu gösterildi.
- [ ] Bütün zorunlu kapıların PASS olması şartı release workflow tarafından uygulanıyor; stabilizasyon tamamlanmadan beta yayınlanamıyor.

İlerleme ve kanıtlar PR/CI run/commit/artifact referanslarıyla kayıt altına alınmalıdır. Bu belgeyi tek başına repoya eklemek yukarıdaki kutuların hiçbirini tamamlamaz.

## 11. Uygulama günlüğü

Her oturumda yalnızca sıradaki tek iş ele alınır. Kod değişikliği, doğrulama komutu, sonuç ve bir sonraki iş burada kaydedilir. Çalıştırılmamış test PASS olarak işaretlenmez.

### Aşama 1 — Mevcut test kapsamı ve yanlış başarı kontrolleri

- Kapsam sınıflandırması: Bölüm 2'deki mevcut denetim tablosu başlangıç referansıdır; mock tabanlı API testleri gerçek motor kabulü değildir.
- **İş 1.1 — Windows smoke Settings API false-positive düzeltmesi: TAMAMLANDI / PASS / main'e birleştirildi.**
- Başlangıç commit'i: `e52257fcb0053ad1aceab0915ac0782b76541230`.
- Değişiklik: `.github/workflows/windows-release.yml` artık `apiReady` değerini ancak HTTP 200, geçerli JSON, Free profil ve açıkça boolean false olan API key durumunun tamamı doğrulandıktan sonra true yapar. Doğrulama hatası mevcut retry/catch akışında readiness başarısı üretmez.
- Ortak gerçek kontrol: `scripts/windows/assert-smoke-settings.ps1`. Regresyon: `tests/windows/test-smoke-settings.ps1`; geçerli durum ve HTTP 200 ile yanlış profil/anahtar, eksik alanlar, yanlış tip, bozuk JSON, HTTP hatası olmak üzere sekiz senaryo. Windows workflow bu testi EXE derlemesinden önce çalıştırır.
- Doğrulama komutu: `pwsh -NoProfile -File tests/windows/test-smoke-settings.ps1`.
- Normal doğrulama: **PASS** — [Windows CI #45386758241](https://github.com/Kallone62/llm-council-web/actions/runs/45386758241) içinde sekiz regresyon senaryosu, EXE derleme ve paketli smoke geçti; [CI #45386758252](https://github.com/Kallone62/llm-council-web/actions/runs/45386758252) Pytest ve frontend build geçti. Her ikisi `a6660a80ac479461a00127fbd865f8f34570a493` için çalıştı. Linux sandbox'ta `pwsh` yok, dış ağ DNS çözümlemesi başarısız; gerçek PowerShell doğrulaması Windows runner'da yapıldı. Bu test gerçek kurulum/yükseltme veya tüm WIN-01/WIN-06 kabulü değildir.
- Mutation doğrulaması: `pwsh -NoProfile -File tests/windows/test-smoke-settings-mutation.ps1`. Değişmemiş regresyon testinin önce doğru kodda PASS, sonra geçici kopyada profil ve anahtar korumaları ayrı ayrı kaldırılınca ilgili beklenen assertion'da FAIL, son olarak doğru kod geri gelince PASS olması zorunludur. Üretim kodu değiştirilmez. Sonuç: **PASS** — [Windows CI #45387139925](https://github.com/Kallone62/llm-council-web/actions/runs/45387139925), commit `17a0d9a855074c61bfc511e5b404c16d01478e2f`. Normal regresyon, iki ayrı mutation'ın beklenen hatada yakalanması, doğru kodla tekrar PASS, EXE derleme, paketli smoke ve ZIP üretimi geçti. Yayın adımı atlandı. Regresyon dosyasının blob SHA'sı `fb899e011b5ad62fc842c304050813f6bfdfe339` değişmedi; başarı ölçütleri gevşetilmedi. [CI #45387140037](https://github.com/Kallone62/llm-council-web/actions/runs/45387140037) aynı commit için Pytest ve frontend build PASS.
- Kanıt: [uygulama commit'i `2df1409`](https://github.com/Kallone62/llm-council-web/commit/2df1409016ba497c5946d9e8428223b94cd91b59), [PR #22](https://github.com/Kallone62/llm-council-web/pull/22), dal `test-architecture/step-1-smoke-settings`. Normal ve mutation CI kanıtları yukarıda. PR #22 doğrulanmış head SHA ile main'e birleştirildi: [merge commit `b849ae2`](https://github.com/Kallone62/llm-council-web/commit/b849ae2576a219be625a6cab7a669ba5eb4ca618).
- **Sıradaki iş: İş 1.2 — Aşama 1'de kalan yanıltıcı başarı kontrollerinin kapsamını belirle ve sıradaki tek kritik kontrolü düzelt.** Önce mevcut testleri ve ilgili uygulama davranışını incele, test başarısızsa kök nedeni tespit et; üretim kodunu düzelt, test beklentisini sırf PASS elde etmek için gevşetme. Sandbox mümkünse gerçek testleri çalıştır; gerekli platform/bağımlılık mevcut değilse bunu belirt ve uygun CI runner ile doğrula. Normal ve negatif doğrulamalar geçtikten sonra main'e birleştir, kanıtları ve sonraki işi kaydet. İş 1.2 aşağıda yürütülmektedir; Aşama 2 başlatılmadı.

Beta yayını yapılmadı; mevcut release ve ürün özellikleri bu işin kapsamı dışındadır.

### İş 1.2 — Başarısız Chairman yanıtlarının konuşma hafızasına girmesi

- Durum: **TAMAMLANDI / PASS / main'e birleştirildi.** Başlangıç main commit'i: `654f770ab5f996d6bd054371466f8f85397c3b6e`.
- Kök neden: `_completed_exchanges` sadece boş olmayan Stage 3 metnine bakıyordu; `error_status` içeren sağlayıcı hata metinlerini tamamlanmış karar olarak alıyordu. Eski özetlerde de bu metinler ve yanlış turn offset'leri kalabiliyordu; yalnız `covered_turns` sayısını kontrol etmek yeterli değildi.
- Çözüm: `backend/conversation_context.py` hata durumlu Chairman yanıtlarını dışlar; `MEMORY_VERSION = 2` ile eski sürümsüz özetleri kullanmaz. Geçerli kısa geçmiş doğrudan kullanılır; uzun geçmiş normal özetleme yoluyla başarılı turlardan yeniden oluşturulur. Yeni özetler tekrar kullanılabilir. Orijinal mesajlar ve kayıtlar değiştirilmez. Eski özetin yeniden oluşturulması gerekirse mevcut model/Free guard kuralları geçerlidir; özetleme başarısızsa mevcut hata davranışı korunur.
- Regresyon: `tests/test_failed_round_memory.py` (5 senaryo: error, rate_limited, no_responses, eski cache'in reddi, yeniden oluşturulan temiz cache'in kullanımı). Sandbox'ta orijinal üretim dosyası repo blob SHA `5072549a22b055c3f0bce2143605997ec2058a1b` ile eşleştirildi. Python 3.13.5 / Pytest 9.0.2; ağ DNS erişimi ve Amiable paketi olmadığından yalnız dış model istemcisinin import sınırında hata fırlatan geçici shim kullanıldı. Testlerde harici özetleme zaten kontrollü; gerçek hafıza modülü çalıştı. Bu, native motor veya tam repo entegrasyon testi değildir; gerçek bağımlılıklarla CI zorunludur.
- Komut: `python -m pytest -q tests/test_failed_round_memory.py`. Eski kod **5 FAIL**, düzeltilmiş kod **5 PASS**. Geçici mutation 1 (error_status koruması kaldırıldı): **5 FAIL**; mutation 2 (cache sürüm koruması kaldırıldı): **2 FAIL / 3 PASS**. Doğru kod geri yüklendi: **5 PASS**. Test blob SHA `c8c1667464dc1a6eb870107e93ade93fd231ab7d` değişmedi; düzeltilmiş üretim blob SHA `6baea98384185ffa08d64b15850fb741f91c90b1`.
- Repo doğrulaması: [Python CI #45388050235](https://github.com/Kallone62/llm-council-web/actions/runs/45388050235) **PASS** (gerçek bağımlılık kurulumu, tüm Pytest testleri ve frontend build); [Windows CI #45388050093](https://github.com/Kallone62/llm-council-web/actions/runs/45388050093) **PASS** (önceki regresyon ve mutation kontrolleri, EXE derleme, GUI doğrulaması, paketli smoke, ZIP ve SHA256). Her iki çalışma da `375a5c4f683d105e2db9c2b234026dee81d09e85` commit'i için tamamlandı. Yayın adımı atlandı. Bu sonuç native motorun canlı kabulü değildir.
- Birleştirme öncesi sandbox ile repo üretim/test blob SHA'larının eşitliği yeniden kontrol edildi; test tasarımı değiştirilmedi. [PR #23](https://github.com/Kallone62/llm-council-web/pull/23), [merge commit `58c816b`](https://github.com/Kallone62/llm-council-web/commit/58c816b08656998b1ca0b220de4bc9e99658cf75) ile main'e birleştirildi.
- **Sıradaki iş: İş 1.3 — Boş/whitespace Chairman sentezinin başarılı stage progress ve yanıltıcı kalite metriği üretmesini engelle.** Mevcut native motor sınırını koru; önce hatayı yeniden üreten regresyon testini yaz, ardından kök nedeni düzelt, normal/negatif doğrulamaları ve CI'ı tamamla, test beklentilerini gevşetmeden main'e birleştir. İş 1.3 aşağıda yürütülmektedir. Aşama 2 başlanmadı.

### İş 1.3 — Boş Chairman sentezinin yanlış başarı üretmesi

- Durum: **TAMAMLANDI / PASS / main'e birleştirildi.** Başlangıç main commit'i: `02991de3ffdb365c844310e8adc1516703ba32fa`. İş ancak tüm kapsam doğrulandıktan ve main'e birleştirildikten sonra tamamlanır.
- Kök neden: `wrap_stage` Stage 3'te yalnız `error_status` kontrol ediyordu. `repair_quality_metrics` boş/eksik metinde mevcut yanıltıcı metriği bırakıyor, whitespace metni hesaplatıyordu. Cache veya hook'u atlayan sonuçlar da REST/SSE sınırında başarı verdict'i taşıyabiliyordu.
- Çözüm: `backend/engine_runtime.py` geçerli metni ve mevcut native hata durumunu değiştirmeden, boş/whitespace/eksik/metin olmayan sentezi `empty_synthesis` hata durumuyla normalleştirir. Stage 3 progress failed olur; geçersiz sentezin uygulama kalite metriği null olur. `backend/main.py` dönen/cache sonuçlarını da kontrol eder, bu durumda metadata status failed ve verdict kaldırılmış olur. Stage 1/2, native usage, sıralamalar ve ham konuşma kayıtları korunur; yeni retry, native algoritma değişikliği veya frontend yeniden yazımı yoktur. Mevcut Stage3 bileşeni error_status ile hata gösterir. SSE complete taşıma sonudur, Council başarısı değildir; metadata failed ve stage3 error_status taşır.
- Sandbox: Python 3.13.5 / Pytest 9.0.2, GitHub DNS erişimi ve kurulu pinli Amiable yok. Repo orijinal engine_runtime blob SHA `3f4dba4d570e5e5317fcf505a321ababa78154c1` ve free_profile blob SHA `326719f6e89638357ae2e4500d30fa0a1346adf2` birebir eşleştirildi. Sadece native import sınırında fail-closed geçici shim, kontrollü stage ve metric callback kullanıldı. `PYTHONPATH=dependency-shim:. python -m pytest -q tests/test_empty_chairman.py`: orijinal kod **15 FAIL / 3 PASS**, düzeltme **18 PASS**; stage doğrulaması kaldırıldı **10 FAIL / 8 PASS**; kalite doğrulaması eski davranışa döndürüldü **5 FAIL / 13 PASS**; doğru kod geri yüklendi **18 PASS**. Test blob SHA `17bdd1eac6969180ebedfa10e992e46865d446fa` değişmedi; düzeltilmiş engine_runtime blob SHA `db4d2acb7f9e447daecc490ef26422358f260a38`. Shim repoya eklenmedi.
- Ek sözleşme regresyonu: `tests/test_empty_chairman_api.py` (8 senaryo: REST/SSE × doğrudan/cache-benzeri sonuç × boş/whitespace). Stage 1/2, kullanım, sıralama, failed status, boş verdict, null kalite, SSE progress/terminal payload, JSON ve sonraki hafıza birlikte doğrulanır. Bu tam API testleri sandbox'ta pinli bağımlılık eksikliği nedeniyle çalıştırılamadı; gerçek bağımlılıklı Python CI'da tüm Pytest paketiyle birlikte PASS. Native motor/live veya gerçek tarayıcı kabulü iddia edilmez; eski kaydedilmiş hatalı UI kayıtlarının migrasyonu bu işin kapsamı dışında.
- Repo doğrulaması: [Python CI #45389278816](https://github.com/Kallone62/llm-council-web/actions/runs/45389278816) **PASS** (pinli bağımlılıklar, tüm Pytest regresyonları ve frontend build); [Windows CI #45389278825](https://github.com/Kallone62/llm-council-web/actions/runs/45389278825) **PASS** (önceki smoke regresyonları ve mutation, EXE derleme, GUI doğrulaması, paketli smoke, ZIP ve SHA256). İki çalışma da `81748e34255b99c195353dd8cbcc3d78d574cc59` commit'i için tamamlandı. Yayın adımı atlandı. Bu sonuç canlı motor ya da gerçek tarayıcı kabulü değildir.
- Birleştirme öncesi dört dosyanın blob SHA'ları tekrar doğrulandı: `backend/engine_runtime.py` ve `tests/test_empty_chairman.py` yukarıdaki değerlerle; `backend/main.py` `ee8cb12000ac355175f3148b1438ba7318252063`, `tests/test_empty_chairman_api.py` `c03c3f4ed9f7740862656308b97cec04b5a80c8c`. Test beklentileri gevşetilmedi. [PR #24](https://github.com/Kallone62/llm-council-web/pull/24), [merge commit `3496546`](https://github.com/Kallone62/llm-council-web/commit/34965464ec639d52f2799aeba3c3fb6257c75dbe) ile main'e birleştirildi.
- **Sıradaki iş: İş 1.4 — Stage 2 boş/geçersiz değerlendirme false-positive davranışı.** Native algoritmanın sözleşmesini incele; boş/geçersiz değerlendirmeyi yanlışlıkla başarılı gösteren davranışı önce regresyonla kanıtla, kök nedeni düzelt, geçerli değerlendirme ve native davranışı koru, sandbox ve negatif doğrulamaları, CI ve main birleştirmesini tamamla. İş 1.4 aşağıda yürütülmektedir. Aşama 2 başlanmadı. Yeni beta yayınlanmadı.

### İş 1.4 — Stage 2 boş/geçersiz değerlendirmelerin yanlış başarı üretmesi

- Durum: **TAMAMLANDI / PASS / main'e birleştirildi.** Başlangıç main commit'i: `3a47a51cdfa1497c48544f69a9f48c61274d728c`. Kod, negatif test, CI, main birleştirme ve dokümantasyon tek iş olarak tamamlanacaktır.
- Kök neden: Stage 2 wrapper yalnız dönen değerlendirme sayısını başarı sayıyordu. Native aggregate, sıfır etkin oylu adayları da döndürebiliyor; liste bulunması gerçek oy veya konsensüs kanıtı değildir. Native tek model yolu peer review'u bilinçli olarak atlar. Bunların aynı başarı gibi görünmesi hatalıdır.
- Çözüm: Native parsed ballot ve label mapping üzerinden geçerli/tam, kısmi, geçersiz ve abstained değerlendirmeler ayrı sayılır. Stage 2 canlı progress gerçek dönen ballot durumunu bildirir; sonuç sınırında `peer_review` native `vote_count` ile denetlenir. Başarısız/kısmi review, geçerli Chairman sentezi varsa partial oturum olarak belirtilir; single_model skipped, Stage 1 yoksa not_run. Stage 3 hatası korunur. Sıfır kullanılabilir review veya ikiden az etkin oy alan adayla üretilen yanıltıcı kalite metriği kaldırılır. Native aggregate, ham critique, kullanım, Chairman sentezi ve verdict değiştirilmez; verdict'in tam peer-validated olmadığı UI'da açıkça belirtilir. Mevcut algoritma, reviewer seçimleri, self-vote exclusion, model çağrısı ve retry davranışları değişmez. Stage 2 canlı progress, henüz aggregate yapılmadan parsed ballot durumudur; nihai metadata etkin oy durumunun otoritesidir. Eski kayıtlarda peer_review alanı yoksa geçmişe dönük tam sınıflandırma bu işin kapsamı dışındadır; sıfır oy rank gösterimi yine düzeltilmiştir.
- Sandbox: Linux Python 3.13.5 / Pytest 9.0.2. Repo başlangıç engine_runtime blob SHA `db4d2acb7f9e447daecc490ef26422358f260a38` ve free_profile blob SHA `326719f6e89638357ae2e4500d30fa0a1346adf2` eşleşti. Kullanıcının yüklediği Amiable kaynak arşivinde CHANGELOG üst sürümü 0.53.0; oluşturulmamış paket sürüm dosyası yalnız geçici çıkarılmış kopyaya eklendi. Native kaynak gerçek import ve aggregate çağrısı çalıştı; arşivin dağıtılan pinli wheel ile birebir eşliği kanıtlanmadığından CI pinli paket doğrulaması zorunlu. GitHub DNS erişimi yok; sandbox'ta React/Vite paketleri yok.
- Komut: `PYTHONPATH=/tmp/council-native/llm-council-master/src:. python -m pytest -q tests/test_peer_review_status.py`. Aynı 24 testle eski kod **24 FAIL** (progress/kalite davranış hataları ve eksik sonuç sınıflandırma sözleşmesi), yeni kod **24 PASS**. Geçici mutation: eski progress davranışı **10 FAIL / 14 PASS**, etkin oy kontrolü kaldırıldı **4 FAIL / 20 PASS**, kalite guard kaldırıldı **1 FAIL / 23 PASS**. Doğru kod geri yüklendi; önceki 18 Chairman testi dahil **42 PASS**. Test beklentileri mutation sırasında değiştirilmedi. Engine blob SHA `46dcf68c9b2b0b842c153ea80d6f76b17a675781`, test blob SHA `b2f02acdb972675ace84ac672cc19042bd53861d`.
- Ek doğrulamalar: `tests/test_peer_review_api.py` REST/SSE × 8 senaryo, native aggregate ile failed/partial/completed/skipped/cache ve JSON kayıt sözleşmesi; `frontend/tests/stage2-status.test.mjs` gerçek React SSR render ile boş review, raw critique, skipped, partial/zero vote, complete ve Chairman verdict uyarısı. CI mevcut bağımlılıkları kullanır; yeni test framework veya paket eklenmez. Bu 16 API ve 6 render senaryosu sandbox'ta tam repo ve frontend bağımlılıkları yokluğu nedeniyle çalıştırılamadı; pinli bağımlılıklarla GitHub CI'da PASS. Render testi gerçek tarayıcı E2E değildir (Aşama 3).
- Son sandbox denetimi: aynı kaynak ve test SHA'larıyla 42 PASS tekrar doğrulandı. Ayrı geçici kopyada orijinal 24 FAIL, progress mutation 10 FAIL/14 PASS, etkin oy mutation 4 FAIL/20 PASS, kalite mutation 1 FAIL/23 PASS, düzeltilmiş kod 24 PASS. Her çalışmada test blob SHA `b2f02acdb972675ace84ac672cc19042bd53861d` aynı kaldı.
- Repo doğrulaması: [Python ve frontend CI #45392632044](https://github.com/Kallone62/llm-council-web/actions/runs/45392632044) **PASS** (143 Pytest PASS, 6 React SSR render PASS, frontend build PASS). Bir adet Starlette TestClient/httpx deprecation uyarısı var; hata değil ve bu işin kapsamı dışında. [Windows CI #45392632065](https://github.com/Kallone62/llm-council-web/actions/runs/45392632065) **PASS** (installer doğrulaması, önceki smoke regresyonu/mutation, EXE derleme, GUI kontrolü, paketli smoke, ZIP/SHA256 ve artifact yüklemesi); yayın atlandı. İki CI çalışması da `894c06b3f27773a47b7f37df2edd5f7fa6a6c3bb` commit'i için tamamlandı. Bu, canlı motor veya gerçek tarayıcı E2E kabulü değildir.
- Birleştirme öncesi üretim, test ve CI dosyalarının dokuz blob SHA'sı doğrulandı. Ek test SHA'ları: `tests/test_peer_review_api.py` `c5127e404a8db2a71ae6909488bcb429c8ba2312`, `frontend/tests/stage2-status.test.mjs` `6b2b4c73fe132d092cf60a14ee015ea46ce908db`. Test kriterleri gevşetilmedi. [PR #25](https://github.com/Kallone62/llm-council-web/pull/25), [merge commit `32f49c6`](https://github.com/Kallone62/llm-council-web/commit/32f49c6e08959f124d0788feafb7cf9fd6fd56e5) ile main'e birleştirildi.
- **Sıradaki iş: İş 1.5 — Geçersiz/yarım SSE sonlanmasının yanlış başarı kabulü.** Mevcut SSE sözleşmesini incele, eksik/bozuk/yarım akışın başarı sayılmasını önce regresyonla kanıtla, kök nedeni düzelt. Geçerli akış ve gerçek hata durumları korunmalı; sandbox, negatif doğrulamalar, CI ve main birleştirmesi tamamlanmalı. İş 1.5 aşağıda yürütülmektedir. Aşama 2 başlanmadı. Yeni beta yayınlanmadı.

### İş 1.5 — Geçersiz veya yarım SSE akışının yanlış başarı kabulü

- Durum: **Sandbox SSE regresyonu PASS; tam repo ve Windows CI bekleniyor.** Başlangıç main commit'i: `2be43701b488b075baa9ebf9e4116db05f0918f6`. İş ancak doğrulamalar ve main birleştirmesi tamamlandığında kapanır.
- Kök neden: `frontend/src/api.js` JSON ve callback hatalarını aynı catch içinde yutuyor, çerçeve sınırını ve UTF-8 sonunu doğrulamıyor, terminal complete veya zorunlu stage sonuçları gelmeden bağlantının kapanmasını başarıyla çözülmüş Promise sayıyordu. `App.jsx` complete callback'inde success reload bayrağını erken açıyordu.
- Çözüm: Mevcut backend SSE sözleşmesi korunur. İstemci geçerli UTF-8 ve LF/CRLF ile tam SSE frame, doğru sıra ve tekil Stage 1/2/3 sonuçları, payload şekilleri, terminal complete ve yalnız isteğe bağlı son title_complete olayını doğrular. Keepalive ve canlı stage progress korunur. Hatalı/eksik frame, JSON, sıra, terminal veya transport hatası Promise rejection üretir; backend error mesajı aktarılır. Stage 1/2 ve progress erken gösterilebilir; Stage 3/complete/title ancak akışın temiz EOF ile tamamlandığı doğrulanınca iletilir. App success reload yalnız doğrulanmış Promise çözülünce açılır. Council failed/partial metadata taşıyan geçerli tamamlanmış transport hâlâ sonuç olarak işlenir; transport başarısı Council başarısı değildir. Backend, native motor, veri biçimi ve retry politikası değişmedi. Optional title event gecikirse final gösterimi stream EOF'a kadar gecikir; bunun nedeni geç gelen kesik/bozuk akışın yanlış başarıya dönüşmesini engellemektir.
- Sandbox: Node v22.16.0 yerleşik node:test, gerçek `api.sendMessageStream`, gerçek Web Streams/Response/TextDecoder; yalnız fetch sınırında kontrollü response. Orijinal API blob SHA `f24aebd33934983a33d192e9ad93dd07c7c1ef12` eşleştirildi. Komut: `cd frontend && node --test tests/sse-contract.test.mjs`. Test kapsamı 31 senaryo (parçalanmış UTF-8/CRLF, keepalive, geçerli failed/partial sonuç, legacy stage-start, eksik/bozuk/tekrarlı/sırasız frame, eksik terminal, backend error, ağ kopması, UTF-8, content-type/body, callback exception ve reader lock). Aynı testlerle eski kod **28 FAIL / 3 PASS**, düzeltilmiş **31 PASS**. Geçici mutation: terminal guard kaldırıldı **2 FAIL / 29 PASS**; JSON hatası yutuldu **1 FAIL / 30 PASS**; final erken yayımlandı **11 FAIL / 20 PASS**; doğru kod geri yüklendi **31 PASS**. Test blob SHA `7d00b21a50a3f578be4345d3269c8b2ecb1fde0a` değişmedi; düzeltilmiş API SHA `aabea8b3fa8e42840ad623da5424a89e44f73214`. Mutation değişiklikleri repoya taşınmadı.
- CI: yeni bağımlılık veya test framework yok; mevcut CI'ya Node SSE test adımı eklendi. App JSX değişikliği ve önceki tüm backend/render testleri tam CI ile doğrulanacak. Sandbox'ta npm/React bağımlılıkları ve GitHub DNS erişimi yok; gerçek browser E2E ve live motor kabulü bu unit/transport testiyle iddia edilmez.
- Sıradaki işlem: İş 1.5 için Python, React, SSE ve frontend build CI ile Windows paketli smoke doğrulaması; başarısızsa kök nedeni düzelt, test beklentilerini gevşetme. SHA eşleşmesi ve tüm kapsam PASS sonrasında main'e birleştir ve kanıtları kaydet. Aşama 1'in kalan kritik false-positive denetimlerini belirlemeden Aşama 2'ye geçme. Yeni beta yayınlanmadı.

- Ek aynı kapsam denetimi: İlk 31 testin beklentileri korunarak boş/whitespace ve metin olmayan Stage 3 için iki ek test yazıldı. Ara sürümde whitespace regresyonu FAIL oldu; kök neden yalnız string tipinin kontrol edilmesiydi. Geçerli error_status istisnası korunarak boş olmayan string kontrolü eklendi. Nihai 33 testle orijinal main **30 FAIL / 3 PASS**, düzeltilmiş kod **33 PASS**. Dört ayrı mutation (terminal guard kaldırma **2 FAIL / 31 PASS**, JSON hatası yutma **1 FAIL / 32 PASS**, Stage 3 erken yayınlama **13 FAIL / 20 PASS**, whitespace guard kaldırma **1 FAIL / 32 PASS**); doğru kod geri yüklendi **33 PASS**. Test blob SHA `5018def936965171d7a3bdb5f962f6be2c28264e` tüm bu mutation'larda sabit; nihai API blob SHA `f58b375f772ce4f3f1b6f993a8681c2969273369`. İlk 31 test değiştirilmedi. PR #26 mevcut App reload guard'ı ve CI adımı korunur. Son head için bütün CI yeniden geçmeden iş kapatılmaz. 
