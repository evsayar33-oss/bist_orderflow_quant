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
# 1. 52 HAFTALIK VE BİLANÇO VERİLERİ (TRADINGVIEW & İŞ YATIRIM)
# =============================================================================

def get_bist_macro_data():
    """52 haftalık dip/zirve, piyasa değeri ve büyüme verilerini çeker."""
    url = "https://scanner.tradingview.com/turkey/scan"
    payload = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "Value.Traded", "operation": "greater", "right": 5000000}
        ],
        "columns": [
            "name", "close", "open", "high", "low", "volume", "change", "Value.Traded",
            "price_52_week_high",      # 1 Yıllık Zirve (Çanak Hedefi)
            "price_52_week_low",       # 1 Yıllık Dip (Kuluçka Tabanı)
            "market_cap_basic",        # Piyasa Değeri (TL)
            "return_on_equity_fq",     # ROE (Kârlılık)
            "price_earnings_ttm",      # F/K
            "price_book_fq",           # PD/DD
            "Perf.Y",                  # 1 Yıllık Prim
            "relative_volume_10d_calc" # RVOL
        ],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, 400]
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
                "rvol": float(d[15]) if len(d) > 15 and d[15] is not None else 1.0
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
# 2. MULTI-BAGGER (KULUÇKA & BÜYÜME) PUANLAMA MOTORU
# =============================================================================

