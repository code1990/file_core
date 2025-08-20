# -*- coding: utf-8 -*-
"""
基于 SQLite 表/视图训练：t_stock_signal 透视为特征，t_stock_label_1 为标签
- 特征：同一 (trade_date, stock_code) 下，不同 signal_name 的 signal_value（缺省补 0）
- 标签：视图 t_stock_label_1 的 label（由 t_stock_stat 的 v_1/2/3_percent>=1% 构成）
- 切分：严格按日期先后切分，防止未来泄露
- 预测：对最新交易日的所有股票给出上涨概率
"""
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.utils import shuffle
import joblib

# ===================== 配置 =====================
DB_PATH = r"../stock.db"   # ←← 修改为你的 SQLite 文件路径
MODEL_PATH = r"../train/rf_model_stock.pkl"
TOPN_PREDICT = 50          # 预测时输出 Top N
min_df = 10                # 仅保留出现次数 >= min_df 的 signal 列（控维/防过拟合），可调
random_state = 42

# ============== 工具函数：加载并构造特征/标签 ==============
def load_feature_label(conn, use_min_df=True):
    """
    从 SQLite 读取 t_stock_signal + t_stock_label_1，构造训练用宽表
    返回：
        X_df: 特征 DataFrame（含 trade_date, stock_code 作为索引列）
        y_df: 标签 DataFrame（trade_date, stock_code, label）
    """
    # 1) 读取标签（视图已定义）
    y_df = pd.read_sql_query(
        """
        SELECT trade_date, stock_code, label
        FROM t_stock_label_1
        """,
        conn
    )

    # 2) 读取信号明细
    sig_df = pd.read_sql_query(
        """
        SELECT trade_date, stock_code, signal_name, signal_value
        FROM t_stock_signal
        """,
        conn
    )

    if sig_df.empty or y_df.empty:
        raise RuntimeError("信号表或标签视图为空，请检查数据准备。")

    # 3) 透视：每个 (trade_date, stock_code) 一行，每种 signal_name 一列
    #    值为 signal_value（默认=1），缺失补 0
    #    若同一键下同名 signal 多条，求和或取最大都可；这里用 max 防重复
    wide = (
        sig_df.pivot_table(
            index=["trade_date", "stock_code"],
            columns="signal_name",
            values="signal_value",
            aggfunc="max",
            fill_value=0.0,
        )
        .reset_index()
    )

    # 4) 可选：按列统计出现次数（>0 视为出现），过滤低频信号列以降维
    if use_min_df:
        # 出现过（>0）的样本计数
        appear_counts = (wide.drop(columns=["trade_date", "stock_code"]) > 0).sum(axis=0)
        keep_cols = appear_counts[appear_counts >= min_df].index.tolist()
        # 如果全部被过滤，为防止空特征，这里保底保留最多的前 100 列
        if len(keep_cols) == 0:
            keep_cols = appear_counts.sort_values(ascending=False).head(100).index.tolist()
        wide = pd.concat(
            [wide[["trade_date", "stock_code"]], wide[keep_cols]],
            axis=1
        )

    # 5) 关联标签
    df = pd.merge(
        wide,
        y_df,
        on=["trade_date", "stock_code"],
        how="inner",
        validate="one_to_one",
    )

    # 6) 输出特征与标签
    feature_cols = [c for c in df.columns if c not in ("trade_date", "stock_code", "label")]
    X_df = df[["trade_date", "stock_code"] + feature_cols].copy()
    y = df["label"].astype(int).copy()

    return X_df, y, feature_cols


def temporal_train_test_split(X_df, y, test_ratio=0.3):
    """
    按日期划分训练/测试集（时间先后），避免未来信息泄露
    """
    # 按日期排序
    X_df = X_df.sort_values("trade_date").reset_index(drop=True)
    y = y.loc[X_df.index]  # 对齐

    # 找到分割点
    dates = X_df["trade_date"].unique()
    split_idx = int(len(dates) * (1 - test_ratio))
    split_date = dates[split_idx] if split_idx < len(dates) else dates[-1]

    train_mask = X_df["trade_date"] < split_date
    test_mask = X_df["trade_date"] >= split_date

    X_train = X_df.loc[train_mask].drop(columns=["trade_date", "stock_code"]).values
    X_test  = X_df.loc[test_mask ].drop(columns=["trade_date", "stock_code"]).values
    y_train = y.loc[train_mask].values
    y_test  = y.loc[test_mask ].values

    return X_train, X_test, y_train, y_test, split_date


