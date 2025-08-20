import requests
import sqlite3
import json
import re
import time

DB_PATH = r"../stock.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS t_stock_change (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market INTEGER,
            trade_date INTEGER,
            change_count INTEGER,
            UNIQUE(stock_code, trade_date)
        )
    """)
    conn.commit()
    conn.close()

def fetch_stock_changes(code, market=0):
    url = (
        "https://push2ex.eastmoney.com/getStockStatisticsChanges"
        "?ut=7eea3edcaed734bea9cbfc24409ed989"
        f"&startdate=20160101&enddate=20500101&dpt=wzchanges&code={code}&market={market}"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    res.raise_for_status()

    text = res.text
    json_str = re.sub(r"^[^(]+\(|\);?$", "", text)  # 去掉 JSONP 包装
    data = json.loads(json_str)
    return data.get("data")

def save_to_db(stock_data):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    stock_code = stock_data.get("c")
    stock_name = stock_data.get("n")
    market = stock_data.get("m")
    for item in stock_data.get("data", []):
        trade_date = item.get("d")
        change_count = item.get("ct")
        try:
            cur.execute("""
                INSERT OR IGNORE INTO t_stock_change
                (stock_code, stock_name, market, trade_date, change_count)
                VALUES (?, ?, ?, ?, ?)
            """, (stock_code, stock_name, market, trade_date, change_count))
        except Exception as e:
            print("插入失败:", e)
    conn.commit()
    conn.close()

def fetch_and_save(code, market=0):
    stock_data = fetch_stock_changes(code, market)
    if stock_data:
        save_to_db(stock_data)
        print(f"✅ {stock_data['n']}({stock_data['c']}) 数据入库完成")
    else:
        print(f"⚠️ {code} 无数据")


def process_all_stocks():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT stock_code FROM t_stock_quote")
    rows = cur.fetchall()

    for (full_code,) in rows:
        # 市场判断
        if full_code.endswith(".SZ"):
            stock_id = full_code.replace(".SZ", "")
            market_code = 0
        elif full_code.endswith(".SS") or full_code.endswith(".SH"):
            stock_id = full_code.replace(".SS", "").replace(".SH", "")
            market_code = 1
        else:
            print(f"⚠️ 未知市场标识: {full_code}")
            continue

        # 判断是否已爬取过
        cur.execute("SELECT COUNT(*) FROM t_stock_change WHERE stock_code = ?", (stock_id,))
        count = cur.fetchone()[0]
        if count > 1:
            print(f"⏩ 跳过 {stock_id} (已存在 {count} 条记录)")
            continue

        # 防爬休息
        time.sleep(1)

        # 执行抓取
        fetch_and_save(stock_id, market_code)

    conn.close()

if __name__ == "__main__":
    # init_db()
    process_all_stocks()
