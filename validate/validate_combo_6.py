# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from tqdm import tqdm  # ✅ 进度条

DB_PATH = r"../stock.db"
DATA_DIR = Path("../data")
SIGNALS2_FILE = DATA_DIR / "signals2.parquet"
SIGNALS3_FILE = DATA_DIR / "signals3.parquet"
DAILY_FILE = DATA_DIR / "daily.parquet"
OUTPUT_FILE = DATA_DIR / "combo.xls"


def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def validate_combo(signals_df, daily_dict, combo_name, hold_days=3, stop_loss=-0.03, target=0.01):
    """验证单个组合"""
    sig_hits = signals_df.query("combo_name == @combo_name")[["stock_code", "trade_date"]]
    results, holding_days, hit_target_flags = [], [], []

    for _, row in sig_hits.iterrows():
        stock, date = row["stock_code"], row["trade_date"]
        if stock not in daily_dict:
            continue

        df = daily_dict[stock]
        df_future = df[df["trade_date"] > date].head(hold_days)
        if df_future.empty:
            continue

        buy_price = df_future.iloc[0]["open"]
        if buy_price <= 0 or np.isnan(buy_price):
            continue

        pnl, days_held, hit_target = None, hold_days, False

        for i, r in df_future.iterrows():
            if r["close"] <= 0 or np.isnan(r["close"]):
                continue  # 跳过坏数据

            ret = (r["close"] - buy_price) / buy_price

            if ret < stop_loss:
                pnl, days_held = ret, i + 1
                break
            if ret >= target:
                pnl, days_held, hit_target = ret, i + 1, True
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
    # === 1. 加载缓存数据 ===
    log("加载缓存数据 ...")
    signals2 = pd.read_parquet(SIGNALS2_FILE)
    signals3 = pd.read_parquet(SIGNALS3_FILE)
    daily = pd.read_parquet(DAILY_FILE)

    # daily 建字典缓存
    log("构建 daily 字典缓存 ...")
    daily_dict = {code: df.sort_values("trade_date").reset_index(drop=True)
                  for code, df in daily.groupby("stock_code")}
    del daily  # 节省内存

    # === 2. 加载需要验证的组合 ===
    conn = sqlite3.connect(DB_PATH)
    combos = pd.read_sql("SELECT combo_type, combo_name FROM t_combo_eval", conn)
    conn.close()

    combos2 = combos[combos["combo_type"] == "p2"]["combo_name"].unique().tolist()
    combos3 = combos[combos["combo_type"] == "p3"]["combo_name"].unique().tolist()

    log(f"需要回测的组合: p2={len(combos2)}, p3={len(combos3)}")

    # === 3. 执行回测 ===
    reports = []

    for combo in tqdm(combos2, desc="回测2组合"):
        rpt = validate_combo(signals2, daily_dict, combo)
        if rpt:
            rpt["combo_type"] = "p2"
            reports.append(rpt)

    for combo in tqdm(combos3, desc="回测3组合"):
        rpt = validate_combo(signals3, daily_dict, combo)
        if rpt:
            rpt["combo_type"] = "p3"
            reports.append(rpt)

    # === 4. 保存结果 ===
    df_report = pd.DataFrame(reports)
    df_report.to_excel(OUTPUT_FILE, index=False)
    log(f"✅ 回测完成，结果已保存到 {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
