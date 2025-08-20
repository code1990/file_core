# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score
from sklearn.metrics import roc_auc_score, average_precision_score
import joblib
import datetime

DB_PATH = r"../stock.db"  # ←← 修改为你的 SQLite 文件路径
MODEL_PATH = r"../train/rf_model_stock.pkl"
TOPN_PREDICT = 50
min_df = 10
random_state = 42

# === 修正 LAG_FEATURES，保持与 t_stock_daily 一致 ===
LAG_FEATURES = ["percent", "vol", "amount", "close", "high", "low"]


def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")


# === NEW: 读取并生成 t_stock_stat 的 lag1 特征
# === NEW: 读取并生成 t_stock_daily 的 lag1 特征
def load_lag_daily(conn):
    log("开始加载量价数据 t_stock_daily（用于 lag1 特征） ...")
    df = pd.read_sql_query(
        """
        SELECT stock_code, trade_date,
               percent, vol, amount, close, high, low
        FROM t_stock_daily
        """, conn
    )
    if df.empty:
        log("⚠️ t_stock_daily 为空，无法生成量价滞后特征")
        return None

    # 保证类型一致（有些库会返回 object）
    df["trade_date"] = df["trade_date"].astype(int)
    df["stock_code"] = df["stock_code"].astype(str)

    df = df.sort_values(["stock_code", "trade_date"])
    df[LAG_FEATURES] = df.groupby("stock_code")[LAG_FEATURES].shift(1)

    # （可选）更合理的缺失处理：前向填充再补0
    # df[LAG_FEATURES] = (
    #     df.groupby("stock_code")[LAG_FEATURES]
    #       .apply(lambda g: g.fillna(method="ffill"))
    #       .reset_index(level=0, drop=True)
    # )
    # df[LAG_FEATURES] = df[LAG_FEATURES].fillna(0.0)

    df = df.dropna(subset=LAG_FEATURES, how="all").reset_index(drop=True)
    df = df.rename(columns={c: f"lag1_{c}" for c in LAG_FEATURES})
    return df


def load_feature_label(conn, use_min_df=True):
    log("开始加载标签数据 t_stock_label_1 ...")
    y_df = pd.read_sql_query("SELECT trade_date, stock_code, label FROM t_stock_label_1", conn)
    log(f"标签数据加载完成，共 {len(y_df)} 条，交易日数={y_df['trade_date'].nunique()}")

    log("开始加载信号数据 t_stock_signal ...")
    sig_df = pd.read_sql_query(
        "SELECT trade_date, stock_code, signal_name, signal_value FROM t_stock_signal", conn
    )
    log(f"信号数据加载完成，共 {len(sig_df)} 条，信号种类={sig_df['signal_name'].nunique()}")

    if not sig_df.empty:
        top_sig_train = (sig_df.groupby("signal_name")["signal_value"]
                         .size().sort_values(ascending=False).head(20))
        log("训练期最常见信号TOP-20：")
        print(top_sig_train)

    log("开始透视信号表构造特征矩阵 ...")
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
    log(f"透视完成，信号特征列数={wide.shape[1] - 2}")

    if use_min_df:
        appear_counts = (wide.drop(columns=["trade_date", "stock_code"]) > 0).sum(axis=0)
        keep_cols = appear_counts[appear_counts >= min_df].index.tolist()
        if len(keep_cols) == 0:
            keep_cols = appear_counts.sort_values(ascending=False).head(50).index.tolist()
        wide = pd.concat([wide[["trade_date", "stock_code"]], wide[keep_cols]], axis=1)
        log(f"低频信号过滤完成，保留信号特征数={len(keep_cols)}")

    # === NEW: 合并 lag1 量价特征
    st_lag = load_lag_daily(conn)  # === NEW
    if st_lag is not None and not st_lag.empty:  # === NEW
        wide = pd.merge(wide, st_lag, on=["trade_date", "stock_code"], how="left")  # === NEW
        # 缺失补 0（对二元/非负量纲合理；如不合理可改为中位数/前向填充）
        for c in [col for col in wide.columns if col.startswith("lag1_")]:
            wide[c] = wide[c].fillna(0.0)  # === NEW
        log(f"lag1 量价特征并入完成，新增数值列={len([c for c in wide.columns if c.startswith('lag1_')])}")  # === NEW
    else:
        log("⚠️ 未并入 lag1 量价特征（数据为空）")  # === NEW

    log("开始合并标签 ...")
    df = pd.merge(wide, y_df, on=["trade_date", "stock_code"], how="inner")
    log(f"合并完成，总样本={len(df)}")

    # === NEW: 覆盖率统计（信号 → 数值 → 总体）
    sig_cols = [c for c in df.columns if
                c not in ("trade_date", "stock_code", "label") and not c.startswith("lag1_")]  # === NEW
    num_cols = [c for c in df.columns if c.startswith("lag1_")]  # === NEW
    if sig_cols:
        nz_sig = (df[sig_cols].values != 0).sum(axis=1)
        log(f"[训练集] 信号特征非零数：P50={np.percentile(nz_sig, 50)}, P90={np.percentile(nz_sig, 90)}, max={nz_sig.max()}  全零比={(nz_sig == 0).mean():.2%}")
    if num_cols:
        nz_num = (df[num_cols].values != 0).sum(axis=1)
        log(f"[训练集] 量价特征非零数：P50={np.percentile(nz_num, 50)}, P90={np.percentile(nz_num, 90)}, max={nz_num.max()}  全零比={(nz_num == 0).mean():.2%}")
    feat_cols_all = [c for c in df.columns if c not in ("trade_date", "stock_code", "label")]
    nz_all = (df[feat_cols_all].values != 0).sum(axis=1)
    log(f"[训练集] 合并后特征非零数：P50={np.percentile(nz_all, 50)}, P90={np.percentile(nz_all, 90)}, max={nz_all.max()}  全零比={(nz_all == 0).mean():.2%}")

    pos_rate = float(df["label"].mean()) if len(df) else 0.0
    log(f"标签正例占比={pos_rate:.4f}（全预测为1基线acc≈{pos_rate:.4f}）")

    feature_cols = feat_cols_all  # === NEW: 信号 + lag1 数值共同作为特征
    return df, feature_cols


