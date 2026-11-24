# Repo denetimi — İlgilenilecek konular

Denetim referansı: `c445a64` (Amiable Core 0.53.0, beta.5 sonrası main).
Bu belge gelecekte ele alınacak işleri listeler; hiçbir çözüm uygulanmış veya doğrulanmış sayılmaz. 404 hatasının kesin nedeni henüz bilinmiyor.

## P0 — Kritik

- [ ] **Free profilinin çalışmaması / Stage 1 HTTP 404:** OpenRouter sağlayıcı erişimi, model kimlikleri, hesap limitleri ve uygulama entegrasyonu ayrı ayrı incelenecek. Amiable motoru uygulamadan bağımsız test edilecek. (`backend/free_profile.py:13–20`)
- [ ] **Sabit ücretsiz model listesi:** Dinamik ücretsiz model havuzu, güncel katalog, üç farklı model seçimi, ücretsiz fiyat kontrolü ve uygun alternatif model seçimi değerlendirilecek. 404, 429, 5xx ve timeout durumlarında sağlayıcı hataları ile hesap limitleri ayrıştırılacak. Gereksiz API isteği ve ücretli yönlendirme engellenecek.
- [ ] **Council sırasında ayar değişikliği:** Free olarak başlayan oturumun profil değişikliği nedeniyle ücretli modellere yönelme riski incelenecek. (`backend/main.py:158–195, 305–393, 540–582`)

## P1 — İşlevsel sorunlar

- [ ] **Konuşma hafızasına başarısız cevapların girmesi:** Chairman hata mesajlarının başarılı geçmiş cevaplar gibi aktarılması incelenecek. (`backend/conversation_context.py:27–41`)
- [ ] **Hafızanın tek modele bağımlılığı:** Free özetleme modelinin kullanılamaması nedeniyle uzun konuşmaların Council başlamadan başarısız olması incelenecek. (`backend/conversation_context.py:139–170`)
- [ ] **Stage 2 ve Stage 3 başarı/hata durumları:** Sıfır değerlendirme, boş sentez, kısmi sonuçlar ve Chairman hatalarının doğru gösterilmesi incelenecek. (`backend/engine_runtime.py:139–150`)
- [ ] **Konfigürasyon tutarlılığı:** Python ve YAML içindeki mükerrer model listeleri, Free profilinin model kimliklerine bağımlılığı ve ayarların gerçek çalışma davranışı incelenecek. (`backend/free_profile.py`, `llm_council.yaml`, `backend/config.py`)
- [ ] **Windows güncellemesi:** Eski sorunlu YAML ayarlarının yeni sürüme taşınması ve EXE başlatmanın başarılı kurulum sayılması incelenecek. (`scripts/install.ps1:42–91`)
- [ ] **Windows CI mantık hatası:** API 200 döndüğünde ayarlanan `apiReady` değerinin sonraki Free profil/API anahtarı kontrolleri başarısız olsa bile true kalması incelenecek. (`.github/workflows/windows-release.yml:127–139`)

### P1 uygulama notu (doğrulama bekliyor)

P1 değişiklikleri: başarısız/boş final yanıtlarının hafızadan çıkarılması ve eski özetlerin kaynak özetiyle doğrulanması; Free için sınırlı model alternatifi ve açıkça eksik bağlam uyarısı; gerçek peer-ranking/final durumları ve kısmi sonuçların görünür kılınması; merkezi Free preset uygulaması ve eski Free YAML migrasyonu; YAML doğrulaması; Windows installer readiness/rollback ve CI false-positive düzeltmesi. Regresyon testleri eklendi. P0 katalog/model erişimi ve oturum içi ayar değişikliği, P2 ve gerçek OpenRouter/Windows kabul testleri bu değişiklikle çözülmüş sayılmaz. Yukarıdaki kutular kabul testleri tamamlanmadan kapatılmayacaktır.

## P2 — Veri ve arayüz tutarlılığı

- [ ] **Frontend/backend senkronizasyonu:** Reddedilen isteklerde ekrandaki mesajlarla kaydedilen mesajların farklılaşması ve tamamlanmadan kapanan SSE bağlantılarının yönetimi incelenecek. (`frontend/src/App.jsx:126–261`, `frontend/src/api.js:134–157`)
- [ ] **JSON depolama:** Atomik olmayan yazımlar, bozuk konuşma dosyalarının tüm listeyi etkilemesi ve veri bütünlüğü incelenecek. (`backend/storage.py:68–108`)

## Mimari ve Amiable entegrasyonu

- [ ] Stage 1, Stage 1.5, Stage 2, Stage 3 ve Chairman seçimindeki monkey patch müdahalelerinin gerekliliği incelenecek.
- [ ] Native tier orkestrasyonu, fallback, model bazlı ilerleme, Quality Metrics ve timeout ayarlarının gerçek entegrasyon durumu incelenecek.
- [ ] REST/SSE, konfigürasyon, hafıza ve motor entegrasyonu arasındaki sorumluluk sınırları; geçici yamalar, mükerrer tanımlar ve gereksiz kodlar incelenecek.
- [ ] Karpathy arayüzü, mevcut özellikler ve Amiable'ın dört native profili korunacak; motor yeniden yazılmayacak.

## Test ve sürüm yönetimi

- [ ] Güncel commit, kurulu Windows release ve kullanılan motor sürümü karşılaştırılacak.
- [ ] Gerçek Amiable motorunun üç aşaması, model erişimi ve Free profilinin ücretli çağrı yapmaması doğrulanacak. Yalnızca sahte Council cevaplarına dayalı testler yeterli kabul edilmeyecek.
- [ ] Frontend özellikleri gerçek tarayıcı üzerinden uçtan uca test edilecek; kaynak kodda metin arayan testlerin kapsamı incelenecek.
- [ ] Windows EXE temiz kurulumu, eski sürümden yükseltme ve başarılı gerçek Council oturumu doğrulanacak.
- [ ] `pyproject.toml`, backend `APP_VERSION`, installer, release workflow ve main branch sürüm tutarsızlıkları incelenecek.
- [ ] Stabilizasyon ve kabul testleri tamamlanmadan yeni beta yayımlanmayacak.

Öncelik: Yeni özellik geliştirmek yerine mevcut sistemin çalışma sorunlarının kök nedenlerini tespit etmek, mevcut özellikleri doğrulamak ve gereksiz karmaşıklığı azaltmak.
