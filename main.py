import requests
import pandas as pd
import numpy as np
import os
import concurrent.futures
from datetime import datetime
import warnings

from state_manager import load_ai_state, load_lifecycle_signals, LIFECYCLE_LOG_FILE
from longterm_auditor import audit_and_calibrate

warnings.filterwarnings('ignore')

GECMIS_DOSYA = "gecmis_veri.csv"

# =============================================================================
# 1. PİYASA, BİLANÇO VE ESAS FAALİYET KÂRI VERİSİ
# =============================================================================

def get_bist_macro_data():
    url = "https://scanner.tradingview.com/turkey/scan"
    payload = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "Value.Traded", "operation": "greater", "right": 5000000}
        ],
        "columns": [
            "name", "close", "open", "high", "low", "volume", "change", "Value.Traded",
            "price_52_week_high", "price_52_week_low", "market_cap_basic",
            "return_on_equity_fq", "price_earnings_ttm", "price_book_fq",
            "Perf.Y", "relative_volume_10d_calc",
            "operating_margin", # 🛡️ ESAS FAALİYET MARJI KALKANI
            "Perf.1M",
            "Perf.W"
        ],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, 450]
    }
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        data = response.json()
        rows = []
        for item in data.get("data", []):
            d = item["d"]
            close_p = float(d[1]) if d[1] is not None else 0.0
            high_p = float(d[3]) if d[3] is not None else close_p
            low_p = float(d[4]) if d[4] is not None else close_p
            
            rows.append({
                "ticker": d[0],
                "close": close_p,
                "open": float(d[2]) if d[2] is not None else close_p,
                "high": high_p,
                "low": low_p,
                "volume": float(d[5]) if d[5] is not None else 0.0,
                "change_%": float(d[6]) if d[6] is not None else 0.0,
                "value_traded": float(d[7]) if d[7] is not None else 0.0,
                "high_52w": float(d[8]) if d[8] is not None else close_p * 1.5,
                "low_52w": float(d[9]) if d[9] is not None else close_p * 0.7,
                "market_cap": float(d[10]) if d[10] is not None else 10000000000.0,
                "roe": float(d[11]) if d[11] is not None else 15.0,
                "pe": float(d[12]) if d[12] is not None else 10.0,
                "pb": float(d[13]) if d[13] is not None else 2.0,
                "perf_y": float(d[14]) if d[14] is not None else 0.0,
                "rvol": float(d[15]) if len(d) > 15 and d[15] is not None else 1.0,
                "oper_margin": float(d[16]) if len(d) > 16 and d[16] is not None else 12.0,
                "perf_1m": float(d[17]) if len(d) > 17 and d[17] is not None else 0.0,
                "perf_w": float(d[18]) if len(d) > 18 and d[18] is not None else 0.0
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"⚠️ Piyasa Verisi Hatası: {e}")
        return pd.DataFrame()

def fetch_single_takas(ticker):
    url = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.YatirimDanismanligi/PiyasaVerileri.aspx/GetHisseTakasData"
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest"}
    try:
        res = requests.post(url, json={"hisseKodu": ticker}, headers=headers, timeout=3)
        if res.status_code == 200:
            data = res.json().get("d", [])
            if data:
                foreign_banks = ["CITIBANK YABANCI", "DEUTSCHE YABANCI", "HSBC YATIRIM", "YATIRIM FINANSMAN", "QNB FINANS"]
                f_ratio = sum([float(x.get("Yuzde", 0) or 0) for x in data if str(x.get("ALAN_ADI")).upper() in foreign_banks])
                return ticker, round(f_ratio, 2)
    except Exception:
        pass
    return ticker, 0.0

def fetch_all_market_data():
    df_market = get_bist_macro_data()
    if df_market.empty:
        return df_market

    takas_results = []
    tickers = df_market['ticker'].tolist()
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_single_takas, ticker): ticker for ticker in tickers}
        for future in concurrent.futures.as_completed(futures):
            takas_results.append(future.result())

    df_takas = pd.DataFrame(takas_results, columns=['ticker', 'foreign_ratio'])
    df_final = pd.merge(df_market, df_takas, on='ticker', how='left')

    range_diff = df_final['high'] - df_final['low']
    clv = np.where(
        range_diff > 0,
        ((df_final['close'] - df_final['low']) - (df_final['high'] - df_final['close'])) / (range_diff + 1e-9),
        0.0
    )
    df_final['foreign_ratio'] = pd.to_numeric(df_final['foreign_ratio'], errors='coerce').fillna(0.0)
    mask_f = (df_final['foreign_ratio'] == 0.0)
    df_final.loc[mask_f, 'foreign_ratio'] = np.round(np.abs(clv[mask_f] * 28.0 + 20.0), 2)
    df_final['tarih'] = pd.Timestamp.now().normalize()
    return df_final

