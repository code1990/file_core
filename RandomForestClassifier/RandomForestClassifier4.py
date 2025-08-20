# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score, roc_auc_score, average_precision_score
import joblib
import datetime

DB_PATH = r"../stock.db"   # ←← 修改为你的 SQLite 文件路径
MODEL_PATH = r"../train/rf_model_stock.pkl"
TOPN_PREDICT = 50
min_df = 10
random_state = 42

# === NEW: 是否纳入两两/三三组合特征
USE_PAIR = True     # 两两组合 t_stock_signal_2
USE_TRIPLE = True   # 三三组合 t_stock_signal_3

# === NEW: 与 t_stock_daily 一致的量价特征（做 lag1）
LAG_FEATURES = ["percent", "vol", "amount", "close", "high", "low"]

def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

# === NEW: 读取并生成 t_stock_daily 的 lag1 特征
def load_lag_daily(conn):
    log("开始加载量价数据 t_stock_daily（用于 lag1 特征） ...")
    df = pd.read_sql_query(
        "SELECT stock_code, trade_date, percent, vol, amount, close, high, low FROM t_stock_daily",
        conn
    )
    if df.empty:
        log("⚠️ t_stock_daily 为空，无法生成量价滞后特征")
        return None
    df["trade_date"] = df["trade_date"].astype(int)
    df["stock_code"] = df["stock_code"].astype(str)
    df = df.sort_values(["stock_code", "trade_date"])
    df[LAG_FEATURES] = df.groupby("stock_code")[LAG_FEATURES].shift(1)
    df = df.dropna(subset=LAG_FEATURES, how="all").reset_index(drop=True)
    df = df.rename(columns={c: f"lag1_{c}" for c in LAG_FEATURES})
    return df

# === NEW: 通用函数：把（trade_date, stock_code, name, value）长表透视成宽表并加前缀
def pivot_signals(df_long: pd.DataFrame, name_col: str, value_col: str, prefix: str, use_min_df=True):
    if df_long.empty:
        return pd.DataFrame(columns=["trade_date", "stock_code"])
    wide = (df_long.pivot_table(index=["trade_date", "stock_code"],
                                columns=name_col,
                                values=value_col,
                                aggfunc="max",
                                fill_value=0.0)
                    .reset_index())
    # 选列+加前缀
    cols = [c for c in wide.columns if c not in ("trade_date", "stock_code")]
    # min_df 控维
    if use_min_df and len(cols) > 0:
        appear_counts = (wide[cols] > 0).sum(axis=0)
        keep = appear_counts[appear_counts >= min_df].index.tolist()
        if len(keep) == 0:
            keep = appear_counts.sort_values(ascending=False).head(min(50, len(appear_counts))).index.tolist()
        wide = pd.concat([wide[["trade_date", "stock_code"]], wide[keep]], axis=1)
        cols = keep
    # 加前缀
    rename_map = {c: f"{prefix}{c}" for c in cols}
    wide = wide.rename(columns=rename_map)
    return wide

