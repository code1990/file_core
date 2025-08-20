# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score, roc_auc_score, average_precision_score
import joblib
import datetime
import pickle

DB_PATH = r"../stock.db"   # ← 修改为你的 SQLite 文件路径
MODEL_PATH = r"../train/rf_model_stock.pkl"
TOPN_PREDICT = 50
min_df = 10
random_state = 42

USE_PAIR = True      # 两两组合
USE_TRIPLE = False   # 三三组合关闭

def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def load_labels(conn):
    log("开始加载标签数据 t_stock_label_1 ...")
    label_df = pd.read_sql("SELECT stock_code, trade_date, label FROM t_stock_label_1", conn)
    log(f"标签数据加载完成，共 {len(label_df)} 条")
    return label_df

def load_signals(conn, table_name):
    log(f"开始加载信号数据 {table_name} ...")
    sig_df = pd.read_sql(f"""
        SELECT stock_code, trade_date, combo_name, combo_value 
        FROM {table_name}
    """, conn)
    log(f"信号数据加载完成，共 {len(sig_df)} 条，组合种类={sig_df['combo_name'].nunique()}")
    # 为了统一，重命名一下列
    sig_df = sig_df.rename(columns={"combo_value": "signal_value"})
    return sig_df

def pivot_signals(sig_df, prefix="s3_"):
    log("透视组合信号为宽表 ...")
    wide_df = sig_df.pivot_table(index=["trade_date", "stock_code"],
                                 columns="combo_name",
                                 values="signal_value",
                                 fill_value=0).reset_index()
    wide_df.columns = ["trade_date", "stock_code"] + [f"{prefix}{c}" for c in wide_df.columns[2:]]
    log(f"透视完成，保留列数={len(wide_df.columns)-2}")
    return wide_df

def merge_data(label_df, sig_df):
    df = pd.merge(label_df, sig_df, on=["trade_date", "stock_code"], how="inner")
    log(f"合并完成，总样本={len(df)}")
    return df

def train_model(df):
    X = df.drop(columns=["label", "trade_date", "stock_code"])
    y = df["label"]

    # 时间切分
    split_point = int(len(df) * 0.7)
    X_train, X_test = X.iloc[:split_point], X.iloc[split_point:]
    y_train, y_test = y.iloc[:split_point], y.iloc[split_point:]

    log("开始训练随机森林模型 ...")
    clf = RandomForestClassifier(n_estimators=100, max_depth=6, n_jobs=-1, random_state=42)
    clf.fit(X_train, y_train)
    log("训练完成")

    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    auc = roc_auc_score(y_test, clf.predict_proba(X_test)[:, 1])
    pr_auc = average_precision_score(y_test, clf.predict_proba(X_test)[:, 1])
    log(f"测试集准确率={acc:.4f} AUC={auc:.4f} PR-AUC={pr_auc:.4f}")

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(clf, f)
    log(f"模型已保存到 {MODEL_PATH}")

    return clf, X_test, y_test

def evaluate_combos(clf, X_test, y_test, conn, combo_type="p3"):
    cur = conn.cursor()
    saved_count = 0

    for col in X_test.columns:
        if not (col.startswith("s3_") or col.startswith("p3_")):
            continue

        # 自动去掉前缀
        combo = col.split("_", 1)[1]
        active_idx = X_test.index[X_test[col] > 0]

        if len(active_idx) < 100:
            continue

        try:
            preds = clf.predict(X_test.loc[active_idx])
            probs = clf.predict_proba(X_test.loc[active_idx])[:, 1]

            acc = accuracy_score(y_test.loc[active_idx], preds)
            try:
                auc = roc_auc_score(y_test.loc[active_idx], probs)
            except ValueError:
                auc = 0.5
            try:
                pr_auc = average_precision_score(y_test.loc[active_idx], probs)
            except ValueError:
                pr_auc = 0.0

            pos_rate = float(np.mean(y_test.loc[active_idx]))

            cur.execute("""
                INSERT INTO t_combo_eval (combo_type, combo_name, n_samples, accuracy, auc, pr_auc, pos_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                combo_type,
                combo,
                int(len(active_idx)),
                float(acc),
                float(auc),
                float(pr_auc),
                float(pos_rate)
            ))

            saved_count += 1

        except Exception as e:
            print(f"[WARN] 评估组合 {combo} 失败: {e}")

    conn.commit()
    print(f"[INFO] 评估完成，共保存 {saved_count} 条记录")
    df_eval = pd.read_sql("""
        SELECT combo_name, n_samples, accuracy, auc, pr_auc, pos_rate
        FROM t_combo_eval
        WHERE combo_type='p3'
        ORDER BY accuracy DESC
        LIMIT 20
    """, conn)

    print("\n=== Top20 p3 组合 ===")
    print(df_eval.to_string(index=False))

def main():
    conn = sqlite3.connect(DB_PATH)

    # 1) 筛选符合条件的 p2 组合
    log("筛选符合条件的 22 组合 ...")
    combo22_df = pd.read_sql("""
        SELECT combo_name 
        FROM t_combo_eval
        WHERE combo_type='p2' AND accuracy >= 0.65 AND n_samples >= 1000
    """, conn)
    valid_combos = set(combo22_df["combo_name"].tolist())
    log(f"有效 22 组合数={len(valid_combos)}")

    # 2) 加载 p3 信号
    sig3_df = load_signals(conn, "t_stock_signal_3")

    # 过滤：只保留包含有效 p2 组合的三三组合
    sig3_df = sig3_df[sig3_df["combo_name"].apply(lambda x: any(c in x for c in valid_combos))]
    log(f"过滤后的三三组合记录数={len(sig3_df)}")

    # 3) 转宽表
    sig3_wide = pivot_signals(sig3_df, prefix="s3_")

    # 4) 合并标签
    label_df = load_labels(conn)
    df = merge_data(label_df, sig3_wide)

    # 5) 训练模型
    clf, X_test, y_test = train_model(df)

    # 6) 评估 p3 组合
    evaluate_combos(clf, X_test, y_test, conn)

    conn.close()

if __name__ == "__main__":
    main()