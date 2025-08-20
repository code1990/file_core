# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
from pathlib import Path
import numpy as np

DB_PATH = r"../stock.db"  # ← 修改为你的 SQLite 路径


def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def validate_combo(conn, combo_name, hold_days=3, target=0.01):
    """
    回测某个组合信号的实盘收益
    假设信号日尾盘买入 -> 次日开盘建仓
    - 第1天尾盘收益 >= target → 清仓
    - 否则继续持有；第2天尾盘收益 >= target → 清仓
    - 否则继续持有；第3天尾盘收益 >= target → 清仓
    - 三天都未达标 → 第3天尾盘强制清仓

    :param conn: sqlite3 连接
    :param combo_name: 组合名称
    :param hold_days: 最大持仓天数
    :param target: 达标收益率 (例如 0.01 = 1%)
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

        # 2) 提取未来 N 天行情（必须有 hold_days 天）
        df = pd.read_sql(f"""
            SELECT trade_date, open, close
            FROM t_stock_daily
            WHERE stock_code='{stock}'
              AND trade_date > {date}
            ORDER BY trade_date ASC
            LIMIT {hold_days}
        """, conn)

        if len(df) < hold_days:
            continue  # 样本不足，跳过

        buy_price = df.iloc[0]["open"]  # 信号日次日开盘买入
        pnl = None

        # 3) 滚动检查每天尾盘收益
        for i in range(len(df)):
            day_close = df.iloc[i]["close"]
            ret = (day_close - buy_price) / buy_price

            if ret >= target:  # 达标清仓
                pnl = ret
                break
            pnl = ret  # 没达标，更新到最后一天

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

    combo_name = "简单买点&绝对底部&进攻"  # ✅ 你要验证的组合

    report = validate_combo(conn, combo_name, hold_days=3, target=0.01)

    if report:
        print("\n===== 回测报告 =====")
        for k, v in report.items():
            print(f"{k}: {v}")
    else:
        print("没有得到回测结果")

    conn.close()


if __name__ == "__main__":
    main()