def calculate_quant_scores(df, df_gecmis, state):
    if df.empty:
        return df

    weights = state.get("weights", {"macro_base": 0.35, "growth_quality": 0.30, "stealth_accumulation": 0.20, "volume_ignition": 0.15})
    thresholds = state.get("thresholds", {})
    
    min_mcap = thresholds.get("min_market_cap_tl", 2000000000)      # Min 2 Milyar TL
    max_mcap = thresholds.get("max_market_cap_tl", 40000000000)     # Max 40 Milyar TL (Atak Şirketler)
    min_roe = thresholds.get("min_roe", 18.0)

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

        # A. 52 HAFTALIK MAKRO TABAN GEOMETRİSİ
        # 1 Yıllık Çanak genişliği ve dipten uzaklık
        dist_from_52w_low = ((close - low_52w) / (low_52w + 1e-9)) * 100.0 if low_52w > 0 else 0.0
        dist_from_52w_high = ((high_52w - close) / (close + 1e-9)) * 100.0 if close > 0 else 0.0

        # Dinamik %100 - %250 Hedef Projeksiyonu
        target_cup = round(high_52w, 2)                                  # 1. Hedef: Eski Zirveye Dönüş (Çanak Tamamlama)
        target_bagger = round(close * 2.50, 2)                           # 2. Hedef: Multi-Bagger (%150 Prim)
        stop_price = round(low_52w * 0.96, 2)                            # Makro Dip Desteği Kırılırsa Stop

        potansiyel_cup = round(((target_cup - close) / close) * 100.0, 1)
        potansiyel_bagger = 150.0

        # B. DİSKALİFİYE FİLTRELERİ
        # 1. Zombi / Zarardaki Şirketler elenir
        is_zombie = (roe < min_roe) or (pb <= 0.0)
        
        # 2. Dev Hisseler (50B+ TL) 3x yapamayacağı için elenir; sığ hisseler (<1.5B) batık riski nedeniyle elenir
        is_wrong_size = (mcap < min_mcap) or (mcap > max_mcap)

        # 3. Zaten 1 yılda %300 koşmuş tepedeki hisseler elenir
        is_overextended = (perf_y > 150.0) or (dist_from_52w_low > 50.0)

        # 1. Makro Taban Skoru (52 Haftalık Dipte Kuluçka)
        score_base = 20.0
        if 4.0 <= dist_from_52w_low <= 28.0:
            score_base = 90.0
            if dist_from_52w_high >= 60.0: # Önünde en az %60 çanak boşluğu olanlar
                score_base = 100.0
        elif dist_from_52w_low <= 35.0:
            score_base = 65.0

        # 2. Temel Büyüme ve Kârlılık Skoru (Quality Engine)
        score_quality = 30.0
        if roe >= 35.0: score_quality += 45.0
        elif roe >= 22.0: score_quality += 30.0
        elif roe >= min_roe: score_quality += 15.0

        if 0 < pe <= 12.0: score_quality += 25.0
        elif 0 < pe <= 20.0: score_quality += 15.0

        score_quality = min(max(score_quality, 5.0), 100.0)

        # 3. Kurumsal Sessiz Toplama (Stealth Accumulation)
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        score_sweep = (f_ratio * 0.40) + (max(clv, 0.0) * 60.0)
        score_sweep = round(min(max(score_sweep, 5.0), 98.5), 1)

        # 4. Hacimli Ateşleme (Ignition)
        score_ignition = round(min(max((rvol * 35.0) + (max(change, 0.0) * 5.0), 10.0), 100.0), 1)

        item['dist_from_52w_low'] = round(dist_from_52w_low, 1)
        item['dist_from_52w_high'] = round(dist_from_52w_high, 1)
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
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty:
        return res_df

    res_df['pct_base'] = res_df['score_base'].rank(pct=True) * 100.0
    res_df['pct_qual'] = res_df['score_quality'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['score_sweep'].rank(pct=True) * 100.0
    res_df['pct_ign'] = res_df['score_ignition'].rank(pct=True) * 100.0

    w_b = weights.get('macro_base', 0.35)
    w_q = weights.get('growth_quality', 0.30)
    w_s = weights.get('stealth_accumulation', 0.20)
    w_i = weights.get('volume_ignition', 0.15)

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
        res_df['is_disqualified'] & (res_df['market_cap'] > max_mcap),
        res_df['is_disqualified'],
        (res_df['quant_score'] >= 65.0) & (res_df['potansiyel_cup'] >= 50.0),
        (res_df['quant_score'] >= 50.0)
    ]
    choices = [
        "🏢 MEGA DEV (3X POTANSİYELİ DÜŞÜK)",
        "⚠️ ELENDİ (KULUÇKA ŞARTINA UYMUYOR)",
        "🦅 KULUÇKA LİDERİ (MULTI-BAGGER ADAYI)",
        "⚡ TABAN BİRİKTİRME (TAKİP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_base', 'pct_qual', 'pct_sweep', 'pct_ign', 'is_disqualified']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)

# =============================================================================
# 3. YAŞAM DÖNGÜSÜ GÜNLÜĞÜ
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
                "quant_score": float(row["quant_score"]),
                "regime": row["regime"],
                "score_base": float(row.get("score_base", 50.0)),
                "score_quality": float(row.get("score_quality", 50.0)),
                "score_sweep": float(row.get("score_sweep", 50.0)),
                "score_ignition": float(row.get("score_ignition", 50.0)),
                "ret_30d": np.nan,
                "ret_90d": np.nan,
                "ret_180d": np.nan,
                "max_drawdown": 0.0,
                "peak_gain": 0.0,
                "outcome": "INCUBATING"
            })

        df_new = pd.DataFrame(new_entries)
        if not history_df.empty:
            history_df["tarih"] = pd.to_datetime(history_df["tarih"])
            history_df = history_df[history_df["tarih"] != today]
            updated_history = pd.concat([history_df, df_new], ignore_index=True)
        else:
            updated_history = df_new

        updated_history.to_csv(LIFECYCLE_LOG_FILE, index=False)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(df_new)} adet Kuluçka Adayı deftere işlendi.")
    except Exception as e:
        print(f"⚠️ Yaşam döngüsü hatası: {e}")

# =============================================================================
# 4. TELEGRAM RAPORU (MULTI-BAGGER PROJEKSİYONLU)
# =============================================================================

