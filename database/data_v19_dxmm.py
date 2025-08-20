# -*- coding: utf-8 -*-
"""
从 SQLite 读取 v1..v19（二元特征）和未来收益，构造标签 y（任一 >1.5% 即正例），
进行时序切分训练并评估多种 ML 模型（L1-Logistic / RandomForest / GradientBoosting），
输出特征重要性、树规则（人类可读）、并把预测概率写回 SQLite。
"""
import os
import sqlite3
import numpy as np
import pandas as pd

from sklearn.model_selection import TimeSeriesSplit, train_test_split, GridSearchCV
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.metrics import (
    classification_report, confusion_matrix, roc_auc_score,
    average_precision_score, precision_recall_curve
)
from sklearn.tree import _tree, DecisionTreeClassifier
import joblib

# ========= 可配置 =========
DB_PATH = r"../stock.db"  # SQLite 数据库文件
TABLE = "t_signal"                    # 数据表
DATE_COL = "trade_date"
ID_COLS = ["code", "trade_date"]      # 方便回写预测结果
V_COLS = [f"v{i}" for i in range(1, 20)]
RET_COLS = ["v_1_percent", "v_2_percent", "v_3_percent"]

# 时序切分：指定训练集结束日期（左闭右闭）
TRAIN_END = 20231231                  # 根据你的数据范围调整
VAL_RATIO = 0.2                       # 如果不指定 TRAIN_END，可用随机划分验证集

# 结果输出
MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

