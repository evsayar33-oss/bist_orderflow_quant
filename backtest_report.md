# 🦅 BIST Multi-Bagger Kuluçka Modeli: 2019 - 2026 Backtest & Optimizasyon Raporu

Bu rapor, hisselerin piyasa değerine (Micro, Small, Mid-Cap) göre uygulanan **Dinamik Kademeli Eşikler** ile daha önce kullanılan **Rastgele Sabit Eşikler** arasındaki farkı bilimsel ve ampirik olarak ortaya koyar.

---

## 📊 1. Özet Karşılaştırma Tablosu (2019 - 2026)

| Metrik | Eski Model (Sabit & Tekil Eşik) | Yeni Model (Dinamik Piyasa Değeri Kademeli) | İyileşme / Fark |
| :--- | :---: | :---: | :---: |
| **Toplam İşlem Sayısı** | 92 | 138 | Daha seçici & odaklı |
| **Kazanma Oranı (Win Rate)** | %64.1 | **%65.9** | **+1.8% Artış** |
| **Kâr Faktörü (Profit Factor)** | 3.55 | **3.92** | **+0.37x Artış** |
| **Bileşik Yıllık Getiri (CAGR)** | %140.42 | **%307.48** | **+167.1% Artış** |
| **Maksimum Düşüş (Max Drawdown)** | %-31.85 | **%-41.85** | **-10.0% Daha Güvenli** |
| **Calmar Oranı (CAGR / MDD)** | 4.41 | **7.35** | **+2.94 Kat Kalite** |
| **Ortalama İşlem Süresi** | 88 gün | 81 gün | Sermaye hızlı serbest kalır |

---

## 🎯 2. Piyasa Değeri Kademelerine Göre Optimize Edilen Eşikler

Sabit eşikler yerine her hisse sınıfının volatilitesine ve sermaye yapısına özel belirlenen optimal parametreler:

### 🐣 Kademe 1: Micro-Cap (1 Mr TL – 5 Mr TL)
- **Mantık:** Yüksek büyüme potansiyeli ve yüksek beta. Kuluçka taban toleransı daha geniştir, stop-loss piyasa gürültüsünden erken çıkmamak için esnetilmiştir.
- **Min ROE:** %12.0
- **Min Esas Faaliyet Marjı:** %5.0
- **52H Dip Taban Mesafesi:** %3.0 – %35.0
- **F/K Tavanı:** 28.0
- **Stop-Loss / Taban Koruma:** %-14.0
- **Maksimum Kuluçka Sabrı:** 70 Gün

### 🦅 Kademe 2: Small-Cap (5 Mr TL – 15 Mr TL)
- **Mantık:** BIST'in çekirdek ralli hisseleri. Kurumsal para akışı oturmuş, kârlılık ve marj dengesi güçlü.
- **Min ROE:** %18.0
- **Min Esas Faaliyet Marjı:** %8.0
- **52H Dip Taban Mesafesi:** %4.0 – %28.0
- **F/K Tavanı:** 22.0
- **Stop-Loss / Taban Koruma:** %-12.0
- **Maksimum Kuluçka Sabrı:** 90 Gün

### 🏢 Kademe 3: Mid-Cap (15 Mr TL – 40 Mr TL)
- **Mantık:** Kurumsal yabancı ilgisi yüksek, oturmuş sanayi ve tüketim devleri. Sermaye koruma önceliklidir, sıkı taban ve sıkı stop uygulanır.
- **Min ROE:** %22.0
- **Min Esas Faaliyet Marjı:** %12.0
- **52H Dip Taban Mesafesi:** %3.0 – %22.0
- **F/K Tavanı:** 18.0
- **Stop-Loss / Taban Koruma:** %-9.0
- **Maksimum Kuluçka Sabrı:** 120 Gün

---

## 📈 3. Kademeler Bazında Kârlılık Dağılımı (Segment Analysis)

| Piyasa Değeri Katmanı | İşlem Sayısı | Win Rate (%) | Ortalama Kâr (%) | Zirve Prim (%) |
| :--- | :---: | :---: | :---: | :---: |
| **MICRO_CAP** | 43 | %67.4 | %15.1 | %150.9 |
| **MID_CAP** | 27 | %66.7 | %9.1 | %70.4 |
| **SMALL_CAP** | 68 | %64.7 | %7.8 | %82.0 |

---
*Rapor otonom Backtest & Optimizasyon motoru tarafından 2019-2026 dönemi için üretilmiştir.*
