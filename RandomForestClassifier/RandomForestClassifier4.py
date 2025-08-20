# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score, roc_auc_score, average_precision_score
import joblib
import datetime

DB_PATH = r"../stock.db"   # ← 修改为你的 SQLite 文件路径
MODEL_PATH = r"../train/rf_model_stock.pkl"
TOPN_PREDICT = 50
min_df = 10
random_state = 42

USE_PAIR = True      # 两两组合
USE_TRIPLE = False   # 三三组合关闭

def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")


# === 新增：22组合评估函数 ===
def eval_combos(conn, acc_thresh=0.55, auc_thresh=0.60):
    """
    评估每个22组合在预测中的表现，结果存入 t_combo_eval
    """
    from sklearn.metrics import accuracy_score, roc_auc_score, average_precision_score

    log("开始评估两两组合表现 ...")
    y_df = pd.read_sql_query("SELECT trade_date, stock_code, label FROM t_stock_label_1", conn)
    pair_df = pd.read_sql_query("SELECT trade_date, stock_code, combo_name, combo_value FROM t_stock_signal_2", conn)

    if pair_df.empty:
        log("⚠️ t_stock_signal_2 为空，跳过评估")
        return

    results = []
    for cname, sub in pair_df.groupby("combo_name"):
        df = pd.merge(sub, y_df, on=["trade_date", "stock_code"], how="inner")
        if df.empty or df["combo_value"].sum() < 20:  # 出现次数太少跳过
            continue

        y_true = df["label"].values
        y_pred = (df["combo_value"] > 0).astype(int).values

        acc = accuracy_score(y_true, y_pred)
        try:
            auc = roc_auc_score(y_true, df["combo_value"].values)
        except:
            auc = None
        try:
            pr_auc = average_precision_score(y_true, df["combo_value"].values)
        except:
            pr_auc = None

        if (acc >= acc_thresh) or (auc is not None and auc >= auc_thresh):
            results.append(("p2", cname, len(df), acc, auc, pr_auc, y_true.mean()))

    if results:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS t_combo_eval (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                combo_type TEXT,
                combo_name TEXT,
                n_samples INT,
                accuracy REAL,
                auc REAL,
                pr_auc REAL,
                pos_rate REAL,
                create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.executemany("""
            INSERT INTO t_combo_eval(combo_type, combo_name, n_samples, accuracy, auc, pr_auc, pos_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, results)
        conn.commit()
        log(f"组合评估完成，写入 {len(results)} 条记录")
    else:
        log("⚠️ 没有组合满足阈值条件")


# === 信号透视函数 ===
def pivot_signals(df_long: pd.DataFrame, name_col: str, value_col: str, prefix: str, use_min_df=True):
    if df_long.empty:
        return pd.DataFrame(columns=["trade_date", "stock_code"])
    wide = pd.crosstab(
        [df_long["trade_date"], df_long["stock_code"]],
        df_long[name_col],
        values=df_long[value_col],
        aggfunc="max"
    ).reset_index()
    cols = [c for c in wide.columns if c not in ("trade_date", "stock_code")]
    if use_min_df and cols:
        appear_counts = (wide[cols] > 0).sum(axis=0)
        keep = appear_counts[appear_counts >= min_df].index.tolist()
        if not keep:
            keep = appear_counts.sort_values(ascending=False).head(min(50, len(appear_counts))).index.tolist()
        wide = pd.concat([wide[["trade_date", "stock_code"]], wide[keep]], axis=1)
        cols = keep
    rename_map = {c: f"{prefix}{c}" for c in cols}
    wide = wide.rename(columns=rename_map)
    return wide


# === 特征加载 ===
def load_feature_label(conn, use_min_df=True):
    # 标签
    log("开始加载标签数据 t_stock_label_1 ...")
    y_df = pd.read_sql_query("SELECT trade_date, stock_code, label FROM t_stock_label_1", conn)
    y_df["trade_date"] = y_df["trade_date"].astype(int)
    y_df["stock_code"] = y_df["stock_code"].astype(str)
    log(f"标签数据加载完成，共 {len(y_df)} 条，交易日数={y_df['trade_date'].nunique()}")

    # 原始信号
    log("开始加载信号数据 t_stock_signal ...")
    sig_df = pd.read_sql_query("SELECT trade_date, stock_code, signal_name, signal_value FROM t_stock_signal", conn)
    sig_df["trade_date"] = sig_df["trade_date"].astype(int)
    sig_df["stock_code"] = sig_df["stock_code"].astype(str)
    log(f"信号数据加载完成，共 {len(sig_df)} 条，信号种类={sig_df['signal_name'].nunique()}")
    if not sig_df.empty:
        top_sig_train = (sig_df.groupby("signal_name")["signal_value"].size().sort_values(ascending=False).head(20))
        log("训练期最常见信号TOP-20：")
        print(top_sig_train)

    log("开始透视原始信号为宽表 ...")
    wide_s = pivot_signals(sig_df, "signal_name", "signal_value", prefix="s_", use_min_df=use_min_df)
    log(f"原始信号透视完成，保留列数={len([c for c in wide_s.columns if c not in ('trade_date','stock_code')])}")

    # 两两组合
    if USE_PAIR:
        log("开始加载两两组合视图 t_stock_signal_2 ...")
        pair_df = pd.read_sql_query("SELECT trade_date, stock_code, combo_name, combo_value FROM t_stock_signal_2", conn)
        if not pair_df.empty:
            pair_df["trade_date"] = pair_df["trade_date"].astype(int)
            pair_df["stock_code"] = pair_df["stock_code"].astype(str)
        log(f"两两组合记录数={len(pair_df)}，组合种类={pair_df['combo_name'].nunique() if not pair_df.empty else 0}")
        log("透视两两组合 ...")
        wide_p2 = pivot_signals(pair_df, "combo_name", "combo_value", prefix="p2_", use_min_df=use_min_df)
    else:
        wide_p2 = pd.DataFrame(columns=["trade_date", "stock_code"])

    # 合并信号
    log("合并信号（原始 + 两两） ...")
    wide = wide_s.set_index(["trade_date", "stock_code"])
    if not wide_p2.empty:
        wide = wide.join(wide_p2.set_index(["trade_date", "stock_code"]), how="left")
    wide = wide.reset_index()
    wide.fillna(0.0, inplace=True)

    # 合并标签
    log("开始合并标签 ...")
    df = pd.merge(wide, y_df, on=["trade_date", "stock_code"], how="inner")
    log(f"合并完成，总样本={len(df)}")

    # 最终只保留信号特征
    feat_cols_all = [c for c in df.columns if c not in ("trade_date","stock_code","label")]
    feature_cols = [c for c in feat_cols_all if not c.startswith("lag1_")]
    log(f"最终使用特征数={len(feature_cols)}（已剔除 lag1_*）")

    return df, feature_cols


# === 时间切分 ===
def temporal_split(df, test_ratio=0.3):
    dates = sorted(df["trade_date"].unique())
    split_idx = int(len(dates) * (1 - test_ratio))
    split_date = dates[split_idx]
    train = df[df["trade_date"] < split_date]
    test  = df[df["trade_date"] >= split_date]
    log(f"时间切分完成：训练集={len(train)}，测试集={len(test)}，切分点={split_date}")
    X_train = train.drop(columns=["trade_date", "stock_code", "label"]).values
    y_train = train["label"].values
    X_test = test.drop(columns=["trade_date", "stock_code", "label"]).values
    y_test = test["label"].values
    return X_train, y_train, X_test, y_test, split_date


# === 训练与评估 ===
def train_and_eval(conn):
    df, feature_cols = load_feature_label(conn)
    X_train, y_train, X_test, y_test, split_date = temporal_split(df)

    log("开始训练随机森林模型 ...")
    clf = RandomForestClassifier(
        n_estimators=500, max_depth=None, min_samples_split=4,
        n_jobs=-1, random_state=random_state, class_weight="balanced_subsample"
    )
    clf.fit(X_train, y_train)
    log("训练完成")

    log("开始评估模型 ...")
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    log(f"测试集准确率={acc:.4f}")
    print(classification_report(y_test, y_pred, digits=4))
    try:
        y_prob = clf.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, y_prob)
        ap = average_precision_score(y_test, y_prob)
        log(f"ROC-AUC={auc:.4f}  PR-AUC={ap:.4f}")
    except Exception as e:
        log(f"计算AUC/PR-AUC失败：{e}")

    fi = pd.DataFrame({"feature": feature_cols, "importance": clf.feature_importances_}).sort_values("importance", ascending=False)
    log("Top-30 特征重要性：")
    print(fi.head(30))

    joblib.dump({"model": clf, "feature_cols": feature_cols}, MODEL_PATH)
    log(f"模型已保存到 {MODEL_PATH}")

    # === 训练完成后评估22组合 ===
    eval_combos(conn)

    return clf, feature_cols


# === 最新交易日预测 ===
def predict_latest_day(conn, clf, feature_cols):
    log("开始获取最新交易日 ...")
    latest_dt = conn.execute("SELECT MAX(trade_date) FROM t_stock_signal").fetchone()[0]
    log(f"最新交易日={latest_dt}")

    sig_df = pd.read_sql_query(
        "SELECT trade_date, stock_code, signal_name, signal_value FROM t_stock_signal WHERE trade_date=?",
        conn, params=(latest_dt,)
    )
    sig_df["trade_date"] = sig_df["trade_date"].astype(int)
    sig_df["stock_code"] = sig_df["stock_code"].astype(str)

    wide = pivot_signals(sig_df, "signal_name", "signal_value", prefix="s_", use_min_df=False)

    if USE_PAIR:
        pair_df = pd.read_sql_query(
            "SELECT trade_date, stock_code, combo_name, combo_value FROM t_stock_signal_2 WHERE trade_date=?",
            conn, params=(latest_dt,)
        )
        if not pair_df.empty:
            pair_df["trade_date"] = pair_df["trade_date"].astype(int)
            pair_df["stock_code"] = pair_df["stock_code"].astype(str)
            wide_p2 = pivot_signals(pair_df, "combo_name", "combo_value", prefix="p2_", use_min_df=False)
            wide = pd.merge(wide, wide_p2, on=["trade_date", "stock_code"], how="left")

    # 缺失列一次性补全
    missing_cols = [c for c in feature_cols if c not in wide.columns]
    if missing_cols:
        add_df = pd.DataFrame(0.0, index=wide.index, columns=missing_cols)
        wide = pd.concat([wide, add_df], axis=1)

    wide = wide[["trade_date", "stock_code"] + feature_cols]
    X_new = wide[feature_cols].values

    proba = clf.predict_proba(X_new)[:, 1]
    out = wide[["trade_date", "stock_code"]].copy()
    out["pred_up_prob"] = proba
    out = out.sort_values("pred_up_prob", ascending=False).reset_index(drop=True)
    print(out.head(TOPN_PREDICT))


# === 主入口 ===
if __name__ == "__main__":
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"数据库文件不存在：{DB_PATH}")
    with sqlite3.connect(DB_PATH) as conn:
        clf, feature_cols = train_and_eval(conn)
        predict_latest_day(conn, clf, feature_cols)