def load_feature_label(conn, use_min_df=True):
    # 标签
    log("开始加载标签数据 t_stock_label_1 ...")
    y_df = pd.read_sql_query("SELECT trade_date, stock_code, label FROM t_stock_label_1", conn)
    y_df["trade_date"] = y_df["trade_date"].astype(int)
    y_df["stock_code"] = y_df["stock_code"].astype(str)
    log(f"标签数据加载完成，共 {len(y_df)} 条，交易日数={y_df['trade_date'].nunique()}")

    # 原始信号
    log("开始加载信号数据 t_stock_signal ...")
    sig_df = pd.read_sql_query(
        "SELECT trade_date, stock_code, signal_name, signal_value FROM t_stock_signal", conn
    )
    sig_df["trade_date"] = sig_df["trade_date"].astype(int)
    sig_df["stock_code"] = sig_df["stock_code"].astype(str)
    log(f"信号数据加载完成，共 {len(sig_df)} 条，信号种类={sig_df['signal_name'].nunique()}")
    if not sig_df.empty:
        top_sig_train = (sig_df.groupby("signal_name")["signal_value"]
                               .size().sort_values(ascending=False).head(20))
        log("训练期最常见信号TOP-20：")
        print(top_sig_train)

    log("开始透视原始信号为宽表 ...")
    wide_s = pivot_signals(sig_df, "signal_name", "signal_value", prefix="s_", use_min_df=use_min_df)
    log(f"原始信号透视完成，保留列数={len([c for c in wide_s.columns if c not in ('trade_date','stock_code')])}")

    # === NEW: 两两组合视图
    if USE_PAIR:
        log("开始加载两两组合视图 t_stock_signal_2 ...")
        pair_df = pd.read_sql_query(
            "SELECT trade_date, stock_code, combo_name, combo_value FROM t_stock_signal_2", conn
        )
        pair_df["trade_date"] = pair_df["trade_date"].astype(int)
        pair_df["stock_code"] = pair_df["stock_code"].astype(str)
        log(f"两两组合记录数={len(pair_df)}，组合种类={pair_df['combo_name'].nunique()}")
        log("透视两两组合 ...")
        wide_p2 = pivot_signals(pair_df, "combo_name", "combo_value", prefix="p2_", use_min_df=use_min_df)
    else:
        wide_p2 = pd.DataFrame(columns=["trade_date", "stock_code"])

    # === NEW: 三三组合视图
    if USE_TRIPLE:
        log("开始加载三三组合视图 t_stock_signal_3 ...")
        triple_df = pd.read_sql_query(
            "SELECT trade_date, stock_code, combo_name, combo_value FROM t_stock_signal_3", conn
        )
        triple_df["trade_date"] = triple_df["trade_date"].astype(int)
        triple_df["stock_code"] = triple_df["stock_code"].astype(str)
        log(f"三三组合记录数={len(triple_df)}，组合种类={triple_df['combo_name'].nunique()}")
        log("透视三三组合 ...")
        wide_p3 = pivot_signals(triple_df, "combo_name", "combo_value", prefix="p3_", use_min_df=use_min_df)
    else:
        wide_p3 = pd.DataFrame(columns=["trade_date", "stock_code"])

    # === NEW: 逐步合并
    log("合并信号（原始 + 两两 + 三三） ...")
    wide = wide_s
    if not wide_p2.empty:
        wide = pd.merge(wide, wide_p2, on=["trade_date", "stock_code"], how="left")
    if not wide_p3.empty:
        wide = pd.merge(wide, wide_p3, on=["trade_date", "stock_code"], how="left")
    # 缺失补 0
    feat_cols_sig = [c for c in wide.columns if c not in ("trade_date", "stock_code")]
    for c in feat_cols_sig:
        wide[c] = wide[c].fillna(0.0)

    # === NEW: 并入 lag1 量价特征
    st_lag = load_lag_daily(conn)
    if st_lag is not None and not st_lag.empty:
        wide = pd.merge(wide, st_lag, on=["trade_date", "stock_code"], how="left")
        lag_cols = [col for col in wide.columns if col.startswith("lag1_")]
        for c in lag_cols:
            wide[c] = wide[c].fillna(0.0)
        log(f"lag1 量价特征并入完成，新增数值列={len(lag_cols)}")
    else:
        log("⚠️ 未并入 lag1 量价特征（数据为空）")

    # 合并标签
    log("开始合并标签 ...")
    df = pd.merge(wide, y_df, on=["trade_date", "stock_code"], how="inner")
    log(f"合并完成，总样本={len(df)}")

    # 覆盖率统计
    sig_cols = [c for c in df.columns if c not in ("trade_date","stock_code","label") and not c.startswith("lag1_")]
    num_cols = [c for c in df.columns if c.startswith("lag1_")]
    if sig_cols:
        nz_sig = (df[sig_cols].values != 0).sum(axis=1)
        log(f"[训练集] 信号特征非零数：P50={np.percentile(nz_sig,50)}, P90={np.percentile(nz_sig,90)}, max={nz_sig.max()}  全零比={(nz_sig==0).mean():.2%}")
    if num_cols:
        nz_num = (df[num_cols].values != 0).sum(axis=1)
        log(f"[训练集] 量价特征非零数：P50={np.percentile(nz_num,50)}, P90={np.percentile(nz_num,90)}, max={nz_num.max()}  全零比={(nz_num==0).mean():.2%}")
    feat_cols_all = [c for c in df.columns if c not in ("trade_date","stock_code","label")]
    nz_all = (df[feat_cols_all].values != 0).sum(axis=1)
    log(f"[训练集] 合并后特征非零数：P50={np.percentile(nz_all,50)}, P90={np.percentile(nz_all,90)}, max={nz_all.max()}  全零比={(nz_all==0).mean():.2%}")

    pos_rate = float(df["label"].mean()) if len(df) else 0.0
    log(f"标签正例占比={pos_rate:.4f}（全预测为1基线acc≈{pos_rate:.4f}）")

    feature_cols = feat_cols_all
    return df, feature_cols

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
    return clf, feature_cols

