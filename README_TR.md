# BIST Orderflow — 6 Ay Win Rate / Backtest UI Fix V1

Bu paket mevcut `bist_orderflow_quant` snapshot'ı temel alınarak hazırlanmıştır.

## Düzeltilen sorunlar

1. Streamlit `6 Aylık Win Rate` kartı artık `win_rate_6m` alanını gerçek forward outcome'dan okur.
2. 6 aylık sonuç tanımı: **sinyal tarihinden sonraki 126 işlem günü kapanış getirisi** (`ret_t126`).
3. Çözülmemiş 6 aylık sinyaller 0% veya zarar olarak sayılmaz. UI bunları `Hazırlanıyor`/Warmup olarak gösterir.
4. `gecmis_veri.csv` artık 120 gün ile kesilmez; 126 işlem günlük forward outcome üretmeye yetecek yaklaşık 430 takvim günü tutulur.
5. Eski `signals_log.csv` dosyaları `ret_t126` kolonu yok diye çöpe atılmaz; eksik kolon geriye dönük uyumlu şekilde eklenir.
6. `signals_lifecycle.csv` da yeni kolonlar nedeniyle eski kayıtlarını kaybetmez.
7. Akşam audit artık öğrenmeyi full market history tablosu yerine gerçek `signals_log.csv` üzerinden yapar. Böylece market snapshot satırları "denetlenmiş sinyal" gibi sayılmaz.
8. Backtest kartı `backtest_report.json -> oos_metrics.win_rate` değerini öncelikli okur. Rapor henüz oluşmamışsa `Bekliyor` gösterir; sahte 0 üretmez.

## Önemli gerçek durum

Verilen snapshot içindeki `gecmis_veri.csv` yalnızca 2026-08-31 ile 2026-09-21 arasını kapsıyor. Bu nedenle bugün itibarıyla gerçek bir 6 aylık forward örneklem çıkarmak mümkün değildir.

Yeni kod:
- geçmişi artık uzun tutacak,
- her yeni sinyali 126 işlem günü sonra çözümleyecek,
- yeterli sayıda gerçek 6 aylık sonuç oluştuğunda `win_rate_6m` değerini gösterecektir.

Geçmiş verisi olmayan dönemi tahmin ederek bir yüzde üretmez.

## Kurulum

Repo kökündeki aşağıdaki dosyaları `01_REPLACE_FILES` klasöründeki aynı isimli dosyalarla değiştir:

- app.py
- learner_engine.py
- main.py
- longterm_auditor.py
- state_manager.py
- backtest_optimizer.py

`longterm_ai_state.json`, `signals_log.csv`, `signals_lifecycle.csv`, `gecmis_veri.csv` üzerine paket içindeki eski örnek dosyalarla yazmayın.

## Kontrol

```bash
python main.py --self-test
python -m py_compile app.py learner_engine.py main.py longterm_auditor.py state_manager.py backtest_optimizer.py
```

Gerçek OOS backtest:

```bash
python backtest_optimizer.py --start-date 2022-01-01 --save
```

Bu komut başarılı gerçek veri çekerse `backtest_report.json` oluşturur. Streamlit Backtest Win Rate kartı bu raporu otomatik okuyacaktır.

## Site görünümü

Mevcut Streamlit yerleşimi korunmuştur. Bu düzeltmede görsel düzen yeniden tasarlanmamıştır; yalnızca 6 aylık kartın doğru veri kaynağına bağlanması ve backtest kartının sahte `0.0`/`Veri yok` üretmemesi düzeltilmiştir.
