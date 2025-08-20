# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
import time

DB_PATH = r"../stock.db"
OUT_FILE = Path("../data/combo.xls")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def validate_combo(sig_hits, daily_df, combo_name, hold_days=3, stop_loss=-0.03, target=0.01):
    """单个组合的回测"""
    results, holding_days, hit_target_flags = [], [], []

    for _, row in sig_hits.iterrows():
        stock, date = row["stock_code"], row["trade_date"]

        # 未来 hold_days 天行情
        df = daily_df[(daily_df["trade_date"] > date)].head(hold_days)
        if df.empty:
            continue

        buy_price = df.iloc[0]["open"]
        pnl, days_held, hit_target = None, hold_days, False

        for i, r in enumerate(df.itertuples(index=False), start=1):
            ret = (r.close - buy_price) / buy_price
            if ret < stop_loss:
                pnl, days_held = ret, i
                break
            if ret >= target:
                pnl, days_held, hit_target = ret, i, True
                break
            pnl = ret

        results.append(pnl)
        holding_days.append(days_held)
        hit_target_flags.append(hit_target)

    if not results:
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
        "hold_3day_ratio": float(np.mean(holding_days == hold_days)),
    }
    for d in range(1, hold_days + 1):
        report[f"hit_day{d}_ratio"] = float(np.mean(holding_days == d))

    return report


def main():
    conn = sqlite3.connect(DB_PATH)

    # 所有组合
    combos = pd.read_sql("SELECT DISTINCT combo_name FROM t_combo_eval", conn)["combo_name"].tolist()
    log(f"共 {len(combos)} 个组合需要回测验证 ...")

    # 所有股票代码
    stocks = pd.read_sql("SELECT DISTINCT stock_code FROM t_stock_daily", conn)["stock_code"].tolist()
    log(f"共 {len(stocks)} 只股票需要处理 ...")

    sig_df = pd.read_sql("SELECT stock_code, trade_date, combo_name FROM t_stock_signal_3", conn)

    reports = []
    for s_idx, stock in enumerate(stocks, start=1):
        if s_idx % 100 == 0 or s_idx == 1 or s_idx == len(stocks):
            log(f"[{s_idx}/{len(stocks)}] 处理股票 {stock} ...")

        # 当前股票行情
        daily_df = pd.read_sql(
            "SELECT trade_date, open, close FROM t_stock_daily WHERE stock_code=? ORDER BY trade_date ASC",
            conn,
            params=(stock,)
        )

        # 当前股票的信号
        stock_sigs = sig_df[sig_df["stock_code"] == stock]
        if stock_sigs.empty:
            continue

        for combo_name in combos:
            sig_hits = stock_sigs[stock_sigs["combo_name"] == combo_name]
            if sig_hits.empty:
                continue
            rep = validate_combo(sig_hits, daily_df, combo_name)
            if rep:
                reports.append(rep)

    if not reports:
        log("❌ 没有结果")
        return

    result_df = pd.DataFrame(reports)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    result_df.to_excel(OUT_FILE, index=False)
    log(f"✅ 回测完成，结果已保存到 {OUT_FILE}")


if __name__ == "__main__":
    main()