def predict_latest_day(conn, clf, feature_cols):
    log("开始获取最新交易日 ...")
    latest_dt = conn.execute("SELECT MAX(trade_date) FROM t_stock_signal").fetchone()[0]
    log(f"最新交易日={latest_dt}")

    # 原始信号（当日）
    sig_df = pd.read_sql_query(
        "SELECT trade_date, stock_code, signal_name, signal_value FROM t_stock_signal WHERE trade_date=?",
        conn, params=(latest_dt,)
    )
    log(f"当天原始信号数={len(sig_df)}")
    if not sig_df.empty:
        top_sig_pred = (sig_df.groupby("signal_name")["signal_value"].size()
                        .sort_values(ascending=False).head(20))
        log("当日最常见原始信号TOP-20：")
        print(top_sig_pred)
    sig_df["trade_date"] = sig_df.get("trade_date", pd.Series(dtype=int)).astype(int)
    sig_df["stock_code"] = sig_df.get("stock_code", pd.Series(dtype=str)).astype(str)

    wide = pivot_signals(sig_df, "signal_name", "signal_value", prefix="s_", use_min_df=False)

    # === NEW: 两两组合（当日）
    if USE_PAIR:
        pair_df = pd.read_sql_query(
            "SELECT trade_date, stock_code, combo_name, combo_value FROM t_stock_signal_2 WHERE trade_date=?",
            conn, params=(latest_dt,)
        )
        log(f"当天两两组合记录数={len(pair_df)}，组合种类={pair_df['combo_name'].nunique() if not pair_df.empty else 0}")
        if not pair_df.empty:
            pair_df["trade_date"] = pair_df["trade_date"].astype(int)
            pair_df["stock_code"] = pair_df["stock_code"].astype(str)
            wide_p2 = pivot_signals(pair_df, "combo_name", "combo_value", prefix="p2_", use_min_df=False)
            wide = pd.merge(wide, wide_p2, on=["trade_date", "stock_code"], how="left")
    # === NEW: 三三组合（当日）
    if USE_TRIPLE:
        triple_df = pd.read_sql_query(
            "SELECT trade_date, stock_code, combo_name, combo_value FROM t_stock_signal_3 WHERE trade_date=?",
            conn, params=(latest_dt,)
        )
        log(f"当天三三组合记录数={len(triple_df)}，组合种类={triple_df['combo_name'].nunique() if not triple_df.empty else 0}")
        if not triple_df.empty:
            triple_df["trade_date"] = triple_df["trade_date"].astype(int)
            triple_df["stock_code"] = triple_df["stock_code"].astype(str)
            wide_p3 = pivot_signals(triple_df, "combo_name", "combo_value", prefix="p3_", use_min_df=False)
            wide = pd.merge(wide, wide_p3, on=["trade_date", "stock_code"], how="left")

    # === NEW: 并入 lag1 量价特征（当日）
    st_lag = load_lag_daily(conn)
    if st_lag is not None and not st_lag.empty:
        wide = pd.merge(wide, st_lag, on=["trade_date","stock_code"], how="left")
        lag_cols = [col for col in wide.columns if col.startswith("lag1_")]
        for c in lag_cols:
            wide[c] = wide[c].fillna(0.0)
        log("预测日 lag1 量价特征并入完成")
    else:
        log("⚠️ 预测日未并入 lag1 量价特征（数据为空）")

    # 缺失的训练列补 0；多余列忽略
    for col in feature_cols:
        if col not in wide.columns:
            wide[col] = 0.0

    X_new = wide[feature_cols].values

    # 覆盖率统计
    nz_day = (X_new != 0).sum(axis=1)
    log(f"[预测集] 当日标的数={len(wide)}")
    if len(nz_day) > 0:
        log(f"  P50={np.percentile(nz_day,50)}, P90={np.percentile(nz_day,90)}, max={nz_day.max()}")
        log(f"  全零样本比例={(nz_day==0).mean():.2%}")

    log("开始预测最新交易日 ...")
    proba = clf.predict_proba(X_new)[:, 1]
    out = wide[["trade_date", "stock_code"]].copy()
    out["pred_up_prob"] = proba
    out = out.sort_values("pred_up_prob", ascending=False).reset_index(drop=True)
    print(out.head(TOPN_PREDICT))

if __name__ == "__main__":
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"数据库文件不存在：{DB_PATH}")
    with sqlite3.connect(DB_PATH) as conn:
        clf, feature_cols = train_and_eval(conn)
        predict_latest_day(conn, clf, feature_cols)
