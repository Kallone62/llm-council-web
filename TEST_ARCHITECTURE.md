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
- **Sıradaki iş: İş 1.2 — Aşama 1'de kalan yanıltıcı başarı kontrollerinin kapsamını belirle ve sıradaki tek kritik kontrolü düzelt.** Önce mevcut testleri ve ilgili uygulama davranışını incele, test başarısızsa kök nedeni tespit et; üretim kodunu düzelt, test beklentisini sırf PASS elde etmek için gevşetme. Sandbox mümkünse gerçek testleri çalıştır; gerekli platform/bağımlılık mevcut değilse bunu belirt ve uygun CI runner ile doğrula. Normal ve negatif doğrulamalar geçtikten sonra main'e birleştir, kanıtları ve sonraki işi kaydet. İş 1.2 henüz başlatılmadı; Aşama 2 başlatılmadı.

Beta yayını yapılmadı; mevcut release ve ürün özellikleri bu işin kapsamı dışındadır.