# ============== 训练、评估与保存模型 ==============
def train_and_eval(conn):
    # 1) 加载数据
    X_df, y, feature_cols = load_feature_label(conn, use_min_df=True)

    # 2) 时间切分
    X_train, X_test, y_train, y_test, split_date = temporal_train_test_split(X_df, y, test_ratio=0.30)

    # 3) 训练模型（类别不平衡时可 balanced）
    clf = RandomForestClassifier(
        n_estimators=400,
        max_depth=None,
        min_samples_split=4,
        n_jobs=-1,
        random_state=random_state,
        class_weight="balanced"
    )
    clf.fit(X_train, y_train)

    # 4) 评估
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, digits=4)
    print(f"Time split date (test from): {split_date}")
    print("Accuracy:", acc)
    print(report)

    # 5) 特征重要性（Top 30）
    importances = clf.feature_importances_
    fi = pd.DataFrame({"feature": feature_cols, "importance": importances})
    fi = fi.sort_values("importance", ascending=False)
    print("\nTop-30 Feature Importances:")
    print(fi.head(30).to_string(index=False))

    # 6) 保存模型与特征列
    joblib.dump({"model": clf, "feature_cols": feature_cols}, MODEL_PATH)
    print(f"\n模型已保存到: {MODEL_PATH}")

    return clf, feature_cols


# ============== 预测最近交易日（组合选股器） ==============
def predict_latest_day(conn, clf, feature_cols):
    """
    取 t_stock_signal 中最新 trade_date 的所有股票，构造特征并输出上涨概率 TopN
    """
    # 最新交易日
    latest_row = pd.read_sql_query(
        "SELECT MAX(trade_date) AS max_dt FROM t_stock_signal",
        conn
    )
    latest_dt = int(latest_row.iloc[0, 0])

    # 取该日全部信号
    sig_df = pd.read_sql_query(
        """
        SELECT trade_date, stock_code, signal_name, signal_value
        FROM t_stock_signal
        WHERE trade_date = ?
        """,
        conn,
        params=(latest_dt,)
    )
    if sig_df.empty:
        print("最新交易日无信号，无法预测。")
        return None

    # 透视为宽表
    wide = sig_df.pivot_table(
        index=["trade_date", "stock_code"],
        columns="signal_name",
        values="signal_value",
        aggfunc="max",
        fill_value=0.0,
    ).reset_index()

    # 只保留训练中用到的列（缺失的补 0；多余的忽略）
    # 确保列顺序与训练一致
    for col in feature_cols:
        if col not in wide.columns:
            wide[col] = 0.0

    X_new = wide[feature_cols].values
    proba = clf.predict_proba(X_new)[:, 1]

    out = wide[["trade_date", "stock_code"]].copy()
    out["pred_up_prob"] = proba
    out = out.sort_values("pred_up_prob", ascending=False).reset_index(drop=True)

    print(f"\n=== {latest_dt} 预测Top{TOPN_PREDICT} ===")
    print(out.head(TOPN_PREDICT).to_string(index=False))

    return out


# ===================== 主流程 =====================
if __name__ == "__main__":
    # 建议 SQLite 打开 WAL，且 synchronous=NORMAL 已在你的 schema 中设置
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"未找到数据库文件：{DB_PATH}")

    with sqlite3.connect(DB_PATH) as conn:
        # 提升读取性能
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA mmap_size=30000000000;")  # 可按机器内存调整（30GB 示例）
        conn.execute("PRAGMA cache_size=-500000;")      # 负数=KB，约 500MB 缓存

        clf, feature_cols = train_and_eval(conn)
        _ = predict_latest_day(conn, clf, feature_cols)
