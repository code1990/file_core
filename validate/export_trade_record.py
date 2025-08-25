# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = r"../stock.db"  # 修改为你的sqlite路径
EXPORT_DIR = Path("../data")
EXPORT_DIR.mkdir(exist_ok=True)


def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def export_combo_trades(conn, combo_name: str):
    """导出某个组合的交易明细到Excel"""
    signals = combo_name.split("&")
    log(f"处理组合: {combo_name}, 信号={signals}")

    # 1) 取第一个信号
    query = f"""
        SELECT stock_code, trade_date
        FROM t_stock_signal
        WHERE signal_name = ?
    """
    base_df = pd.read_sql(query, conn, params=(signals[0],))

    # 2) 依次取交集
    for sig in signals[1:]:
        df = pd.read_sql(query, conn, params=(sig,))
        base_df = pd.merge(base_df, df, on=["stock_code", "trade_date"], how="inner")

    if base_df.empty:
        log(f"❌ {combo_name} 没有交易记录")
        return

    log(f"组合 {combo_name} 命中记录数={len(base_df)}")

    # 3) 关联 t_stock_stat
    stat_df = pd.read_sql(
        f"""
        SELECT * FROM t_stock_stat
        WHERE (stock_code, trade_date) IN (
            SELECT stock_code, trade_date FROM t_stock_signal WHERE signal_name IN ({','.join(['?'] * len(signals))})
        )
        """,
        conn,
        params=signals,
    )

    # 仅保留命中的行
    merged = pd.merge(
        base_df, stat_df, on=["stock_code", "trade_date"], how="inner"
    )

    if merged.empty:
        log(f"⚠️ {combo_name} 在 t_stock_stat 中没有匹配到数据")
        return

    # 4) 导出 Excel
    out_file = EXPORT_DIR / f"{combo_name}.xls"
    merged.to_excel(out_file, index=False)
    log(f"✅ 导出完成: {out_file}")


def main():
    conn = sqlite3.connect(DB_PATH)

    combo_list = [
        "三枪&绝对底部&趋势为王起涨",
        "绝对底部&趋势为王起涨&进攻",
        "三枪&绝对底部&进攻",
        "绝对底部&趋势为王起涨&趋势为王钱袋",
        "出击&绝对底部&趋势为王钱袋",
        "出击&绝对底部&进攻",
        "短买&绝对底部&趋势为王起涨",
        "绝对底部&追涨买入",
        "三枪&简单买点&绝对底部",
        "出击&绝对底部&趋势为王起涨",
        "绝对底部&趋势为王起涨",
        "简单买点&绝对底部&趋势为王起涨",
        "绝对底部&趋势为王钱袋&进攻",
        "MACD的倔强起&三枪&绝对底部",
        "出击&绝对底部",
        "简单买点&绝对底部&进攻",
        "短买&绝对底部&进攻",
        "三枪&绝对底部",
        "三枪&短买&绝对底部",
        "绝对底部&进攻",

    ]

    for combo in combo_list:
        export_combo_trades(conn, combo)

    conn.close()


if __name__ == "__main__":
    main()