def temporal_split(df, test_ratio=0.3):
    dates = sorted(df["trade_date"].unique())
    split_idx = int(len(dates) * (1 - test_ratio))
    split_date = dates[split_idx]
    train = df[df["trade_date"] < split_date]
    test = df[df["trade_date"] >= split_date]
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
        n_jobs=-1, random_state=random_state, class_weight="balanced_subsample"  # === NEW: 小改，鲁棒些
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

    fi = pd.DataFrame({"feature": feature_cols, "importance": clf.feature_importances_})
    fi = fi.sort_values("importance", ascending=False)
    log("Top-30 特征重要性：")
    print(fi.head(30))

    joblib.dump({"model": clf, "feature_cols": feature_cols}, MODEL_PATH)
    log(f"模型已保存到 {MODEL_PATH}")
    return clf, feature_cols


def predict_latest_day(conn, clf, feature_cols):
    log("开始获取最新交易日 ...")
    latest_dt = conn.execute("SELECT MAX(trade_date) FROM t_stock_signal").fetchone()[0]
    log(f"最新交易日={latest_dt}")

    sig_df = pd.read_sql_query(
        "SELECT trade_date, stock_code, signal_name, signal_value FROM t_stock_signal WHERE trade_date=?",
        conn, params=(latest_dt,)
    )
    log(f"当天信号数={len(sig_df)}")
    if not sig_df.empty:
        top_sig_pred = (sig_df.groupby("signal_name")["signal_value"].size()
                        .sort_values(ascending=False).head(20))
        log("当日最常见信号TOP-20：")
        print(top_sig_pred)

    # 统一键类型（防 join 丢数据）
    if not sig_df.empty:
        sig_df["trade_date"] = sig_df["trade_date"].astype(int)
        sig_df["stock_code"] = sig_df["stock_code"].astype(str)

    wide = sig_df.pivot_table(
        index=["trade_date", "stock_code"],
        columns="signal_name",
        values="signal_value",
        aggfunc="max",
        fill_value=0.0,
    ).reset_index()

    # === 修复处：把 col 写对 ===
    st_lag = load_lag_daily(conn)
    if st_lag is not None and not st_lag.empty:
        st_lag["trade_date"] = st_lag["trade_date"].astype(int)
        st_lag["stock_code"] = st_lag["stock_code"].astype(str)

        wide = pd.merge(wide, st_lag, on=["trade_date","stock_code"], how="left")

        # 这里原来写成了 for c in [col for col in wide.columns if c.startswith("lag1_")]:
        lag_cols = [col for col in wide.columns if col.startswith("lag1_")]
        for c in lag_cols:
            wide[c] = wide[c].fillna(0.0)
        log("预测日 lag1 量价特征并入完成")
    else:
        log("⚠️ 预测日未并入 lag1 量价特征（数据为空）")

    # 缺失的训练列补 0；多余列会被忽略
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
