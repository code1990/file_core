# pip install pandas numpy scipy
import pandas as pd
import numpy as np
from scipy.signal import find_peaks

# ===== 1) 趋势线：用 EMA（也可改MA/更复杂的滤波）=====
def compute_trend(df: pd.DataFrame, span: int = 10) -> pd.DataFrame:
    out = df.copy()
    out["trend"] = out.groupby("stock_code", group_keys=False)["close"]\
                      .apply(lambda s: s.ewm(span=span, adjust=False).mean())
    return out

# ===== 2) 找极小值（谷底）=====
def find_trough_indices(trend_vals: np.ndarray, prominence=0.0, distance=5):
    """
    返回极小值索引数组。distance 控制极值间最小间隔，prominence 控制极值显著性（去噪）。
    """
    troughs, _ = find_peaks(-trend_vals, prominence=prominence, distance=distance)
    return troughs

# ===== 3) 评估某个“谷底”后的反弹表现 =====
def eval_rebound_metrics(close: pd.Series, i: int, horizon: int = 5):
    """
    i: 谷底在序列中的位置
    返回：
      next_ret: 次日收益（若无次日，NaN）
      max5_ret: 未来1..horizon日的最大反弹收益（若不足horizon，按实际可得天数；若无未来数据，NaN）
    """
    if i >= len(close) - 1:
        return np.nan, np.nan
    base = close.iloc[i]
    # 次日收益
    next_ret = close.iloc[i+1] / base - 1
    # 未来horizon日最大反弹
    j_end = min(i + horizon, len(close) - 1)
    if j_end == i:
        return next_ret, np.nan
    future = close.iloc[i+1:j_end+1]
    max5_ret = (future.max() / base) - 1
    return next_ret, max5_ret

# ===== 4) 主流程：对全市场逐股计算“抄底信号表现”=====
def scan_bottom_bounce(df_price: pd.DataFrame,
                       ema_span: int = 10,
                       trough_prominence: float = 0.0,
                       trough_distance: int = 5,
                       day1_thr: float = 0.01,
                       day5_thr: float = 0.05,
                       day5_horizon: int = 5):
    """
    返回两个DataFrame：
      signals：逐个“谷底信号”的表现与是否达标
      summary：按股票聚合的统计（样本、达标率、Wilson下界等）
    """
    df = compute_trend(df_price, span=ema_span)\
           .sort_values(["stock_code","trade_date"]).reset_index(drop=True)

    records = []  # 每个谷底信号的记录
    for code, g in df.groupby("stock_code"):
        trend_vals = g["trend"].values
        trough_idx = find_trough_indices(trend_vals,
                                         prominence=trough_prominence,
                                         distance=trough_distance)
        if len(trough_idx) == 0:
            continue
        for i in trough_idx:
            t = g.iloc[i]["trade_date"]
            nxt, m5 = eval_rebound_metrics(g["close"], i, horizon=day5_horizon)
            hit_d1 = (nxt >= day1_thr) if pd.notna(nxt) else False
            hit_d5 = (m5 >= day5_thr) if pd.notna(m5) else False
            records.append({
                "stock_code": code,
                "trade_date_trough": t,
                "close_at_trough": float(g.iloc[i]["close"]),
                "ema_span": ema_span,
                "next_day_ret": float(nxt) if pd.notna(nxt) else np.nan,
                f"max_{day5_horizon}d_ret": float(m5) if pd.notna(m5) else np.nan,
                "hit_day1_ge_1pct": bool(hit_d1),
                f"hit_{day5_horizon}d_ge_{int(day5_thr*100)}pct": bool(hit_d5),
            })

    signals = pd.DataFrame(records)
    if signals.empty:
        # 返回空表占位
        return signals, pd.DataFrame(columns=[
            "stock_code","samples","hit1_cnt","hit5_cnt",
            "hit1_rate","hit5_rate","hit1_wilson_low","hit5_wilson_low"
        ])

    # ===== 5) 汇总统计（命中率 & Wilson下界更稳健）=====
    def wilson_lower_bound(hits, n, z=1.96):
        if n == 0: return 0.0
        p = hits / n
        denom = 1 + z**2/n
        center = p + z**2/(2*n)
        margin = z * np.sqrt(p*(1-p)/n + z**2/(4*n**2))
        return max(0.0, (center - margin) / denom)

    grp = signals.groupby("stock_code")
    summary = grp.agg(
        samples=("stock_code","size"),
        hit1_cnt=("hit_day1_ge_1pct","sum"),
        hit5_cnt=(f"hit_{day5_horizon}d_ge_{int(day5_thr*100)}pct","sum"),
    ).reset_index()
    summary["hit1_rate"] = summary["hit1_cnt"] / summary["samples"]
    summary["hit5_rate"] = summary["hit5_cnt"] / summary["samples"]
    summary["hit1_wilson_low"] = [wilson_lower_bound(h, n) for h, n in zip(summary["hit1_cnt"], summary["samples"])]
    summary["hit5_wilson_low"] = [wilson_lower_bound(h, n) for h, n in zip(summary["hit5_cnt"], summary["samples"])]

    # 排序建议：优先看5日反弹的稳健下界，其次样本、1日表现
    summary = summary.sort_values(
        by=["hit5_wilson_low","hit5_rate","samples","hit1_rate"],
        ascending=[False, False, False, False]
    ).reset_index(drop=True)

    return signals, summary

# ===== 6) 用法示例 =====
# 假设你已经准备好 df_price（stock_code, trade_date, close）
# df_price["trade_date"] = pd.to_datetime(df_price["trade_date"])
# signals, summary = scan_bottom_bounce(
#     df_price,
#     ema_span=10,              # 趋势线平滑
#     trough_prominence=0.0,    # 极小值显著性（如有噪声可设0.5~2）
#     trough_distance=5,        # 极小值最小间隔（天）
#     day1_thr=0.01,            # 次日 ≥1%
#     day5_thr=0.05,            # 5日内最大反弹 ≥5%
#     day5_horizon=5
# )
# print(signals.head())  # 每个“抄底信号”的表现
# print(summary.head())  # 哪些个股“抄底后反弹”更稳定
