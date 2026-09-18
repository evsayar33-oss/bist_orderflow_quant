# Adaptive BIST Orderflow Meta-Engine V1

Bu sürüm mevcut proje üzerine kurulur ve üretim yolunda sahte piyasa verisi üretmez.

## Çalışma mantığı

1. TradingView piyasa snapshot'ı alınır.
2. Veri bütünlüğü doğrulanır; kritik veri bozuksa sinyal üretimi durur.
3. Takas verisi ayrı bir kaynak güveniyle işlenir; veri yoksa sahte foreign/HHI değeri yazılmaz.
4. Flow/activity/absorption/structure özelliklerinden Pre-Move ve Flow skorları oluşturulur.
5. BIST rejimi ve rejim güveni hesaplanır.
6. Market-relative resilience ve overnight risk hesaplanır.
7. Rejim önceliği ile öğrenilmiş ağırlıklar kontrollü biçimde birleştirilir.
8. Yeterli veri kalitesi ve risk koşulu sağlanırsa aday üretilir.
9. Sinyal sonucu gerçek gelecekteki işlem günleri üzerinden T+1/T+3/T+5/T+10 olarak çözülür.
10. Learner, Champion/Shadow model yaklaşımıyla ağırlıkları sınırlı şekilde günceller; daha kötü shadow model canlıya alınmaz.
11. Promotion sonrasında anlamlı performans bozulması görülürse rollback yapılır.
12. Aylık backtest gerçek tarihsel veri yoksa fail-closed davranır; sentetik veri kullanılmaz.

## GitHub Actions

`daily_scan.yml`: Hafta içi 10:35 Türkiye saati hedefi.

`daily_audit.yml`: Hafta içi 17:45 Türkiye saati hedefi.

`backtest_optimization.yml`: Aylık gerçek veri walk-forward backtest.

GitHub Actions scheduler dakika kesinliği garanti etmez; workflow hedef zamandan sonra gecikebilir.

## Kurulum

Repo içeriğini GitHub repository köküne yükleyin ve mevcut `TELEGRAM_TOKEN` ile `CHAT_ID` secrets değerlerini koruyun. İlk canlı çalışmada eski `signals_log.csv` / `signals_lifecycle.csv` şemaları yeni V1 şemasına uygun değilse öğrenmeye dahil edilmez ve yeni kayıtlarla yeniden oluşturulur.

Yerel sağlık kontrolü:

```bash
python main.py --self-test
```
