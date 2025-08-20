# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
from pathlib import Path
import numpy as np

DB_PATH = r"../stock.db"  # ← 修改为你的 SQLite 路径


def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def validate_combo(conn, combo_name, hold_days=3, stop_loss=-0.03, target=0.01):
    log(f"验证组合 {combo_name} 的实盘收益 ...")

    sig_hits = pd.read_sql(f"""
        SELECT stock_code, trade_date
        FROM t_stock_signal_3
        WHERE combo_name='{combo_name}'
    """, conn)

    results, holding_days, hit_target_flags = [], [], []

    for _, row in sig_hits.iterrows():
        stock, date = row["stock_code"], row["trade_date"]

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
        pnl, days_held, hit_target = None, hold_days, False
        max_dd = 0

        for i, r in df.iterrows():
            ret = (r["close"] - buy_price) / buy_price
            max_dd = min(max_dd, ret)  # 未来最大回撤
            if ret < stop_loss:
                pnl, days_held = ret, i + 1
                break
            if ret >= target:
                pnl, days_held, hit_target = ret, i + 1, True
                break
            pnl = ret  # 如果都不满足，就拿到最后

        results.append(pnl)
        holding_days.append(days_held)
        hit_target_flags.append(hit_target)

    if not results:
        log("❌ 没有足够的样本")
        return None

    results = pd.Series(results)
    holding_days = pd.Series(holding_days)

    report = {
        "combo_name": combo_name,
        "n_trades": int(len(results)),
        "win_ratio": float((results > 0).mean()),
        "avg_return": float(results.mean()),
        "max_return": float(results.max()),
        "min_return": float(results.min()),
        "avg_holding_days": float(holding_days.mean()),
        "hit_target_ratio": float(np.mean(hit_target_flags)),
        "hold_3day_ratio": float(np.mean(holding_days == 3)),
    }

    # 止盈分布
    for d in range(1, hold_days + 1):
        report[f"hit_day{d}_ratio"] = float(np.mean(holding_days == d))

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
