# Batuhan Test Bulguları

## 28 Kasım 2026 — Windows v1.1.0-beta.6

- Test ortamı: kullanıcının gerçek Windows sistemi, beta.6 test prerelease.
- Native motor: `0.53.0+eng31.1.cache1`.
- [Test edilen yayın](https://github.com/Kallone62/llm-council-web/releases/tag/v1.1.0-beta.6).
- Veriler kullanıcının ilk gerçek kullanım gözlemleridir; kontrollü benchmark veya kesin fatura analizi değildir.

### Profil karşılaştırması

| Profil | Bildirilen yaklaşık maliyet (USD) | Chairman (Stage 3) | Cevap kalitesi ve kapsam | Dil uyumu |
| --- | ---: | --- | --- | --- |
| Quick | 0.03 | Haiku 4.5 | İlk test fena değildi; Balanced'a göre daha kapsamlı göründü. | Türkçe soruya İngilizce sonuç bildirildi; profil bazında tekrar doğrulanmalı. |
| Balanced | 0.02 | Haiku 4.5 | Quick'e göre daha az kapsamlı göründü. | Türkçe soruya İngilizce sonuç bildirildi; profil bazında tekrar doğrulanmalı. |
| High | 0.25 | Henüz bildirilmedi | Henüz değerlendirilmedi. | Sorulan dilde, Türkçe cevap verdi. |
| Reasoning | Henüz test edilmedi | — | — | — |

### Gözlemler ve açık sorunlar

1. Quick ilk testte yaklaşık 3 cent, Balanced yaklaşık 2 cent harcadı. Balanced'ın daha düşük maliyetli olması ve Quick'in daha kapsamlı görünmesi dikkat çekti. Her iki oturumda Chairman Haiku 4.5 idi. Tekil gözlemlerden profil hiyerarşisinin genel başarısı veya bir maliyet hatası sonucu çıkarılmaz.
2. High yaklaşık 25 cent harcadı; bildirilen rakamlara göre Quick'in yaklaşık 8,3, Balanced'ın 12,5 katı. Kalite ve maliyet karşılığı henüz ölçülmedi.
3. Türkçe soruya İngilizce sonuç üretilmesi işlevsel dil uyumu sorunudur. High profilinde aynı sorun gözlenmedi. Quick/Balanced için ayrı ayrı hangi aşamada dil sapmasının oluştuğu henüz doğrulanmadı.
4. Native kaynak incelemesi: pin `868adfe6b9a4183b859dd50957fefdd9977ca1f7`, `src/llm_council/council_stages.py` ve `src/llm_council/verdict.py` Chairman promptlarında kullanıcının soru dilini korumayı açıkça zorunlu kılan talimat bulunmadı. Bu olası açıklamadır, kesin kök neden henüz deneyle doğrulanmadı.
5. Önerilen minimal düzeltme adayı: "Respond in the same language as the user's original question, unless the user explicitly requests another language." Bireysel cevap, Chairman sentezi ve kısmi sonuçlarda dil korunmalı; makine tarafından işlenen JSON alanları/verdict değerleri değişmemeli. Ayrı çeviri veya dil algılama servisi önerilmemektedir. Henüz kod değişikliği yapılmadı.

### Açık doğrulamalar

- Aynı soru ve karşılaştırılabilir koşullarda Quick, Balanced, High ve Reasoning profillerini karşılaştırmak; model ve aşama bazında token/maliyet, süre, kapsam, doğruluk ve cevap dilini kaydetmek.
- Dil sorununu profiller ve Stage 1/Stage 3 bazında yeniden üretmek; açıkça başka dil istenen soruları da kontrol etmek.
- Dil talimatı düzeltmesi gerekirse önce regresyon kanıtı, ardından en dar native değişiklik ve doğrulama.

Bu dosya gerçek kullanıcı geri bildirimlerinin günlüğüdür. TEST_ARCHITECTURE.md kabul durumunu değiştirmez; sıradaki planlı iş WIN-01 olarak kalır.
