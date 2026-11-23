# bist_orderflow_quant: Veri önce, strateji sonra

Bu eklenti **araştırma amaçlıdır**; mevcut işlem stratejileri, PAPER veri
kayıtları, immutable deney saatleri, prod gate, Telegram ve broker ayarlarına
dokunmaz. Her gün Actions ile kendi mevcut kaynak kanıtını okur.

**Asıl veri kaynağı:** historical variants + 2017-2026 backtest + günlük sinyal defteri.

**Repo içi ekonomik mantık:** BIST uzun vadeli reel getiri, sektör gücü, kalite/değerleme, likidite, maliyet ve tarihsel PIT evren duyarlılığı.

**Bloke terfi kriteri:** Mevcut üyelerden çıkarılan yüksek CAGR, delist/PIT doğrulaması ve bağımsız OOS olmadan alpha sayılmaz.

Gösterge/olay tekil fikirleri ve yalnızca ekonomik olarak anlamlı tamamlayıcı
eşleştirmeler, sürümü sabitlenmiş quant_superlab araştırma kataloğundan
çalıştırılır. Şimdilik bu kombinasyonlar yalnız tasarım kuyruğudur, **gerçek
strateji backtestleri değildir**. Kaynak verinin doğrudan doğrulanmış
olgunlaşmış sonuçları varsa yerel çalışmada ölçülür; yoksa hiçbir kazanç
veya kazanma oranı uydurulmaz.

**Kanıt saklama:** `research/strategy-mesh-evidence` ayrık dalında
`observations/strategy_mesh/observations.jsonl`; SHA-256 zinciri,
as-of zaman damgası, veri kaynak SHA'ları ve benzersiz verinin ayıklanması.
Aynı veriyi tekrar çalıştırmak yeni örnek yaratmaz. Değişen aynı-tarihli
veri ayrı revizyon sayılır; eski sonuçlar üzerine yazılmaz. Kaynak zamanının
geri sarılması hata üretir. Ayrıca 90 gün tutulan Actions JSON ve Markdown
raporları oluşur.

**Araştırmanın sıradaki aşaması:** repo içinde gerçekten yeterli gerçek
gözlem biriktikten sonra ilk gözlem anında sabitlenen sinyal ailesi,
kontrol grubu, tarihsel PIT ve risk/maliyet testleri, zaman sıralı bağımsız
OOS, gerçekçi fill ve ayrıca bağımsız forward değerlendirilmeli.
Başarı koşulları repo ekonomisine göre ayrı kalacak. Ancak o zaman
önce bağımsız PAPER değerlendirilebilir. Bu iş akışının kendisi asla
PAPER_ACCEPT, LIVE_ACCEPT, kurumsal alpha veya emir üretmez.

**Teknik güvenlik:** Üretim dosyalarını değiştirmez; yalnız kendi
reposunda ayrık araştırma dalına ve kendi Actions artifact'ına veri yazar.
Araştırma kodu `quant_superlab` kaynak commit'ine pinli:
`811c04aeca2f2634bfe0f3d07be9c4b8bf480090`. Diğer repo API'larına veya özel kaynaklara yazma yetkisi yoktur.
`Actions > Native data-first strategy research (NO ORDERS)` üzerinden
`workflow_dispatch` ile manuel başlatılabilir; günlük cron best effort.
