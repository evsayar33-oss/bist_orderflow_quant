# BIST Reel Getiri Motoru V3 (uzun vadeli "al-unut")

**V3.6 — Toplam hedef + panel düzeltmesi:**
* **Hedef:** Hedef artık tek tek ölçütler değil, **toplamları + %3**: TÜFE + dolar (+%3 ABD enflasyonu) + gram altın + mevduat. Örneğin %29,7 + %22,7 + %22,7 + %29,8 + %3 = **%107,9**.
* Bu hedef dört yerde kullanılır:
  * hisselerin sıralaması,
  * "hedefi geçme olasılığı" kalibrasyonu,
  * backtest'te "hedefi geçti" ölçümü,
  * strateji laboratuvarının puanı.
* **Giriş tabanı:** Bir hisse alınmadan önce en güçlü tek alternatifi (örn. mevduat) en az %3 geçmesi beklenir. Bunu geçemeyen hisse yerine o alternatifi tutmak zaten daha iyidir. Tabanı geçen adaylar, toplam hedefe en yakın beklentiden başlayarak seçilir.
* Eski kurala dönmek için Actions ortamına `BOQ_HURDLE_MODE=max` eklenebilir.
* **Panel düzeltmesi:** "Strateji" kartındaki `AttributeError` çökmesi giderildi. Canlı strateji artık `active_strategy` anahtarında tutulur ve eski durum dosyaları da sorunsuz okunur.

**V3.5 — Strateji laboratuvarı:** Backtest artık 72 farklı portföy kuralını gerçek veride dener: hisse sayısı (5/8/12), alım ve tutma eşikleri, ağırlıklandırma, giriş kapısı ve varlık rotasyonu (hisse / altın / mevduat). Her yıl yalnızca o yıldan önceki verilerle en iyi kural seçilir; yani seçim geriye bakarak yapılmaz. Bu seçim yöntemi varsayılan kuralları geçerse kazanan kural `data/strategy_config.json` dosyasına yazılır ve canlı sistem onu kullanır. Geçemezse varsayılan kurallar kalır. Rotasyonda altın seçilirse Telegram "gram altın / ALTINS1" der.

**V3.4 — Çok ölçütlü çıta:** Bir hissenin 12 ayda yalnızca TÜFE'yi değil, **hepsini** geçmesi beklenir: TÜFE, dolar (USDTRY değişimi + %3 ABD enflasyonu), gram altın (TL) ve TL mevduat/para piyasası. Bunlardan en yükseği "çıta" olur; aday, çıtayı en az %3 farkla geçmeyi beklemelidir. BIST100 ikincil ölçüt olarak raporlanır. Arayüz ve Telegram mesajları sadeleştirildi.

**Amaç:** 12 ay içinde **TÜFE'yi yenmesi** beklenen BIST hisselerinden oluşan, ayda bir gözden geçirilen düşük devirli bir portföy. İkincil ölçüt: XU100'e göre fazla getiri.
**Tamamen ücretsiz:** TradingView public scanner, Yahoo Finance (yfinance), İş Yatırım mali tablo uç noktası, TCMB EVDS (ücretsiz anahtar, isteğe bağlı) ve FRED (anahtarsız) TÜFE verisi. Her şey GitHub Actions'ta çalışır.

## Nasıl çalışır
**Her gün 18:25 (kapanış sonrası)**
* Önceki kararın emirleri bugünün açılışında uygulanır, portföy kapanış fiyatına göre değerlenir (düzeltilmiş günlük değişimle; bedelsiz bölünmeler sahte zarar üretmez).
* **Düşüş kontrolü (V3.2):** Zirveden %35 ya da girişten %30 düşüşte pozisyon **bayraklanır**; aylık gözden geçirmede skor da alım eşiğinin altına inmişse (tez bozulmuşsa) satılır, tez sağlamsa tutulur. Girişten %50 düşüşte koşulsuz satış. Kısa vadeli ATR stopu **yoktur**.
* **Nakit:** Boştaki nakit TL para piyasası fonunda varsayılır: TCMB ağırlıklı ortalama fonlama maliyeti (EVDS `TP.APIFON4`) − 2 puan, %15 stopaj sonrası.
* **Maruziyet:** Varsayılan tam yatırım; guard (WATCH %90, RECOVERY %75) ve rejim yalnızca kırpar (en az %80).
* Portföy değeri (NAV), XU100 ve TÜFE kaydı tutulur. Telegram'a yalnızca bir olay olduğunda mesaj gider.

**Seans içinde / tatilde elle çalıştırma = YENİLEME modu (V3.3)**
* Actions → "Daily Run" 18:15'ten önce ya da seans olmayan bir günde çalıştırılırsa işlem ve NAV kaydı yapılmaz. Bunun yerine TÜFE, nakit faizi ve rejim güncellenir.
* TÜFE olmadan verilmiş bir gözden geçirme varsa, son tamamlanan seansın kapanış verisiyle yeniden yapılır.

**Her ayın ilk seansı (aylık gözden geçirme)**
1. Faktörler:
   * **Fiyat:** 12-1 ay momentum, 52 hafta zirvesine yakınlık, momentum istikrarı, düşük oynaklık, düşük beta, düşüş direnci, likidite.
   * **Temel:** ROE, kâr getirisi (E/P), defter getirisi (B/P), satış getirisi, faaliyet marjı, düşük borç, temettü, reel büyüme (enflasyon üstü satış büyümesi).