# ========= 1) 读取 & 生成标签 =========
def load_from_sqlite(db_path: str, table: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(f"SELECT {','.join(ID_COLS + V_COLS + RET_COLS)} FROM {table}", conn)
    finally:
        conn.close()
    # 标签：任一 > 1.5
    df["y"] = (df["v_1_percent"] > 1.5) | (df["v_2_percent"] > 1.5) | (df["v_3_percent"] > 1.5)
    # 排序（时序）
    df = df.sort_values(DATE_COL).reset_index(drop=True)
    return df

# ========= 2) 时序切分 =========
def time_split(df: pd.DataFrame, train_end: int = None, val_ratio: float = 0.2):
    if train_end is not None:
        train = df[df[DATE_COL] <= train_end]
        test  = df[df[DATE_COL] >  train_end]
    else:
        train, test = train_test_split(df, test_size=val_ratio, shuffle=False)  # 保持时序
    return train, test

# ========= 3) 模型们 =========
def build_l1_logistic():
    # 二元特征也建议标准化（让 C 的尺度更合理）
    pipe = Pipeline([
        ("scaler", StandardScaler(with_mean=False)),  # v 是 0/1，with_mean=False 保持稀疏友好
        ("clf", LogisticRegression(
            penalty="l1", solver="liblinear", max_iter=2000, class_weight="balanced"
        ))
    ])
    params = {
        "clf__C": [0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
    }
    return pipe, params

def build_random_forest():
    clf = RandomForestClassifier(
        n_estimators=500,
        max_depth=None,
        min_samples_leaf=5,
        n_jobs=-1,
        class_weight="balanced_subsample",
        random_state=42
    )
    params = {
        "n_estimators": [300, 500],
        "min_samples_leaf": [3, 5, 10],
        "max_depth": [None, 6, 10]
    }
    return clf, params

def build_gbdt():
    clf = GradientBoostingClassifier(
        learning_rate=0.05,
        n_estimators=400,
        max_depth=3,
        subsample=0.9
    )
    params = {
        "learning_rate": [0.03, 0.05, 0.08],
        "n_estimators": [300, 400, 600],
        "max_depth": [2, 3]
    }
    return clf, params

# ========= 4) 评估 =========
def evaluate(model, X_tr, y_tr, X_te, y_te, name: str):
    model.fit(X_tr, y_tr)
    proba_te = model.predict_proba(X_te)[:, 1] if hasattr(model, "predict_proba") else model.decision_function(X_te)
    pred_te = (proba_te >= 0.5).astype(int)

    print(f"\n=== {name} / Test Metrics ===")
    print(classification_report(y_te, pred_te, digits=4))
    cm = confusion_matrix(y_te, pred_te)
    print("Confusion Matrix:\n", cm)

    try:
        auc = roc_auc_score(y_te, proba_te)
        ap  = average_precision_score(y_te, proba_te)
        print(f"ROC-AUC: {auc:.4f}  |  PR-AUC: {ap:.4f}")
    except Exception:
        pass

    return model, proba_te, pred_te

# ========= 5) 树模型的可解释规则提取 =========
def extract_rules_from_tree(tree_clf: DecisionTreeClassifier, feature_names, top_k=10):
    """把决策树的路径导出为人类可读规则（按叶子样本量与纯度排序）"""
    tree_ = tree_clf.tree_
    feature_name = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined!"
        for i in tree_.feature
    ]

    paths = []

    def recurse(node, rule):
        if tree_.feature[node] != _tree.TREE_UNDEFINED:
            name = feature_name[node]
            thresh = tree_.threshold[node]
            # 二元特征：阈值 ~ 0.5
            # 左： <=0.5  右： >0.5
            recurse(tree_.children_left[node], rule + [f"{name}=0"])
            recurse(tree_.children_right[node], rule + [f"{name}=1"])
        else:
            # 叶子
            value = tree_.value[node][0]   # [neg, pos]
            total = int(value.sum())
            pos = int(value[1])
            prec = pos / total if total > 0 else 0.0
            paths.append((prec, total, " & ".join(rule)))

    recurse(0, [])
    # 先按 precision，再按支持度排序
    paths.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return paths[:top_k]

# ========= 6) 写回 SQLite =========
def write_predictions_to_sqlite(db_path, table_out, df_keys, proba, pred):
    out = df_keys.copy()
    out["proba"] = proba
    out["pred"] = pred
    conn = sqlite3.connect(db_path)
    try:
        out.to_sql(table_out, conn, if_exists="replace", index=False)
    finally:
        conn.close()
    print(f"[OK] 预测结果写入 SQLite 表：{table_out}（{len(out)} 行）")

# ========= main =========
def main():
    df = load_from_sqlite(DB_PATH, TABLE)
    X = df[V_COLS].astype(float).values
    y = df["y"].astype(int).values

    # 时序切分
    train_df, test_df = time_split(df, TRAIN_END, VAL_RATIO)
    X_tr = train_df[V_COLS].astype(float).values
    y_tr = train_df["y"].astype(int).values
    X_te = test_df[V_COLS].astype(float).values
    y_te = test_df["y"].astype(int).values

    print(f"Train size: {len(train_df)}, Test size: {len(test_df)}")
    print("Base rate (train):", train_df["y"].mean(), " | (test):", test_df["y"].mean())

    # ===== 模型1：L1 Logistic（做特征选择 + 概率）=====
    logi_pipe, logi_params = build_l1_logistic()
    gs_logi = GridSearchCV(
        logi_pipe, logi_params,
        scoring="average_precision",
        cv=TimeSeriesSplit(n_splits=5),
        n_jobs=-1, verbose=0
    )
    model_logi, proba_logi, pred_logi = evaluate(gs_logi, X_tr, y_tr, X_te, y_te, "L1-Logistic")
    print("Best C:", model_logi.best_params_)
    # 提取稀疏系数
    best_logi = model_logi.best_estimator_.named_steps["clf"]
    coef = best_logi.coef_.ravel()
    sel = np.where(np.abs(coef) > 1e-8)[0]
    print("Selected features by L1:", [V_COLS[i] for i in sel])

    joblib.dump(model_logi, os.path.join(MODEL_DIR, "logi_l1.joblib"))

    # ===== 模型2：RandomForest（非线性、鲁棒，提供重要性）=====
    rf, rf_params = build_random_forest()
    gs_rf = GridSearchCV(
        rf, rf_params,
        scoring="average_precision",
        cv=TimeSeriesSplit(n_splits=5),
        n_jobs=-1, verbose=0
    )
    model_rf, proba_rf, pred_rf = evaluate(gs_rf, X_tr, y_tr, X_te, y_te, "RandomForest")
    print("Best RF params:", model_rf.best_params_)
    best_rf = model_rf.best_estimator_
    importances = best_rf.feature_importances_
    imp_df = pd.DataFrame({"feature": V_COLS, "importance": importances}).sort_values("importance", ascending=False)
    print("\nTop Feature Importances (RF):\n", imp_df.head(10).to_string(index=False))
    joblib.dump(model_rf, os.path.join(MODEL_DIR, "rf.joblib"))

    # ===== 模型3：GradientBoosting（精细拟合）=====
    gbdt, gbdt_params = build_gbdt()
    gs_gbdt = GridSearchCV(
        gbdt, gbdt_params,
        scoring="average_precision",
        cv=TimeSeriesSplit(n_splits=5),
        n_jobs=-1, verbose=0
    )
    model_gbdt, proba_gbdt, pred_gbdt = evaluate(gs_gbdt, X_tr, y_tr, X_te, y_te, "GBDT")
    print("Best GBDT params:", model_gbdt.best_params_)
    joblib.dump(model_gbdt, os.path.join(MODEL_DIR, "gbdt.joblib"))

    # ===== 额外：小深度决策树，用于“可解释规则提取”=====
    dt = DecisionTreeClassifier(
        max_depth=3, min_samples_leaf=30, class_weight="balanced", random_state=42
    )
    dt.fit(X_tr, y_tr)
    rules = extract_rules_from_tree(dt, V_COLS, top_k=15)
    print("\n=== Top Rules from shallow DecisionTree (depth<=3) ===")
    for i, (prec, total, rule) in enumerate(rules, 1):
        print(f"{i:02d}. precision={prec:.3f} support={total}  |  {rule}")

    joblib.dump(dt, os.path.join(MODEL_DIR, "tree_depth3.joblib"))

    # ===== 写回 SQLite：各模型在测试集上的预测 =====
    keys_test = test_df[ID_COLS].copy()
    write_predictions_to_sqlite(DB_PATH, "pred_logi_test", keys_test, proba_logi, pred_logi)
    write_predictions_to_sqlite(DB_PATH, "pred_rf_test",   keys_test, proba_rf,   pred_rf)
    write_predictions_to_sqlite(DB_PATH, "pred_gbdt_test", keys_test, proba_gbdt, pred_gbdt)

    # ===== 保存一个融合分数（简单平均）=====
    blend_proba = (proba_logi + proba_rf + proba_gbdt) / 3.0
    blend_pred = (blend_proba >= 0.5).astype(int)
    write_predictions_to_sqlite(DB_PATH, "pred_blend_test", keys_test, blend_proba, blend_pred)

    print("\n[Done] 模型与预测结果已生成。你可以在 SQLite 中用阈值/排序做选股或回测。")

if __name__ == "__main__":
    main()
