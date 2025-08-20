# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
from pathlib import Path
import numpy as np

DB_PATH = r"../stock.db"  # ← 修改为你的 SQLite 路径


def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def validate_combo(conn, combo_name, hold_days=3, stop_loss=-0.03):
    """
    回测某个组合信号的实盘收益
    :param conn: sqlite3 连接
    :param combo_name: 组合名称，如 "简单买点&绝对底部&进攻"
    :param hold_days: 持仓天数
    :param stop_loss: 止损阈值 (例如 -0.03 = -3%)
    :return: dict 报告
    """
    log(f"验证组合 {combo_name} 的实盘收益 ...")

    # 1) 提取信号出现记录
    sig_hits = pd.read_sql(f"""
        SELECT stock_code, trade_date
        FROM t_stock_signal_3
        WHERE combo_name='{combo_name}'
    """, conn)

    results = []

    for _, row in sig_hits.iterrows():
        stock = row["stock_code"]
        date = row["trade_date"]

        # 2) 提取未来 N 天行情
        df = pd.read_sql(f"""
            SELECT trade_date, open, close
            FROM t_stock_daily
            WHERE stock_code='{stock}'
            AND trade_date > {date}
            ORDER BY trade_date ASC
            LIMIT {hold_days}
        """, conn)

        if df.empty:
            continue

        buy_price = df.iloc[0]["open"]
        pnl = None

        # 3) 模拟持仓 + 止损
        for _, r in df.iterrows():
            ret = (r["close"] - buy_price) / buy_price
            if ret < stop_loss:  # 止损
                pnl = ret
                break
            pnl = ret  # 否则持有到最后

        results.append(pnl)

    if not results:
        log("❌ 没有足够的样本")
        return None

    results = pd.Series(results)
    report = {
        "combo_name": combo_name,
        "n_trades": int(len(results)),
        "win_ratio": float((results > 0).mean()),
        "avg_return": float(results.mean()),
        "max_return": float(results.max()),
        "min_return": float(results.min()),
    }
    return report


def main():
    conn = sqlite3.connect(DB_PATH)

    # ✅ 这里修改为你要验证的组合
    combo_name = "简单买点&绝对底部&进攻"

    report = validate_combo(conn, combo_name, hold_days=3, stop_loss=-0.03)

    if report:
        print("\n===== 回测报告 =====")
        for k, v in report.items():
            print(f"{k}: {v}")
    else:
        print("没有得到回测结果")

    conn.close()


if __name__ == "__main__":
    main()
