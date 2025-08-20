# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from pathlib import Path
import time

# 缓存文件路径
DATA_DIR = Path("../data")
SIGNALS2_FILE = DATA_DIR / "signals2.parquet"
SIGNALS3_FILE = DATA_DIR / "signals3.parquet"
DAILY_FILE = DATA_DIR / "daily.parquet"
OUT_FILE = DATA_DIR / "combo.xls"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def validate_combo(signals: pd.DataFrame, daily: pd.DataFrame,
                   combo_name: str, hold_days=3, stop_loss=-0.03, target=0.01):
    """
    回测单个组合
    """
    sig_hits = signals[signals["combo_name"] == combo_name][["stock_code", "trade_date"]]
    results, holding_days, hit_target_flags = [], [], []

    for _, row in sig_hits.iterrows():
        stock, date = row["stock_code"], row["trade_date"]

        df = daily[(daily["stock_code"] == stock) & (daily["trade_date"] > date)].sort_values("trade_date").head(hold_days)
        if df.empty:
            continue

        buy_price = df.iloc[0]["open"]
        pnl, days_held, hit_target = None, hold_days, False

        for i, r in df.iterrows():
            ret = (r["close"] - buy_price) / buy_price
            if ret < stop_loss:  # 止损
                pnl, days_held = ret, i + 1
                break
            if ret >= target:  # 止盈
                pnl, days_held, hit_target = ret, i + 1, True
                break
            pnl = ret  # 否则拿到最后

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
        "hold_3day_ratio": float(np.mean(holding_days == 3)),
    }

    for d in range(1, hold_days + 1):
        report[f"hit_day{d}_ratio"] = float(np.mean(holding_days == d))

    return report


def main():
    log("加载缓存文件 ...")
    signals2 = pd.read_parquet(SIGNALS2_FILE)
    signals3 = pd.read_parquet(SIGNALS3_FILE)
    daily = pd.read_parquet(DAILY_FILE)

    log(f"信号2：{len(signals2)} 条，信号3：{len(signals3)} 条，日线：{len(daily)} 条")

    results = []

    # 回测 signals2
    log("开始回测 2组合 ...")
    for combo_name in signals2["combo_name"].unique():
        rpt = validate_combo(signals2, daily, combo_name)
        if rpt:
            rpt["combo_type"] = "p2"
            results.append(rpt)

    # 回测 signals3
    log("开始回测 3组合 ...")
    for combo_name in signals3["combo_name"].unique():
        rpt = validate_combo(signals3, daily, combo_name)
        if rpt:
            rpt["combo_type"] = "p3"
            results.append(rpt)

    df_result = pd.DataFrame(results)
    log(f"共生成 {len(df_result)} 条回测结果")

    df_result.to_excel(OUT_FILE, index=False)
    log(f"结果已保存到 {OUT_FILE}")


if __name__ == "__main__":
    main()
