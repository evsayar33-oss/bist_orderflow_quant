# Adaptive BIST Orderflow Meta-Engine V2

Tamamen **ücretsiz** veriyle çalışır: TradingView public scanner, Yahoo Finance (yfinance) ve isteğe bağlı İş Yatırım takas uç noktası. API anahtarı yoktur, ücretli servis yoktur. Her şey GitHub Actions üzerinde çalışır; bilgisayar gerekmez.

## V1 → V2: ne düzeldi

| V1 sorunu | V2 çözümü |
|---|---|
| Learner tüm evreni ve faktör kolonu olmayan eski satırları öğreniyordu | Point-in-time veri seti: yalnızca o gün kaydedilmiş faktörler + sonradan çözülen etiket (`main.build_dataset`) |
| Havuzlanmış korelasyon, çakışan etiket, leakage | Günlük kesitsel rank IC, Newey-West t, etkin örneklem = gün/ufuk, purge'lu champion/challenger testi |
| 10:35 snapshot (saat yanlılığı, rvol ≈ 0.6) | Kapanış sonrası 18:25 EOD çalışma, giriş **ertesi açılış**, 18:15 öncesi manuel çalışma engelli |
| Takas %0 kapsama, sabit 50 puan | Takas opsiyonel; kapsama ölçülüyor, <%30 ise otomatik devre dışı, 2 haftada bir yeniden deneniyor; seviye değil **değişim** (Δ) kullanılıyor |
| Kolinear 5 "faktör" (regime_fit diğerlerinin toplamı) | 9 ayrı primitif faktör, sektör-nötr z-skor; ağırlık = Ω⁻¹·IC (Grinold-Kahn), negatif IC işareti çevirebiliyor |
| "Dipte birikim" tezi veride ters çalışıyordu | Tez sabit değil: faktör işareti/ağırlığı kanıta göre öğreniliyor (başlangıç prior'ı backtest'ten) |
| Sıralama + sabit eşik → her gün aday | Mutlak kapı: beklenen net getiri = HMM piyasa tahmini + kalibre edilmiş fazla getiri − maliyet > %0.25 |
| T+3 win-rate hedefi | Gerçek işlemin (ATR stop, 1.5R hedef, break-even, 10 gün zaman bariyeri) maliyet sonrası beklenen getirisi, tarih-kümeli alt güven sınırıyla |
| Günlük değişen rejim etiketi | Sticky Gaussian HMM (XU100 getirisi, volatilite, USDTRY), yalnızca forward filtre, 5 günlük ileriye dönük piyasa tahmini |
| Backtest canlı modeli test etmiyordu, düzeltilmemiş fiyat, maliyet yok, sahte MDD | Backtest **canlı `score_snapshot` fonksiyonunu** çağırıyor; düzeltilmiş fiyat, maliyet, tavan açılışı, portföy limitleri, günlük MTM özsermaye eğrisi |
| Bedelsiz bölünmeler sahte −%50 | ±%10 limit + TradingView `change` ile kurumsal işlem oranı tespiti ve geriye dönük düzeltme; açık pozisyon seviyeleri ölçekleniyor |
| Guard sayaçları sıfırlanmıyor, test kopya fonksiyonu test ediyordu | Ardışık sayaçlar, test üretim `transition()` fonksiyonunu çalıştırıyor, SAFE kilitlenmesi yok |
| Sentetik T0xx satırları üretim verisine karıştı | Self-test yalnızca geçici klasörde çalışır; `./data`'ya yazamaz |
| Lifecycle kapanmıyordu | Ledger = etiket simülasyonuyla birebir aynı kural (test edildi: fark 0) |

## Günlük akış (18:25 TR)
1. EOD snapshot → doğrulama → tatil/donmuş veri kontrolü → kurumsal işlem tespiti
2. HMM rejim tahmini (yfinance)
3. Etiketleri çözülen geçmiş kesitlerle öğrenme (champion/challenger, rollback) ve kalibrasyon
4. Ledger: dünkü sinyallerin açılıştan girişi, stop/hedef/zaman çıkışları
5. Autonomy guard → maruziyet
6. Yarın açılış için adaylar + pozisyon büyüklüğü (%1 risk / stop mesafesi) → Telegram

## Kurulum (Android telefondan)
1. Zip'i telefonda **Files by Google** ile açıp "Ayıkla" deyin.
2. Tarayıcıda GitHub reponuzu açın (Chrome menüsünden "Masaüstü sitesi" açmak kolaylaştırır).
3. Repo ana sayfasında **Add file → Upload files** → kök klasördeki tüm `.py` dosyalarını, `requirements.txt`, `README.md`, `README_TR.md` dosyalarını seçin → **Commit changes**. Aynı isimli dosyaların üzerine yazılır.
4. Repoda `.github/workflows` klasörüne girin → **Add file → Upload files** → zip'teki 3 adet `.yml` dosyasını seçin → Commit.
5. `TELEGRAM_TOKEN` ve `CHAT_ID` secrets zaten tanımlı; değişiklik gerekmez.
6. **Actions** sekmesi → "Walk Forward Backtest (research prior)" → **Run workflow**. (Bir kez; 20–60 dk sürebilir.) Bu, canlı öğrenmenin başlangıç prior'ını ve kalibrasyonunu üretir.
7. İsteğe bağlı: "Weekly Audit & Self-Test" → Run workflow ile sistem sağlık testini çalıştırın.
8. Günlük çalışma hafta içi 18:25'te otomatik başlar. Veriler `data/` klasöründe tutulur.

**Silinebilecek eski dosyalar (isteğe bağlı, artık kullanılmıyor):** `gecmis_veri.csv`, `signals_log.csv`, `signals_lifecycle.csv`, `longterm_ai_state.json`, `model_weights.json`, `backtest_report.json`, `backtest_report.md`, `VERIFY_RESULTS.txt`, `VERIFY_RESULTS.json`, `apply_guard_patch.py`. Silmeseniz de sistem onları okumaz.

## Önemli dürüstlük notları
* "Orderflow" faktörleri günlük bar ve hacimden türetilmiş **proxy**'lerdir. Gerçek aracı kurum/takas akışı ücretsiz ve güvenilir bir API ile alınamıyor; takas uç noktası çalışırsa otomatik devreye girer.
* Backtest evreni bugünün likit hisseleridir (survivorship bias); rapor bunu `limitations` alanında belirtir.
* Sistem getiri garanti etmez. Canlı OOS IC, ledger sonuçları ve haftalık denetim raporu modelin gerçekten işe yarayıp yaramadığını gösteren tek ölçüttür.

## Parametreler
Tüm ayarlar `config.py` içindedir (maliyet %0.50 gidiş-dönüş, stop 2×ATR [%4–12], hedef 1.5R, 10 seans, maks. 12 açık pozisyon, günlük maks. 8 yeni giriş).
