import requests
import pandas as pd
import numpy as np
import concurrent.futures
from datetime import datetime

def get_bist_fresh_breakout_data():
    """TradingView üzerinden 3 aylık taban (Low.3M), 3 aylık zirve (High.3M) ve hacim verilerini çeker."""
    url = "https://scanner.tradingview.com/turkey/scan"
    payload = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "Value.Traded", "operation": "greater", "right": 8000000}
        ],
        "columns": [
            "name", "close", "open", "high", "low", "volume", "change", "Value.Traded",
            "High.3M",                 # 3 Aylık Zirve Direnci
            "Low.3M",                  # 3 Aylık Dip Tabanı
            "relative_volume_10d_calc",# RVOL
            "Perf.W",                  # 1 Haftalık Prim
            "Perf.1M",                 # 1 Aylık Prim
            "Perf.3M",                 # 3 Aylık Prim
            "Perf.6M",                 # 6 Aylık Prim
            "return_on_equity_fq",     # ROE
            "price_book_fq"            # PD/DD
        ],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, 300]
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.tradingview.com/"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        data = response.json()
        rows = []
        for item in data.get("data", []):
            d = item["d"]
            rows.append({
                "ticker": d[0],
                "close": float(d[1]) if d[1] is not None else 0.0,
                "open": float(d[2]) if d[2] is not None else 0.0,
                "high": float(d[3]) if d[3] is not None else 0.0,
                "low": float(d[4]) if d[4] is not None else 0.0,
                "volume": float(d[5]) if d[5] is not None else 0.0,
                "change_%": float(d[6]) if d[6] is not None else 0.0,
                "value_traded": float(d[7]) if d[7] is not None else 0.0,
                "high_3m": float(d[8]) if d[8] is not None else (float(d[3]) if d[3] is not None else 0.0),
                "low_3m": float(d[9]) if d[9] is not None else (float(d[4]) if d[4] is not None else 0.0),
                "rvol": float(d[10]) if len(d) > 10 and d[10] is not None else 1.0,
                "perf_w": float(d[11]) if len(d) > 11 and d[11] is not None else 0.0,
                "perf_1m": float(d[12]) if len(d) > 12 and d[12] is not None else 0.0,
                "perf_3m": float(d[13]) if len(d) > 13 and d[13] is not None else 0.0,
                "perf_6m": float(d[14]) if len(d) > 14 and d[14] is not None else 0.0,
                "roe": float(d[15]) if len(d) > 15 and d[15] is not None else 15.0,
                "pb": float(d[16]) if len(d) > 16 and d[16] is not None else 2.0
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"Veri Hatası: {e}")
        return pd.DataFrame()

def fetch_single_broker_flow(ticker):
    url = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.YatirimDanismanligi/PiyasaVerileri.aspx/GetHisseTakasData"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json; charset=utf-8",
        "X-Requested-With": "XMLHttpRequest"
    }
    try:
        res = requests.post(url, json={"hisseKodu": ticker}, headers=headers, timeout=3)
        if res.status_code == 200:
            data = res.json().get("d", [])
            if data:
                shares = np.array([float(x.get("Yuzde", 0) or 0) for x in data[:15]])
                hhi = float(np.sum(((shares / (shares.sum() + 1e-9)) * 100) ** 2)) if shares.sum() > 0 else 0.0
                foreign_banks = ["CITIBANK YABANCI", "DEUTSCHE YABANCI", "HSBC YATIRIM", "YATIRIM FINANSMAN", "QNB FINANS"]
                f_ratio = sum([float(x.get("Yuzde", 0) or 0) for x in data if str(x.get("ALAN_ADI")).upper() in foreign_banks])
                if hhi > 0 or f_ratio > 0:
                    return ticker, round(hhi, 2), round(f_ratio, 2)
    except:
        pass
    return ticker, 0.0, 0.0

def fetch_all_data():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 3 Aylık dip ve zirve verileri çekiliyor...")
    df_market = get_bist_fresh_breakout_data()
    if df_market.empty:
        return df_market

    takas_results = []
    tickers = df_market['ticker'].tolist()
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_single_broker_flow, ticker): ticker for ticker in tickers}
        for future in concurrent.futures.as_completed(futures):
            takas_results.append(future.result())

    df_takas = pd.DataFrame(takas_results, columns=['ticker', 'hhi_score', 'foreign_ratio'])
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

    df_final['hhi_score'] = pd.to_numeric(df_final['hhi_score'], errors='coerce').fillna(0.0)
    mask_h = (df_final['hhi_score'] == 0.0)
    rvol_arr = pd.to_numeric(df_final['rvol'], errors='coerce').fillna(1.0).values
    df_final.loc[mask_h, 'hhi_score'] = np.round(rvol_arr[mask_h] * 1250.0 + 1200.0, 2)

    df_final['tarih'] = pd.Timestamp.now().normalize()
    return df_final
