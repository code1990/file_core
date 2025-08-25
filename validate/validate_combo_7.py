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


def validate_combo(sig_hits, daily_dict, stat_dict, combo_name,
                   hold_days=3, stop_loss=-0.03, target=0.01):
    """
    验证单个组合，带扩展指标：
    - 剔除 v_0_percent > 5% 的追高交易
    - 年均交易次数 annualized_trades
    - 预期年收益率 expected_yearly_return
    - 创业板 vs 主板分市场胜率
    """
    results, holding_days, hit_target_flags = [], [], []
    dates, boards = [], []
    n_filtered = 0

    for _, row in sig_hits.iterrows():
        stock, date = row["stock_code"], row["trade_date"]

        # 过滤追高 (v_0_percent > 5%)
        v0 = stat_dict.get((stock, date))
        if v0 is not None and v0 > 5:
            n_filtered += 1
            continue

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

        for i, r in enumerate(df_future.itertuples(), start=1):
            if r.close <= 0 or np.isnan(r.close):
                continue
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
        dates.append(date)

        # 判断市场
        if stock.startswith("300"):
            boards.append("创业板")
        elif stock.startswith("688"):
            boards.append("科创板")
        else:
            boards.append("主板")

    if not results:
        return None

    results = pd.Series(results)
    holding_days = pd.Series(holding_days)

    # 年均交易次数
    years = max(1, (max(dates) - min(dates)) // 10000)  # 粗略按 YYYYMMDD → 年份差
    annualized_trades = len(results) / years

    # 预期年收益率（复利近似）
    expected_yearly_return = (1 + results.mean()) ** annualized_trades - 1

    # 分市场胜率
    df_board = pd.DataFrame({"board": boards, "result": results > 0})
    win_ratio_chuangye = df_board[df_board.board == "创业板"]["result"].mean()
    win_ratio_main = df_board[df_board.board == "主板"]["result"].mean()

    report = {
        "combo_name": combo_name,
        "n_trades": int(len(results) + n_filtered),
        "n_used": int(len(results)),
        "n_filtered": int(n_filtered),
        "filter_ratio": round(n_filtered / (len(results) + n_filtered + 1e-9), 4),
        "win_ratio": float((results > 0).mean()),
        "avg_return": float(results.mean()),
        "max_return": float(results.max()),
        "min_return": float(results.min()),
        "avg_holding_days": float(holding_days.mean()),
        "hit_target_ratio": float(np.mean(hit_target_flags)),
        "hold_3day_ratio": float(np.mean(holding_days == hold_days)),
        "annualized_trades": float(annualized_trades),
        "expected_yearly_return": float(expected_yearly_return),
        "win_ratio_chuangye": float(win_ratio_chuangye) if not np.isnan(win_ratio_chuangye) else None,
        "win_ratio_main": float(win_ratio_main) if not np.isnan(win_ratio_main) else None,
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
    stat_df = pd.read_sql("SELECT stock_code, trade_date, v_0_percent FROM t_stock_stat", conn)
    conn.close()

    combos2 = combos[combos["combo_type"] == "p2"]["combo_name"].unique().tolist()
    combos3 = combos[combos["combo_type"] == "p3"]["combo_name"].unique().tolist()

    log(f"需要回测的组合: p2={len(combos2)}, p3={len(combos3)}")

    # 预切分 signals，避免每次 query
    signals2_groups = {k: v[["stock_code", "trade_date"]] for k, v in signals2.groupby("combo_name")}
    signals3_groups = {k: v[["stock_code", "trade_date"]] for k, v in signals3.groupby("combo_name")}

    # 把 stat_df 变 dict，加速索引
    stat_dict = {(row.stock_code, row.trade_date): row.v_0_percent for row in stat_df.itertuples()}

    # === 3. 执行回测 ===
    reports = []

    for combo in tqdm(combos2, desc="回测2组合"):
        sig_hits = signals2_groups.get(combo)
        if sig_hits is None:
            continue
        rpt = validate_combo(sig_hits, daily_dict, stat_dict, combo)
        if rpt:
            rpt["combo_type"] = "p2"
            reports.append(rpt)

    for combo in tqdm(combos3, desc="回测3组合"):
        sig_hits = signals3_groups.get(combo)
        if sig_hits is None:
            continue
        rpt = validate_combo(sig_hits, daily_dict, stat_dict, combo)
        if rpt:
            rpt["combo_type"] = "p3"
            reports.append(rpt)

    # === 4. 保存结果 ===
    df_report = pd.DataFrame(reports)
    df_report.to_excel(OUTPUT_FILE, index=False)
    log(f"✅ 回测完成，结果已保存到 {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
