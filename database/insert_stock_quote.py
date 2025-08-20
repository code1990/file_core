import time
import requests
import sqlite3
import math

DB_FILE = "stock.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS t_stock_quote (
        stock_code TEXT PRIMARY KEY,          -- 股票代码，例如 300004.SZ
        addi_tradetime_bits REAL,
        amount REAL,
        amplitude REAL,
        auction_px REAL,
        auction_val REAL,
        auction_vol REAL,
        bid_grp TEXT,
        bps REAL,
        business_amount REAL,
        business_amount_am REAL,
        business_amount_in REAL,
        business_amount_out REAL,
        business_balance REAL,
        business_balance_am REAL,
        business_balance_scale REAL,
        business_count REAL,
        business_last_closedate TEXT,
        circulation_amount REAL,
        circulation_value REAL,
        close_px REAL,
        current_amount REAL,
        data_timestamp REAL,
        debt_fund_value REAL,
        down_px REAL,
        dyn_pb_rate REAL,
        entrust_diff REAL,
        entrust_rate REAL,
        eps REAL,
        eps_ttm REAL,
        eps_year REAL,
        fund_discount_value REAL,
        high_px REAL,
        iopv REAL,
        last_px REAL,
        low_px REAL,
        market_date TEXT,
        market_value REAL,
        min1_chgpct REAL,
        min3_chgpct REAL,
        min5_chgpct REAL,
        mrq_pb_rate REAL,
        offer_grp TEXT,
        open_flag REAL,
        open_px REAL,
        osov_rate REAL,
        pe_rate REAL,
        preclose_px REAL,
        premium_rate REAL,
        prev_amount REAL,
        prod_name TEXT,
        prod_name_ext TEXT,
        px_change REAL,
        px_change_rate REAL,
        px_change_rate_10days REAL,
        px_change_rate_20days REAL,
        px_change_rate_5days REAL,
        px_change_rate_60days REAL,
        shares_per_hand REAL,
        special_marker REAL,
        static_pe_rate REAL,
        total_bidqty REAL,
        total_offerqty REAL,
        total_shares REAL,
        trade_mins REAL,
        trade_status TEXT,
        ttm_pe_rate REAL,
        turnover_1mins REAL,
        turnover_3mins REAL,
        turnover_5mins REAL,
        turnover_ratio REAL,
        up_px REAL,
        updown_days REAL,
        vol_ratio REAL,
        w52_high_px REAL,
        w52_low_px REAL,
        wavg_px REAL,
        year_pxchange_rate REAL
    )
    """)
    conn.commit()
    conn.close()

def get_stock_total_count():
    """获取股票总数"""
    url = "https://quotedata.cnfin.com/quote/v1/sort"
    params = {
        "sort_field_name": "px_change_rate",
        "sort_type": "undefined",
        "start_pos": 0,
        "data_count": 1,
        "en_hq_type_code": "SS.ESA.M,SZ.ESA.M,SZ.ESA.SMSE,SZ.ESA.GEM,SS.KSH,SZ.ESA.SMSE",
        "fields": "null",
        "request_sort_count": 1,
        "localDate": str(int(time.time() * 1000))
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    js = resp.json()
    return js["data"]["sort"]["sort_result_count"]

def fetch_and_store(start_pos=0, page_size=20):
    """分页抓取并写入SQLite"""
    url = "https://quotedata.cnfin.com/quote/v1/sort"
    params = {
        "sort_field_name": "px_change_rate",
        "sort_type": 1,
        "start_pos": start_pos,
        "data_count": page_size,
        "en_hq_type_code": "SS.ESA.M,SZ.ESA.M,SZ.ESA.SMSE,SZ.ESA.GEM,SS.KSH,SZ.ESA.SMSE",
        "fields": "null",
        "localDate": str(int(time.time() * 1000))
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    js = resp.json()

    fields = js["data"]["sort"]["fields"]
    data = js["data"]["sort"]

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    for stock_code, values in data.items():
        if stock_code == "fields":
            continue
        placeholders = ",".join(["?"] * (len(fields) + 1))
        sql = f"""
        INSERT INTO t_stock_quote (stock_code, {",".join(fields)})
        VALUES ({placeholders})
        ON CONFLICT(stock_code) DO UPDATE SET 
        {",".join([f"{f}=excluded.{f}" for f in fields])}
        """
        cur.execute(sql, [stock_code] + values)

    conn.commit()
    conn.close()
    print(f"✅ 已处理 start_pos={start_pos}")

def fetch_all(page_size=100):
    """自动分页抓取所有实时行情"""
    total = get_stock_total_count()
    total_pages = math.ceil(total / page_size)
    print(f"总股票数: {total}, 总页数: {total_pages}")

    for i in range(total_pages):
        start_pos = i * page_size
        fetch_and_store(start_pos, page_size)

if __name__ == "__main__":
    init_db()
    fetch_all(page_size=100)
    print("🎉 全量实时行情已写入 SQLite")
