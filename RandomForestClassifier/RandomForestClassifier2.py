# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score
# === NEW: 附加评估指标（可选）
from sklearn.metrics import roc_auc_score, average_precision_score  # === NEW
import joblib
import datetime

DB_PATH = r"../stock.db"   # ←← 修改为你的 SQLite 文件路径
MODEL_PATH = r"../train/rf_model_stock.pkl"
TOPN_PREDICT = 50
min_df = 10
random_state = 42

def log(msg: str):
    """统一日志输出，带时间戳"""
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

def load_feature_label(conn, use_min_df=True):
    log("开始加载标签数据 t_stock_label_1 ...")
    y_df = pd.read_sql_query("SELECT trade_date, stock_code, label FROM t_stock_label_1", conn)
    log(f"标签数据加载完成，共 {len(y_df)} 条，交易日数={y_df['trade_date'].nunique()}")

    log("开始加载信号数据 t_stock_signal ...")
    sig_df = pd.read_sql_query(
        "SELECT trade_date, stock_code, signal_name, signal_value FROM t_stock_signal", conn
    )
    log(f"信号数据加载完成，共 {len(sig_df)} 条，信号种类={sig_df['signal_name'].nunique()}")

    # === NEW: 训练期最常见信号TOP（排查命名是否一致）
    if not sig_df.empty:  # === NEW
        top_sig_train = (sig_df.groupby("signal_name")["signal_value"]
                               .size().sort_values(ascending=False).head(20))  # === NEW
        log("训练期最常见信号TOP-20：")  # === NEW
        print(top_sig_train)  # === NEW

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
    log(f"透视完成，特征列数={wide.shape[1]-2}")

    if use_min_df:
        appear_counts = (wide.drop(columns=["trade_date", "stock_code"]) > 0).sum(axis=0)
        keep_cols = appear_counts[appear_counts >= min_df].index.tolist()
        if len(keep_cols) == 0:
            keep_cols = appear_counts.sort_values(ascending=False).head(50).index.tolist()
        wide = pd.concat([wide[["trade_date", "stock_code"]], wide[keep_cols]], axis=1)
        log(f"低频信号过滤完成，保留特征数={len(keep_cols)}")

    log("开始合并标签 ...")
    df = pd.merge(wide, y_df, on=["trade_date", "stock_code"], how="inner")
    log(f"合并完成，总样本={len(df)}")

    # === NEW: 打印“非零特征覆盖率”（训练数据整体）
    feature_cols = [c for c in df.columns if c not in ("trade_date", "stock_code", "label")]
    if len(feature_cols) > 0:  # === NEW
        feat_only = df[feature_cols].values  # === NEW
        nz = (feat_only != 0).sum(axis=1)  # === NEW
        log("[训练集] 非零特征数分布：")  # === NEW
        log(f"  P50={np.percentile(nz,50)}, P90={np.percentile(nz,90)}, max={nz.max()}")  # === NEW
        log(f"  全零样本比例={(nz==0).mean():.2%}")  # === NEW
    else:  # === NEW
        log("⚠️ 训练特征列为空，请检查信号过滤或列名对齐。")  # === NEW

    # === NEW: 标签正例占比（作为“全预测为1”的基线准确率参考）
    pos_rate = float(df["label"].mean()) if len(df) else 0.0  # === NEW
    log(f"标签正例占比={pos_rate:.4f}（全预测为1的基线acc≈{pos_rate:.4f}）")  # === NEW

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
        n_estimators=400, max_depth=None, min_samples_split=4,
        n_jobs=-1, random_state=random_state, class_weight="balanced"
    )
    clf.fit(X_train, y_train)
    log("训练完成")

    log("开始评估模型 ...")
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    log(f"测试集准确率={acc:.4f}")
    print(classification_report(y_test, y_pred, digits=4))

    # === NEW: AUC / PR-AUC（不平衡数据更有参考价值）
    try:
        y_prob = clf.predict_proba(X_test)[:, 1]  # === NEW
        auc = roc_auc_score(y_test, y_prob)       # === NEW
        ap = average_precision_score(y_test, y_prob)  # === NEW
        log(f"ROC-AUC={auc:.4f}  PR-AUC={ap:.4f}")    # === NEW
    except Exception as e:
        log(f"计算AUC/PR-AUC失败：{e}")  # === NEW

    fi = pd.DataFrame({"feature": feature_cols, "importance": clf.feature_importances_})
    fi = fi.sort_values("importance", ascending=False)
    log("Top-20 特征重要性：")
    print(fi.head(20))

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

    # === NEW: 当日最常见信号（排查训练-预测是否一致）
    if not sig_df.empty:  # === NEW
        top_sig_pred = (sig_df.groupby("signal_name")["signal_value"]
                               .size().sort_values(ascending=False).head(20))  # === NEW
        log("当日最常见信号TOP-20：")  # === NEW
        print(top_sig_pred)  # === NEW

    wide = sig_df.pivot_table(
        index=["trade_date", "stock_code"],
        columns="signal_name",
        values="signal_value",
        aggfunc="max",
        fill_value=0.0,
    ).reset_index()

    # === NEW: （可选）过滤指数/板块代码，避免干扰；按需开启
    # idx_mask = ~wide["stock_code"].astype(str).str.startswith(("88", "89"))  # === NEW（可选）
    # wide = wide.loc[idx_mask].copy()  # === NEW（可选）
    # log(f"过滤指数后，当日标的数={len(wide)}")  # === NEW（可选）

    for col in feature_cols:
        if col not in wide.columns:
            wide[col] = 0.0
    X_new = wide[feature_cols].values

    # === NEW: 打印“非零特征覆盖率”（预测当日）
    nz_day = (X_new != 0).sum(axis=1)  # === NEW
    log(f"[预测集] 当日标的数={len(wide)}")  # === NEW
    if len(nz_day) > 0:  # === NEW
        log(f"  P50={np.percentile(nz_day,50)}, P90={np.percentile(nz_day,90)}, max={nz_day.max()}")  # === NEW
        log(f"  全零样本比例={(nz_day==0).mean():.2%}")  # === NEW
        if (nz_day == 0).all():  # === NEW
            log("❌ 当日所有标的特征全为0：很可能当天信号与训练使用的列不相交，或信号触发极少。")  # === NEW
            # return  # ← 如需避免输出“全一样概率”，可以直接返回  # === NEW

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
