import pandas as pd
import numpy as np
from scipy.signal import find_peaks
from sqlalchemy import create_engine, text
from math import sqrt

# ========= 数据加载（你也可以直接用已有DataFrame） =========
# 如果直接读MySQL，取消注释并替换连接
# engine = create_engine("mysql+pymysql://user:pwd@host:3306/yourdb?charset=utf8mb4")
# df_p = pd.read_sql("SELECT stock_code, trade_date, close FROM t_ohlc_daily", engine)
# df_sig = pd.read_sql("SELECT stock_code, trade_date FROM t_signal_events WHERE signal='A'", engine)

# 这里用示例伪数据占位（请替换成你的数据源）
# df_p: 必含 ['stock_code','trade_date','close']，按日期升序
# df_sig: 必含 ['stock_code','trade_date'] 为信号A出现的日期
df_p = pd.DataFrame(...)   # TODO 填充
df_sig = pd.DataFrame(...) # TODO 填充

df_p["trade_date"] = pd.to_datetime(df_p["trade_date"])
df_sig["trade_date"] = pd.to_datetime(df_sig["trade_date"])
df_p = df_p.sort_values(["stock_code","trade_date"])

# ========= 1) 构造趋势线（平滑） =========
def ema(series, span=10):
    return series.ewm(span=span, adjust=False).mean()

# 给每个股票生成趋势线
df_p["trend"] = df_p.groupby("stock_code", group_keys=False)["close"].apply(lambda s: ema(s, span=10))

# ========= 2) 找趋势线极值点 =========
def local_extrema(trend_vals, prominence=0.0, distance=5):
    """
    用 find_peaks 找极大值；极小值可以对trend取负再找峰。
    prominence/distance 可按你数据调参，distance~至少相隔N天
    """
    peaks, _ = find_peaks(trend_vals, prominence=prominence, distance=distance)
    troughs, _ = find_peaks(-trend_vals, prominence=prominence, distance=distance)
    return peaks, troughs

# ========= 3) 由极值点切出“上升趋势窗口” =========
def uptrend_windows(dates, trend):
    """
    简便定义：
      - 从一个“低点 -> 随后的高点”为一个上升段；
      - 或者连续低点抬高 / 高点抬高也可扩展（此处先给简洁版）。
    返回: [(t1, t2), ...]
    """
    peaks_idx, troughs_idx = local_extrema(trend.values, prominence=0.0, distance=5)
    troughs_idx = np.sort(troughs_idx)
    peaks_idx = np.sort(peaks_idx)
    wins = []
    # 为每个低点找其后的最近高点，构成上升段
    for ti in troughs_idx:
        # 下一个峰在其后
        hi = peaks_idx[peaks_idx > ti]
        if len(hi) == 0:
            continue
        hi = hi[0]
        t1, t2 = dates.iloc[ti], dates.iloc[hi]
        if trend.iloc[hi] > trend.iloc[ti]:
            wins.append((t1, t2))
    return wins

# ========= 4) 在上升窗口中评估“信号A -> 次日收益≥1%” =========
def next_day_return(close_s):
    # 次日收益: r_{t+1} = (close_{t+1} / close_t) - 1
    return close_s.shift(-1) / close_s - 1

df_p["next_ret"] = df_p.groupby("stock_code", group_keys=False)["close"].apply(next_day_return)

# 合并信号：标记某天是否有A
df_p = df_p.merge(df_sig.assign(has_A=1), on=["stock_code","trade_date"], how="left")
df_p["has_A"] = df_p["has_A"].fillna(0).astype(int)

# ========= 5) 仅在上升窗口统计：每只股票的命中/稳定性 =========
records = []  # 保存每只股票的统计行
for code, g in df_p.groupby("stock_code"):
    g = g.sort_values("trade_date").reset_index(drop=True)
    if g.shape[0] < 30:
        continue
    wins = uptrend_windows(g["trade_date"], g["trend"])
    if not wins:
        continue
    # 在窗口内选出所有出现A信号的行，并查看次日收益≥1%
    hits = 0
    total = 0
    dates_col = g["trade_date"]
    for (t1, t2) in wins:
        mask = (g["trade_date"] >= t1) & (g["trade_date"] <= t2)
        sub = g.loc[mask]
        a_days = sub[sub["has_A"] == 1]
        if a_days.empty:
            continue
        # 次日收益是否>=1%
        # 注意最后一天出信号无次日收益，需剔除
        elig = a_days.dropna(subset=["next_ret"])
        total += len(elig)
        hits  += int((elig["next_ret"] >= 0.01).sum())
    if total == 0:
        continue

    # 命中率 & Wilson置信下界（95%）
    p = hits / total
    z = 1.96
    denom = 1 + z**2 / total
    center = p + z**2/(2*total)
    margin = z * sqrt(p*(1-p)/total + z**2/(4*total**2))
    wilson_low = max(0.0, (center - margin) / denom)

    records.append({
        "stock_code": code,
        "samples": total,
        "hits": hits,
        "hit_rate": p,
        "wilson_low": wilson_low
    })

rank_df = pd.DataFrame(records).sort_values(["wilson_low","hit_rate","samples"], ascending=[False, False, False])
# 你可以设置门槛筛选“稳定发挥”的股票：
stable = rank_df[(rank_df["samples"] >= 5) & (rank_df["wilson_low"] >= 0.5)]  # 示例门槛
print(stable.head(30))

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

def first_up_window(dates: pd.Series, trend: pd.Series,
                    prominence=0.0, distance=5):
    """
    规则：
    1) 取“第一个最小值” t_min（在趋势线上找谷）
    2) 在 t_min 之后，取“第一个最大值” t_max（在趋势线上找峰）
    3) 窗口 = [t_min, t_max]；若不存在 t_max，则返回 None
    参数：
      - prominence/distance 用于去噪，按你的数据酌情调
    返回：(t_min_date, t_max_date) 或 None
    """
    vals = trend.values

    # 极大值（峰）
    peaks, _ = find_peaks(vals, prominence=prominence, distance=distance)
    # 极小值（谷）：对trend取相反数再找峰
    troughs, _ = find_peaks(-vals, prominence=prominence, distance=distance)

    if len(troughs) == 0:
        return None

    t_min_idx = troughs[0]  # 第一次出现的最小值索引
    # 在 t_min 之后的所有最大值
    cand_max = peaks[peaks > t_min_idx]
    if len(cand_max) == 0:
        return None

    t_max_idx = cand_max[0]  # 第一次出现的最大值
    return dates.iloc[t_min_idx], dates.iloc[t_max_idx]

# ==== 用法示例 ====
# df 有两列：trade_date（按升序）、trend（趋势线）
# df = pd.DataFrame({...})
# win = first_up_window(df['trade_date'], df['trend'], prominence=0.2, distance=5)
# if win:
#     print("上升窗口：", win[0], "→", win[1])