# =============================================================================
# 2. PİYASA DEĞERİ KADEMELERİ VE DİNAMİK EŞİK FONKSİYONU
# =============================================================================

def get_bist_tier_thresholds(mcap, thresholds):
    """
    Hissenin piyasa değerine göre (Micro, Small, Mid-Cap) dinamik ve optimize edilmiş
    gösterge eşiklerini döner.
    """
    tiers = thresholds.get("market_cap_tiers", {})
    if not tiers:
        return {
            "tier_name": "GENEL",
            "min_roe": thresholds.get("min_roe", 18.0),
            "min_oper_margin": thresholds.get("min_oper_margin", 6.0),
            "min_dist_from_52w_low": thresholds.get("min_dist_from_52w_low", 4.0),
            "max_dist_from_52w_low": thresholds.get("max_dist_from_52w_low", 28.0),
            "ideal_pe_max": 12.0,
            "acceptable_pe_max": 20.0,
            "stop_loss_pct": thresholds.get("macro_stop_loss_pct", -12.0),
            "target_cup_min": 50.0,
            "max_patience_days": 90
        }

    if mcap < 5_000_000_000:
        cfg = tiers.get("micro_cap", {})
        tier_name = "MICRO_CAP"
    elif mcap < 15_000_000_000:
        cfg = tiers.get("small_cap", {})
        tier_name = "SMALL_CAP"
    else:
        cfg = tiers.get("mid_cap", {})
        tier_name = "MID_CAP"

    return {
        "tier_name": tier_name,
        "min_roe": cfg.get("min_roe", 14.0),
        "min_oper_margin": cfg.get("min_oper_margin", 5.0),
        "min_dist_from_52w_low": cfg.get("min_dist_from_52w_low", 3.0),
        "max_dist_from_52w_low": cfg.get("max_dist_from_52w_low", 32.0),
        "ideal_pe_max": cfg.get("ideal_pe_max", 16.0),
        "acceptable_pe_max": cfg.get("acceptable_pe_max", 26.0),
        "stop_loss_pct": cfg.get("stop_loss_pct", -14.0),
        "target_cup_min": cfg.get("target_cup_min", 45.0),
        "max_patience_days": cfg.get("max_patience_days", 75)
    }

# =============================================================================
# 3. REJİM VE KADEME DUYARLI QUANT PUANLAMA MOTORU
# =============================================================================

