# -*- coding: utf-8 -*-
import requests
import sqlite3
import json
import re
import time


DB_PATH = r"../stock.db"  # SQLite 数据库文件

# 加载 position.json 做信号映射
with open("../data/postition.json", "r", encoding="utf-8") as f:
    POSITION_MAP = json.load(f)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS t_stock_change_detail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market INTEGER,
            trade_date INTEGER,
            trade_time TEXT,
            signal_code INTEGER,
            signal_name TEXT,
            price REAL,
            change_percent REAL,
            extra_info TEXT,
            volume INTEGER,
            amount REAL,
            UNIQUE(stock_code, trade_date, trade_time, signal_code)
        )
    """)
    conn.commit()
    conn.close()


def fetch_stock_change_detail(code, date, market=0):
    url = (
        "https://push2ex.eastmoney.com/getStockChanges"
        "?ut=7eea3edcaed734bea9cbfc24409ed989"
        f"&date={date}&dpt=wzchanges&code={code}&market={market}"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    text = res.text
    json_str = re.sub(r"^[^(]+\(|\);?$", "", text)  # 去掉 JSONP 包装
    data = json.loads(json_str)
    print(data)
    return data.get("data")


def save_detail(stock_data):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    stock_code = stock_data.get("c")
    stock_name = stock_data.get("n")
    market = stock_data.get("m")
    trade_date = stock_data.get("d")

    for item in stock_data.get("data", []):
        # 1) 交易时间
        tm_raw = item.get("tm")
        tm = str(tm_raw).zfill(6) if tm_raw is not None else "000000"
        trade_time = f"{tm[0:2]}:{tm[2:4]}:{tm[4:6]}"

        # 2) 信号
        signal_code = item.get("t")
        signal_info = POSITION_MAP.get(str(signal_code), {})
        signal_name = signal_info.get("name", "")

        # 3) 解析 i 字段
        extra_info = item.get("i")
        vol = amt = price2 = percent2 = None
        if isinstance(extra_info, str) and extra_info:
            parts = [p.strip() for p in extra_info.split(",")]
            try:
                if len(parts) >= 1 and parts[0] != "":
                    vol = int(float(parts[0]))
                if len(parts) >= 2 and parts[1] != "":
                    price2 = float(parts[1])
                if len(parts) >= 3 and parts[2] != "":
                    percent2 = float(parts[2]) * 100.0
                if len(parts) >= 4 and parts[3] != "":
                    amt = float(parts[3])
            except Exception:
                pass

        # 4) 价格
        p_raw = item.get("p")
        if price2 is not None:
            price = price2
        else:
            price = float(p_raw) / 1000.0 if p_raw is not None else None

        # 5) 涨跌幅
        u_raw = item.get("u")
        if percent2 is not None:
            change_percent = percent2
        else:
            change_percent = float(u_raw) if (u_raw is not None and str(u_raw).strip() != "") else None

        try:
            cur.execute("""
                INSERT OR IGNORE INTO t_stock_change_detail
                  (stock_code, stock_name, market, trade_date, trade_time,
                   signal_code, signal_name, price, change_percent,
                   extra_info, volume, amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stock_code, stock_name, market, trade_date, trade_time,
                signal_code, signal_name, price, change_percent,
                extra_info, vol, amt
            ))

            if cur.rowcount == 0:
                print(f"⚠️ 已存在，忽略 {stock_code}-{trade_date}-{trade_time}-{signal_code}")
            else:
                print(f"✅ 插入成功 {stock_code}-{trade_date}-{trade_time}-{signal_code}")

        except Exception as e:
            print("插入失败:", e, "原始条目:", item)

    conn.commit()
    conn.close()


def process_all_stocks(start=0, end=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 先取出所有股票
    cur.execute("SELECT stock_code FROM t_stock_quote ORDER BY stock_code")
    rows = cur.fetchall()

    # 切片范围
    if end is None or end > len(rows):
        end = len(rows)
    rows = rows[start:end]

    print(f"👉 本次处理股票范围: {start} ~ {end-1} (共 {len(rows)} 支)")

    for idx, (full_code,) in enumerate(rows, start=start):
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

        # 从 t_stock_change 取该股票的所有交易日期
        cur.execute("SELECT DISTINCT trade_date FROM t_stock_change WHERE stock_code = ?", (stock_id,))
        dates = [row[0] for row in cur.fetchall()]

        for trade_date in dates:
            # 判断是否已经抓取过明细
            cur.execute(
                "SELECT COUNT(*) FROM t_stock_change_detail WHERE stock_code = ? AND trade_date = ?",
                (stock_id, trade_date)
            )
            count = cur.fetchone()[0]
            if count > 0:
                print(f"⏩ 跳过 {stock_id}-{trade_date} (已有 {count} 条记录)")
                continue

            time.sleep(1)  # 防爬休息

            # 抓取并保存
            stock_data = fetch_stock_change_detail(stock_id, trade_date, market_code)
            if stock_data:
                save_detail(stock_data)
                print(f"✅ {stock_data['n']}({stock_data['c']}) {stock_data['d']} 明细入库完成")
            else:
                print(f"⚠️ {stock_id}-{trade_date} 无数据")

    conn.close()


if __name__ == "__main__":
    init_db()
    process_all_stocks()