def send_telegram(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('CHAT_ID')
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        if res.status_code != 200:
            plain_text = message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
            requests.post(url, json={"chat_id": chat_id, "text": plain_text}, timeout=10)
            print("✅ Telegram mesajı düz metin olarak iletildi.")
        else:
            print("✅ Telegram raporu HTML olarak iletildi.")
    except Exception as e:
        print(f"⚠️ Telegram Hatası: {e}")

def format_telegram_report(df_scored, state):
    leaders = df_scored[df_scored['regime'].str.contains("KULUÇKA LİDERİ")].head(5)
    audit = state.get("audit_summary", {})
    weights = state.get("weights", {})
    
    msg = "🦅 <b>BIST MULTI-BAGGER & KULUÇKA MOTORU</b>\n"
    msg += f"🗓 <i>{datetime.now().strftime('%Y-%m-%d')} | 6-12 Aylık Makro Rapor</i>\n"
    msg += f"🤖 <b>AI Durumu:</b> <code>{audit.get('status', 'AKTİF')}</code>\n"
    msg += f"⚖️ <b>Ağırlıklar:</b> 52H Taban: %{int(weights.get('macro_base', 0)*100)} | Büyüme: %{int(weights.get('growth_quality', 0)*100)} | Takas: %{int(weights.get('stealth_accumulation', 0)*100)}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    if leaders.empty:
        msg += "ℹ️ <i>Bugün 52 haftalık derin tabanında kuluçkaya yatmış, yüksek kârlı Small-Cap hisse bulunamadı.</i>\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "🛡️ <i>Filtre: Şişmiş devler (50 Milyar TL üstü) ve kârsız zombi şirketler elenmiştir.</i>"
        return msg

    for idx, row in leaders.iterrows():
        s_diff = row.get('score_diff', 0.0)
        fark_str = f"+{s_diff:.1f}" if s_diff > 0 else f"{s_diff:.1f}"
        
        msg += f"💎 <b>#{row['ticker']}</b> ── <b>Skor: {row['quant_score']:.1f}</b> <i>({fark_str})</i>\n"
        msg += f"💵 Fiyat: <b>{row['close']:.2f} TL</b> (Piyasa Değeri: <b>{row['mcap_milyar']:.1f} Mr TL</b>)\n"
        msg += f"📍 52 Haftalık Dip Mesafesi: <b>+%{row['dist_from_52w_low']:.1f}</b> (Derin Taban)\n"
        msg += f"🎯 <b>1. Hedef (Çanak Tamamlama):</b> <b>{row['target_cup']:.2f} TL</b> (<b>+%{row['potansiyel_cup']:.0f}</b>)\n"
        msg += f"🚀 <b>2. Hedef (Multi-Bagger 2.5x):</b> <b>{row['target_bagger']:.2f} TL</b> (<b>+%150</b>)\n"
        msg += f"🛡️ Makro Destek Stopu: <b>{row['stop_price']:.2f} TL</b>\n"
        msg += f"📊 ROE: <b>%{row.get('roe', 0):.1f}</b> | F/K: <b>{row.get('pe', 0):.1f}</b> | Takas: <b>%{row.get('score_sweep', 0):.1f}</b>\n\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "⏳ <i>Yatırım Ufku: 6-12 Ay (Buy & Hold Sabır Stratejisi)</i>"
    return msg

# =============================================================================
# 5. ANA YÜRÜTÜCÜ
# =============================================================================

def main():
    print("=== BIST Multi-Bagger Kuluçka Motoru Başlıyor ===")
    state = audit_and_calibrate()
    df_current = fetch_all_market_data()
    if df_current.empty:
        return

    df_gecmis = pd.DataFrame()
    if os.path.exists(GECMIS_DOSYA):
        try:
            df_gecmis = pd.read_csv(GECMIS_DOSYA)
            df_gecmis['tarih'] = pd.to_datetime(df_gecmis['tarih'])
        except Exception:
            pass

    df_scored = calculate_quant_scores(df_current, df_gecmis, state)
    if df_scored.empty:
        return

    log_lifecycle_signals(df_scored, state)

    if not df_gecmis.empty:
        df_gecmis = df_gecmis[df_gecmis['tarih'] != pd.Timestamp.now().normalize()]
        df_yeni = pd.concat([df_gecmis, df_scored], ignore_index=True)
    else:
        df_yeni = df_scored

    df_yeni['tarih'] = pd.to_datetime(df_yeni['tarih'])
    limit_tarih = pd.Timestamp.now().normalize() - pd.Timedelta(days=60)
    df_yeni[df_yeni['tarih'] >= limit_tarih].to_csv(GECMIS_DOSYA, index=False)

    send_telegram(format_telegram_report(df_scored, state))
    print("Multi-Bagger Taraması Tamamlandı!")

if __name__ == "__main__":
    main()