def calculate_quant_scores(df, df_gecmis, state):
    if df.empty:
        return df

    thresholds = state.get("thresholds", {})
    min_mcap = thresholds.get("min_market_cap_tl", 1000000000)
    max_mcap = thresholds.get("max_market_cap_tl", 40000000000)

    # 1. BIST GENEL PİYASA REJİMİ TESPİTİ
    market_perf_median = float(df['perf_1m'].median())
    if market_perf_median >= 0.0:
        market_regime = "BOĞA / GENİŞLEME"
        # Boğada hacimli kırılımlar ödüllendirilir
        weights = {"macro_base": 0.35, "growth_quality": 0.25, "stealth_accumulation": 0.20, "volume_ignition": 0.20}
    else:
        market_regime = "AYI / DURGUNLUK"
        # Ayıda hacim tuzaklarına karşı DEFANS MODU (Temel Kârlılık %40'a çıkarılır)
        weights = {"macro_base": 0.35, "growth_quality": 0.40, "stealth_accumulation": 0.20, "volume_ignition": 0.05}

    state["market_regime"] = market_regime

    scored_data = []

    for idx, row in df.iterrows():
        item = row.to_dict()
        close = float(item.get('close', 0.0))
        high = float(item.get('high', close))
        low = float(item.get('low', close))
        change = float(item.get('change_%', 0.0))
        rvol = float(item.get('rvol', 1.0))
        f_ratio = float(item.get('foreign_ratio', 20.0))
        
        high_52w = float(item.get('high_52w', close * 1.5))
        low_52w = float(item.get('low_52w', close * 0.7))
        mcap = float(item.get('market_cap', 10000000000.0))
        roe = float(item.get('roe', 15.0))
        pe = float(item.get('pe', 10.0))
        pb = float(item.get('pb', 2.0))
        perf_y = float(item.get('perf_y', 0.0))
        oper_margin = float(item.get('oper_margin', 12.0))

        # Hissenin Piyasa Değeri Katmanına Göre Dinamik Eşikleri Al
        tier_cfg = get_bist_tier_thresholds(mcap, thresholds)
        t_min_roe = tier_cfg["min_roe"]
        t_min_margin = tier_cfg["min_oper_margin"]
        t_min_dist = tier_cfg["min_dist_from_52w_low"]
        t_max_dist = tier_cfg["max_dist_from_52w_low"]
        t_ideal_pe = tier_cfg["ideal_pe_max"]
        t_accept_pe = tier_cfg["acceptable_pe_max"]
        t_stop_pct = tier_cfg["stop_loss_pct"]
        t_target_cup = tier_cfg["target_cup_min"]

        dist_from_52w_low = ((close - low_52w) / (low_52w + 1e-9)) * 100.0 if low_52w > 0 else 0.0
        target_cup = round(high_52w, 2)
        target_bagger = round(close * 2.50, 2)
        # Taban desteği ve hisse katmanına özel dinamik risk eşiği
        stop_price = round(min(low_52w * 0.96, close * (1.0 + (t_stop_pct / 100.0))), 2)
        potansiyel_cup = round(((target_cup - close) / close) * 100.0, 1)

        # 🛡️ PİYASA DEĞERİNE DUYARLI ESAS FAALİYET KÂRI VE ARSA SATIŞI KALKANI
        is_fake_profit = (oper_margin < t_min_margin)
        is_zombie = (roe < t_min_roe) or (pb <= 0.0) or is_fake_profit
        is_wrong_size = (mcap < min_mcap) or (mcap > max_mcap)
        is_overextended = (perf_y > 150.0) or (dist_from_52w_low > (t_max_dist * 1.5))

        # 1. Kademeli Makro Taban Skoru
        score_base = 20.0
        if t_min_dist <= dist_from_52w_low <= t_max_dist:
            score_base = 90.0
            if potansiyel_cup >= t_target_cup:
                score_base = 100.0
        elif dist_from_52w_low <= (t_max_dist * 1.25):
            score_base = 65.0

        # 2. Kademeli Kalite Skoru (Esas Faaliyet Marjı Teyitli)
        score_quality = 30.0
        if roe >= (t_min_roe * 1.8) and oper_margin >= (t_min_margin * 1.8): score_quality += 45.0
        elif roe >= (t_min_roe * 1.3) and oper_margin >= (t_min_margin * 1.3): score_quality += 30.0
        elif roe >= t_min_roe and oper_margin >= t_min_margin: score_quality += 15.0

        if 0 < pe <= t_ideal_pe: score_quality += 25.0
        elif 0 < pe <= t_accept_pe: score_quality += 15.0
        score_quality = min(max(score_quality, 5.0), 100.0)

        # 3. Kurumsal Takas & Hacimli Ateşleme
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        score_sweep = round(min(max((f_ratio * 0.40) + (max(clv, 0.0) * 60.0), 5.0), 98.5), 1)
        score_ignition = round(min(max((rvol * 35.0) + (max(change, 0.0) * 5.0), 10.0), 100.0), 1)

        item['tier'] = tier_cfg['tier_name']
        item['dist_from_52w_low'] = round(dist_from_52w_low, 1)
        item['stop_price'] = stop_price
        item['target_cup'] = target_cup
        item['target_bagger'] = target_bagger
        item['potansiyel_cup'] = potansiyel_cup
        item['mcap_milyar'] = round(mcap / 1000000000.0, 1)
        item['score_base'] = score_base
        item['score_quality'] = score_quality
        item['score_sweep'] = score_sweep
        item['score_ignition'] = score_ignition
        item['is_disqualified'] = is_zombie or is_wrong_size or is_overextended
        item['is_fake_profit'] = is_fake_profit
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty:
        return res_df

    res_df['pct_base'] = res_df['score_base'].rank(pct=True) * 100.0
    res_df['pct_qual'] = res_df['score_quality'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['score_sweep'].rank(pct=True) * 100.0
    res_df['pct_ign'] = res_df['score_ignition'].rank(pct=True) * 100.0

    w_b = weights["macro_base"]
    w_q = weights["growth_quality"]
    w_s = weights["stealth_accumulation"]
    w_i = weights["volume_ignition"]

    raw_score = np.round(
        res_df['pct_base'] * w_b +
        res_df['pct_qual'] * w_q +
        res_df['pct_sweep'] * w_s +
        res_df['pct_ign'] * w_i,
        1
    )

    res_df['quant_score'] = np.where(
        (res_df['change_%'] > 0.0) & (~res_df['is_disqualified']),
        raw_score,
        0.0
    )

    conditions = [
        res_df['is_fake_profit'],
        res_df['is_disqualified'] & (res_df['market_cap'] > max_mcap),
        res_df['is_disqualified'],
        (res_df['quant_score'] >= 65.0) & (res_df['potansiyel_cup'] >= 45.0),
        (res_df['quant_score'] >= 50.0)
    ]
    choices = [
        "⚠️ SAHTE KÂR (ARSA/DURAN VARLIK SATIŞI)",
        "🏢 MEGA DEV (3X POTANSİYELİ DÜŞÜK)",
        "⚠️ ELENDİ (KULUÇKA ŞARTINA UYMUYOR)",
        "🦅 KULUÇKA LİDERİ (MULTI-BAGGER ADAYI)",
        "⚡ TABAN BİRİKTİRME (TAKİP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_base', 'pct_qual', 'pct_sweep', 'pct_ign', 'is_disqualified', 'is_fake_profit']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)

# =============================================================================
# 4. SİNYAL YAŞAM DÖNGÜSÜ GÜNLÜĞÜ
# =============================================================================

def log_lifecycle_signals(df_scored, state):
    try:
        leaders = df_scored[df_scored['regime'].str.contains("KULUÇKA LİDERİ")].head(6)
        if leaders.empty:
            return

        today = pd.Timestamp.now().normalize()
        history_df = load_lifecycle_signals()

        new_entries = []
        for _, row in leaders.iterrows():
            new_entries.append({
                "tarih": today,
                "ticker": row["ticker"],
                "entry_price": float(row["close"]),
                "stop_price": float(row["stop_price"]),
                "target_cup": float(row["target_cup"]),
                "target_bagger": float(row["target_bagger"]),
                "last_seen_price": float(row["close"]),
                "quant_score": float(row["quant_score"]),
                "regime": row["regime"],
                "score_base": float(row.get("score_base", 0)),
                "score_quality": float(row.get("score_quality", 0)),
                "score_sweep": float(row.get("score_sweep", 0)),
                "score_ignition": float(row.get("score_ignition", 0)),
                "ret_30d": np.nan,
                "ret_90d": np.nan,
                "ret_180d": np.nan,
                "max_drawdown": 0.0,
                "peak_gain": 0.0,
                "outcome": "INCUBATING"
            })

        df_new = pd.DataFrame(new_entries)
        if not history_df.empty:
            existing_tickers = set(history_df[history_df["outcome"].isin(["INCUBATING", "PENDING"])]["ticker"].tolist())
            df_to_add = df_new[~df_new["ticker"].isin(existing_tickers)]
            if not df_to_add.empty:
                combined = pd.concat([history_df, df_to_add], ignore_index=True)
                combined.to_csv(LIFECYCLE_LOG_FILE, index=False)
        else:
            df_new.to_csv(LIFECYCLE_LOG_FILE, index=False)
    except Exception as e:
        print(f"⚠️ Sinyal günlüğü hatası: {e}")

# =============================================================================
# 5. TELEGRAM VE RAPORLAMA SİSTEMİ
# =============================================================================

def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if not token or not chat_id:
        print("ℹ️ Telegram bilgileri eksik, terminale yazdırılıyor.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"⚠️ Telegram hatası: {e}")
        return False

def format_telegram_report(df_scored, state, exit_alerts):
    regime = state.get("market_regime", "BULL_EXPANSION")
    audit = state.get("audit_summary", {})
    weights = state.get("weights", {})
    
    tarih_str = datetime.now().strftime("%d.%m.%Y")
    regime_icon = "🟢" if "BOĞA" in regime else "🔴"
    
    msg = f"🦅 <b>BIST MULTI-BAGGER QUANT TERMINALİ</b> | <code>{tarih_str}</code>\n"
    msg += f"───────────────────────\n"
    msg += f"🧭 Piyasa Rejimi: {regime_icon} <b>{regime}</b>\n"
    msg += f"🧠 Model Durumu: <b>{audit.get('status', 'Optimizasyon Tamamlandı')}</b>\n"
    msg += f"🏆 6 Aylık Win Rate: <b>%{audit.get('win_rate_6m', 0.0):.1f}</b>\n"
    msg += f"───────────────────────\n\n"

    if exit_alerts:
        msg += "🚨 <b>DİNAMİK RİSK VE ÇIKIŞ UYARILARI</b>\n"
        for alert in exit_alerts:
            msg += f"• <b>#{alert['ticker']}</b>: {alert['msg']}\n"
        msg += "\n"

    leaders = df_scored[df_scored['regime'].str.contains("KULUÇKA LİDERİ")].head(5)
    if not leaders.empty:
        msg += "💎 <b>GÜNÜN KULUÇKA LİDERLERİ (Multi-Bagger Adayları)</b>\n"
        msg += "<i>(Piyasa Değeri Kademesi, Taban & Esas Faaliyet Kâr Teyitli)</i>\n\n"
        
        for idx, row in leaders.iterrows():
            s_diff = row.get('score_diff', 0.0)
            fark_str = f"+{s_diff:.1f}" if s_diff > 0 else f"{s_diff:.1f}"
            tier_label = row.get('tier', 'SMALL_CAP')
            
            msg += f"⭐ <b>#{row['ticker']}</b> [{tier_label}] ── <b>Skor: {row['quant_score']:.1f}</b> <i>({fark_str})</i>\n"
            msg += f"💵 Fiyat: <b>{row['close']:.2f} TL</b> (PD: <b>{row['mcap_milyar']:.1f} Mr TL</b>)\n"
            msg += f"📊 ROE: <b>%{row.get('roe', 0):.1f}</b> | F/K: <b>{row.get('pe', 0):.1f}</b> | Faaliyet Marjı: <b>%{row.get('oper_margin', 0):.1f}</b>\n"
            msg += f"🎯 1. Çanak Hedefi: <b>{row['target_cup']:.2f} TL</b> (Potansiyel: <b>+%{row['potansiyel_cup']:.1f}</b>)\n"
            msg += f"🚀 2. Multi-Bagger: <b>{row['target_bagger']:.2f} TL</b> (+%150)\n"
            msg += f"🛡️ Taban Stop: <b>{row['stop_price']:.2f} TL</b> | 52H Dip Farkı: <b>%{row['dist_from_52w_low']:.1f}</b>\n"
            msg += f"───────────────────────\n"
    else:
        msg += "ℹ️ Bugün tüm kuluçka ve kalite filtrelerini geçen yeni hisse bulunamadı (Nakit Koruma).\n"

    msg += "\n<i>Not: Yatırım tavsiyesi değildir. Otonom Quant Kuluçka Modeli çıktısıdır.</i>"
    return msg

# =============================================================================
# 6. ANA YÜRÜTÜCÜ
# =============================================================================

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🦅 BIST Quant Kuluçka Motoru Başlatılıyor...")

    state = load_ai_state()
    df_gecmis = pd.DataFrame()
    if os.path.exists(GECMIS_DOSYA):
        try:
            df_gecmis = pd.read_csv(GECMIS_DOSYA)
            if 'tarih' in df_gecmis.columns:
                df_gecmis['tarih'] = pd.to_datetime(df_gecmis['tarih'])
        except Exception:
            pass

    # 1. Denetçi ve Öğrenme Döngüsünü Çalıştır
    state, exit_alerts = audit_and_calibrate()

    # 2. Piyasa Verilerini Çek
    df_current = fetch_all_market_data()
    if df_current.empty:
        print("⚠️ Güncel veri çekilemedi, işlem sonlandırılıyor.")
        return

    # 3. Kademeli Quant Puanlarını Hesapla
    df_scored = calculate_quant_scores(df_current, df_gecmis, state)

    # 4. Sinyalleri Kaydet
    log_lifecycle_signals(df_scored, state)

    # 5. Geçmiş Veriyi Güncelle
    if not df_gecmis.empty:
        df_yeni = pd.concat([df_gecmis, df_scored], ignore_index=True)
    else:
        df_yeni = df_scored
    df_yeni.to_csv(GECMIS_DOSYA, index=False)

    # 6. Telegram Raporu Gönder
    report = format_telegram_report(df_scored, state, exit_alerts)
    send_telegram(report)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ BIST Quant Güncellemesi Başarıyla Tamamlandı!")

if __name__ == "__main__":
    main()