2. Geçmiş aylık kararların 12 aylık sonuçları çözülür: nominal getiri, **reel (TÜFE'den arındırılmış)** getiri ve XU100 farkı.
3. Öğrenme: aylık kesitsel IC, Newey-West (11 gecikme), purge'lu champion/challenger, Ω⁻¹·IC ağırlıklandırma, araştırma prior'ına Bayesçi büzülme.
4. Kalibrasyon: skor diliminden **beklenen 12 aylık reel getiri** ve **TÜFE'yi yenme olasılığı** hesaplanır.
5. Karar kuralları:
   * **AL:** Skor ≥ kalibre edilmiş eşik (varsayılan p85) **ve** beklenen reel getiri ≥ %3 **ve** yeterli likidite **ve** temel veride bozulma yok. Sektör başına en fazla 3 hisse, toplam hedef 12 hisse.
   * **TUT:** Skor p60'ın üzerinde kaldıkça pozisyon korunur. Eşikler arasındaki bu boşluk kazananların yıllarca portföyde kalmasını sağlar.
   * **SAT:** Skor p60'ın altına düşerse (tez zayıfladı) ya da şirket hem zarar edip hem de ROE'si negatife dönerse satılır.
   * **Ağırlık:** Ters oynaklıkla dağıtılır, hisse başına en fazla %15. Toplam maruziyeti guard ve rejim belirler; kalan kısım nakitte bekler.

## Kurulum (Android)
1. Zip'i **Files by Google** ile açıp "Ayıkla" deyin.
2. GitHub'da **Add file → Upload files** ile kök klasördeki tüm `.py` dosyalarını, `requirements.txt` ve README dosyalarını yükleyin. Aynı isimli V2 dosyalarının üzerine yazılır.
3. `.github/workflows` klasörüne girip 3 `.yml` dosyasını yükleyin.
4. **(Şiddetle önerilir, ücretsiz)** TÜFE verisi için **evds3.tcmb.gov.tr**'de ücretsiz üye olun → **Profilim → API Key Kopyala**. Anahtarı repoda **Settings → Secrets and variables → Actions → New repository secret** yoluyla `EVDS_API_KEY` adıyla ekleyin. Anahtar yoksa sistem sırasıyla FRED, DBnomics ve önbelleği dener. Hiçbiri çalışmazsa USDTRY vekilini (+5 puan güvenlik payıyla) kullanır; vekil de yoksa **yeni alım yapmaz**.
5. **Actions → "Walk Forward Backtest" → Run workflow**. Bu adım 30–90 dakika sürebilir. 2012'den bugüne gerçek veriyle araştırma prior'ını ve kalibrasyonu üretir. **Canlı sistem bu dosya olmadan da çalışır, ancak ilk alımlar çok daha temkinli olur.**
6. Günlük çalışma otomatiktir. İlk çalıştırmada aylık gözden geçirme hemen yapılır ve alımlar ertesi günün açılışında gerçekleşir.

**Artık kullanılmayan V1/V2 dosyaları (isteğe bağlı silebilirsiniz):** `data/snapshots_eod.csv`, `data/signals_ledger.csv`, `data/engine_state.json`, `data/research_prior.json`, `data/backtest_report.json`, `gecmis_veri.csv`, `signals_log.csv`, `signals_lifecycle.csv`, `longterm_ai_state.json`, `model_weights.json`, `backtest_report.*`, `VERIFY_RESULTS.*`.

## TÜFE verisi
Sistem kaynakları şu sırayla dener: `data/cpi_manual.csv` (isterseniz kendiniz yükleyebilirsiniz; format: `tarih,cpi`) → EVDS → FRED → önbellek. TÜİK 2025'te TÜFE'yi 2025=100 bazına taşıdı; sistem eski `TP.FG.J0` ile yeni `TP.TUKFIY2025.GENEL` serisini otomatik birleştirir. TÜİK seri kodunu yine değiştirirse yeni kodu `EVDS_CPI_SERIES` ortam değişkeniyle ekleyebilirsiniz. Kaynaklar birbirine oran eşlemesiyle eklenir (splice). Enflasyon verisi hiçbir zaman uydurulmaz: yayımlanmamış bir ay için reel getiri boş bırakılır.

## Dürüstlük notları
* Hiçbir sistem "her hisse enflasyonu yenecek" garantisi veremez. Ölçülen ve raporlanan başarı ölçütleri şunlardır:
  * 12 aylık pencerelerin yüzde kaçında portföy TÜFE'yi yendi,
  * kapanan pozisyonların yüzde kaçı TÜFE'yi yendi,
  * XU100'e göre fark.
* Backtest bugünün likit hisseleriyle yapılır (survivorship bias). Temel veri geçmişinin kapsamı raporda gösterilir. Nakitte bekleyen para modelde faiz kazanmaz (muhafazakâr varsayım).
* Canlı öğrenme için 12 aylık sonuçlar gerekir. Bu yüzden ilk yıllarda araştırma prior'ı baskın olur ve canlı kanıt biriktikçe ağırlık canlı veriye kayar.

## Ayarlar
Tüm parametreler `config.py` içindedir: ufuk (`HORIZON_MONTHS`), hedef pozisyon sayısı, AL/TUT eşikleri, minimum beklenen reel getiri, felaket stopu seviyeleri, işlem maliyeti (%0.50 gidiş-dönüş).
