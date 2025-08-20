# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = r"../stock.db"
OUT_DIR = Path("../data")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def extract_table(table_name: str, out_file: str):
    log(f"开始提取 {table_name} ...")
    conn = sqlite3.connect(DB_PATH)

    # 分批读取，避免一次性内存爆炸
    chunks = []
    for chunk in pd.read_sql(f"SELECT * FROM {table_name}", conn, chunksize=200000):
        chunks.append(chunk)
        log(f"已加载 {len(chunk)} 行 ...")

    conn.close()

    df = pd.concat(chunks, ignore_index=True)
    log(f"{table_name} 总行数={len(df)}，保存到 {out_file}")
    df.to_parquet(out_file, index=False)

def main():
    extract_table("t_stock_signal_2", OUT_DIR / "signals2.parquet")
    extract_table("t_stock_signal_3", OUT_DIR / "signals3.parquet")
    extract_table("t_stock_daily", OUT_DIR / "daily.parquet")

if __name__ == "__main__":
    main()