# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score, roc_auc_score, \
    average_precision_score  # === CHANGED: 合并导入
import joblib
import datetime
# === NEW: 阈值扫描（给你一个参考阈值，不用也行，Top-K就够）
from sklearn.metrics import precision_recall_curve
# === NEW:
import json, hashlib

DB_PATH = r"../stock.db"  # ←← 修改为你的 SQLite 文件路径
MODEL_PATH = r"../train/rf_model_stock.pkl"
TOPN_PREDICT = 50
min_df = 10
random_state = 42

# === NEW: 使用 t_stock_feat 对应的基础列（不再用 t_stock_daily 那些绝对量价）
FEAT_BASE_COLS = ["turnover", "amplitude", "pct_chg"]  # === NEW


# === NEW: 序列化成稳定 JSON
def dumps_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# === NEW: 简单数据指纹（可选）
def quick_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# === NEW: 保存本次训练的模型元数据到 t_model_meta，返回 model_id
def save_model_meta(conn,
                    model_version: str,
                    model_type: str,
                    train_start: int, train_end: int,
                    valid_start: int, valid_end: int,
                    label_rule: str,
                    features: dict,
                    params: dict,
                    metrics: dict,
                    artifact_path: str,
                    tag: str = None,
                    note: str = None,
                    data_hash_text: str = None) -> int:
    features_json = dumps_json(features)
    params_json = dumps_json(params)
    metrics_json = dumps_json(metrics) if metrics else None
    data_hash = quick_hash(data_hash_text) if data_hash_text else None

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO t_model_meta
        (model_version, model_type,
         train_start, train_end, valid_start, valid_end,
         label_rule, features_json, params_json, metrics_json, artifact_path,
         data_hash, tag, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (model_version, model_type,
          train_start, train_end, valid_start, valid_end,
          label_rule, features_json, params_json, metrics_json, artifact_path,
          data_hash, tag, note))
    conn.commit()
    return cur.lastrowid


# === NEW: 工具，把 p2_/p3_ 非零列转成可读组合名
def _extract_combo_hits(row: pd.Series) -> tuple[str, str]:
    p2_hits, p3_hits = [], []
    for col, val in row.items():
        if not val:
            continue
        if isinstance(col, str) and col.startswith("p2_") and float(val) != 0.0:
            # p2_趋势为王钱袋&短买  ->  趋势为王钱袋&短买
            p2_hits.append(col[3:])
        elif isinstance(col, str) and col.startswith("p3_") and float(val) != 0.0:
            p3_hits.append(col[3:])
    return ",".join(p2_hits) if p2_hits else None, ",".join(p3_hits) if p3_hits else None


def scan_thresholds(y_true, y_prob, target_precision=None, target_recall=None):
    ps, rs, ths = precision_recall_curve(y_true, y_prob)
    ths = np.append(ths, 1.0)  # 对齐
    f1s = 2 * ps * rs / (ps + rs + 1e-12)
    out = {}
    i = np.nanargmax(f1s)
    out["best_f1"] = {"thr": float(ths[i]), "P": float(ps[i]), "R": float(rs[i]), "F1": float(f1s[i])}
    if target_precision is not None:
        idx = np.where(ps >= target_precision)[0]
        if len(idx):
            j = idx[np.argmax(rs[idx])]
            out["at_P"] = {"thr": float(ths[j]), "P": float(ps[j]), "R": float(rs[j]), "F1": float(f1s[j])}
    if target_recall is not None:
        idx = np.where(rs >= target_recall)[0]
        if len(idx):
            j = idx[np.argmax(ps[idx])]
            out["at_R"] = {"thr": float(ths[j]), "P": float(ps[j]), "R": float(rs[j]), "F1": float(f1s[j])}
    return out


# === NEW: “按交易日”的 Precision@K（每天只取前K只）
# === REPLACE: 更稳的按日 Precision@K 计算（返回 float）
def daily_precision_at_k(test_df, k=20):
    # test_df 需要含: trade_date, y_true, y_prob
    topk = (test_df
            .sort_values(['trade_date', 'y_prob'], ascending=[True, False])
            .groupby('trade_date')
            .head(k))
    # 每天的命中率
    by_day = topk.groupby('trade_date')['y_true'].mean()
    # 所有测试日的平均命中率
    return float(by_day.mean())

# === NEW: 通用透视函数，把长表(信号)变宽表，并可加前缀
def pivot_signals(df_long: pd.DataFrame,
                  name_col: str = "signal_name",
                  value_col: str = "signal_value",
                  prefix: str = "s_",
                  use_min_df: bool = False,
                  min_df: int = 10) -> pd.DataFrame:
    """
    输入形如: [trade_date, stock_code, signal_name, signal_value]
    输出形如: [trade_date, stock_code, s_信号A, s_信号B, ...]
    """
    if df_long is None or df_long.empty:
        return pd.DataFrame(columns=["trade_date", "stock_code"])

    wide = (df_long.pivot_table(
                index=["trade_date", "stock_code"],
                columns=name_col,
                values=value_col,
                aggfunc="max",
                fill_value=0.0)
            .reset_index())

    cols = [c for c in wide.columns if c not in ("trade_date", "stock_code")]

    if use_min_df and cols:
        appear_counts = (wide[cols] > 0).sum(axis=0)
        keep = appear_counts[appear_counts >= min_df].index.tolist()
        if not keep:
            keep = appear_counts.sort_values(ascending=False).head(min(50, len(appear_counts))).index.tolist()
        wide = pd.concat([wide[["trade_date", "stock_code"]], wide[keep]], axis=1)
        cols = keep

    if prefix:
        wide = wide.rename(columns={c: f"{prefix}{c}" for c in cols})

    return wide

def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")


# === NEW: 基于 t_stock_feat 生成 lag1_* 特征（替代原 load_lag_daily）
def load_lag_feat(conn):
    """
    从 t_stock_feat 读取 (stock_code, trade_date, turnover, amplitude, pct_chg)
    生成上一交易日可用的 lag1_turnover / lag1_amplitude / lag1_pct_chg
    """
    log("开始加载 t_stock_feat（用于 lag1 特征） ...")  # === NEW
    df = pd.read_sql_query(
        """
        SELECT stock_code, trade_date, turnover, amplitude, pct_chg
        FROM t_stock_feat
        """, conn
    )
    if df.empty:
        log("⚠️ t_stock_feat 为空，无法生成 lag1 特征")  # === NEW
        return None

    # 统一类型
    df["trade_date"] = df["trade_date"].astype(int)
    df["stock_code"] = df["stock_code"].astype(str)

    # 排序 & 逐股 shift(1)
    df = df.sort_values(["stock_code", "trade_date"])
    df[FEAT_BASE_COLS] = df.groupby("stock_code")[FEAT_BASE_COLS].shift(1)

    # 去掉全空 lag 行
    df = df.dropna(subset=FEAT_BASE_COLS, how="all").reset_index(drop=True)

    # 重命名为 lag1_*
    df = df.rename(columns={c: f"lag1_{c}" for c in FEAT_BASE_COLS})
    return df  # 含 stock_code, trade_date, lag1_turnover, lag1_amplitude, lag1_pct_chg


def load_feature_label(conn, use_min_df=True):
    log("开始加载标签数据 t_stock_label_1 ...")
    y_df = pd.read_sql_query("SELECT trade_date, stock_code, label FROM t_stock_label_1", conn)
    y_df["trade_date"] = y_df["trade_date"].astype(int)
    y_df["stock_code"] = y_df["stock_code"].astype(str)
    log(f"标签数据加载完成，共 {len(y_df)} 条，交易日数={y_df['trade_date'].nunique()}")

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

    # === CHANGED: 合并 lag1 特征，来源改为 t_stock_feat
    st_lag = load_lag_feat(conn)  # === NEW
    if st_lag is not None and not st_lag.empty:
        wide = pd.merge(wide, st_lag, on=["trade_date", "stock_code"], how="left")
        lag_cols = [col for col in wide.columns if col.startswith("lag1_")]
        for c in lag_cols:
            wide[c] = wide[c].fillna(0.0)
        log(f"lag1(t_stock_feat) 特征并入完成，新增数值列={len(lag_cols)}")
    else:
        log("⚠️ 未并入 lag1 特征（t_stock_feat 为空）")

    log("开始合并标签 ...")
    df = pd.merge(wide, y_df, on=["trade_date", "stock_code"], how="inner")
    log(f"合并完成，总样本={len(df)}")

    # === NEW: 覆盖率统计（信号 → 数值 → 总体）
    sig_cols = [c for c in df.columns if c not in ("trade_date", "stock_code", "label") and not c.startswith("lag1_")]
    num_cols = [c for c in df.columns if c.startswith("lag1_")]
    if sig_cols:
        nz_sig = (df[sig_cols].values != 0).sum(axis=1)
        log(f"[训练集] 信号特征非零数：P50={np.percentile(nz_sig, 50)}, P90={np.percentile(nz_sig, 90)}, max={nz_sig.max()}  全零比={(nz_sig == 0).mean():.2%}")
    if num_cols:
        nz_num = (df[num_cols].values != 0).sum(axis=1)
        log(f"[训练集] 量价特征（feat）非零数：P50={np.percentile(nz_num, 50)}, P90={np.percentile(nz_num, 90)}, max={nz_num.max()}  全零比={(nz_num == 0).mean():.2%}")
    feat_cols_all = [c for c in df.columns if c not in ("trade_date", "stock_code", "label")]
    nz_all = (df[feat_cols_all].values != 0).sum(axis=1)
    log(f"[训练集] 合并后特征非零数：P50={np.percentile(nz_all, 50)}, P90={np.percentile(nz_all, 90)}, max={nz_all.max()}  全零比={(nz_all == 0).mean():.2%}")

    pos_rate = float(df["label"].mean()) if len(df) else 0.0
    log(f"标签正例占比={pos_rate:.4f}（全预测为1基线acc≈{pos_rate:.4f}）")

    feature_cols = feat_cols_all
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

        # === NEW: 阈值扫描（可选）
        scan = scan_thresholds(y_test, y_prob, target_precision=0.80, target_recall=0.80)
        log(f"阈值扫描 bestF1: {scan.get('best_f1')}")
        log(f"阈值扫描 P>=0.80: {scan.get('at_P')}")
        log(f"阈值扫描 R>=0.80: {scan.get('at_R')}")

        # === NEW: 按日Top-K（更贴近实盘；示例K=5/10/20）
        mask_test = (df['trade_date'] >= split_date)
        test_df_eval = df.loc[mask_test, ['trade_date', 'stock_code', 'label']].copy().reset_index(drop=True)
        assert len(test_df_eval) == len(y_prob), "y_prob 与测试样本数量不一致，可能发生了顺序错位"  # ←← 推荐加
        test_df_eval['y_prob'] = y_prob
        p5 = float(daily_precision_at_k(test_df_eval.rename(columns={'label': 'y_true'}), k=5))
        p10 = float(daily_precision_at_k(test_df_eval.rename(columns={'label': 'y_true'}), k=10))
        p20 = float(daily_precision_at_k(test_df_eval.rename(columns={'label': 'y_true'}), k=20))
        log(f"按日 Precision@5={p5:.4f}  @10={p10:.4f}  @20={p20:.4f}")

        auc = roc_auc_score(y_test, y_prob)
        ap = average_precision_score(y_test, y_prob)
        log(f"ROC-AUC={auc:.4f}  PR-AUC={ap:.4f}")

        # === NEW: 组装并登记本次训练元数据
        # 训练/验证窗口（与你的 time split 对齐）
        train_dates = sorted(df[df['trade_date'] < split_date]['trade_date'].unique())
        valid_dates = sorted(df[df['trade_date'] >= split_date]['trade_date'].unique())
        train_start, train_end = int(train_dates[0]), int(train_dates[-1])
        valid_start, valid_end = int(valid_dates[0]), int(valid_dates[-1])

        # 特征与参数
        features = {
            "signals": [c for c in feature_cols if not c.startswith("lag1_")],
            "price_lag": [c for c in feature_cols if c.startswith("lag1_")]
        }
        params = {
            "n_estimators": 500, "max_depth": None, "min_samples_split": 4,
            "class_weight": "balanced_subsample", "random_state": random_state
        }

        # 评测指标（把上面已算好的数塞进来）
        metrics = {
            "accuracy": float(acc),
            "auc": float(auc) if 'auc' in locals() else None,
            "pr_auc": float(ap) if 'ap' in locals() else None,
            "p@5": float(p5) if 'p5' in locals() else None,
            "p@10": float(p10) if 'p10' in locals() else None,
            "p@20": float(p20) if 'p20' in locals() else None
        }

        # 版本号/备注
        model_version = f"combo_rf-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        note = "t_stock_feat(lag1_turnover, lag1_amplitude, lag1_pct_chg) + signals; TopK评估启用"

        # 可选：做一个数据指纹（用特征名 + 时间窗口简易hash）
        data_hash_text = "|".join(feature_cols) + f"|{train_start}-{train_end}|{valid_start}-{valid_end}"

        model_id = save_model_meta(
            conn=conn,
            model_version=model_version,
            model_type="RandomForest",
            train_start=train_start, train_end=train_end,
            valid_start=valid_start, valid_end=valid_end,
            label_rule="label = (v1>=1% or v2>=1% or v3>=1%) from t_stock_label_1",
            features=features,
            params=params,
            metrics=metrics,
            artifact_path=MODEL_PATH,
            tag="baseline_topk",
            note=note,
            data_hash_text=data_hash_text
        )
        log(f"[Model Registry] 已登记模型: model_id={model_id}, version={model_version}")

    except Exception as e:
        log(f"计算AUC/PR-AUC失败：{e}")

    fi = pd.DataFrame({"feature": feature_cols, "importance": clf.feature_importances_}).sort_values("importance",
                                                                                                     ascending=False)
    log("Top-30 特征重要性：")
    print(fi.head(30))

    joblib.dump({"model": clf, "feature_cols": feature_cols}, MODEL_PATH)
    log(f"模型已保存到 {MODEL_PATH}")
    return clf, feature_cols


def predict_latest_day(conn, clf, feature_cols, model_version: str = "adhoc", topk: int = 20):
    log("开始获取最新交易日 ...")
    latest_dt = conn.execute("SELECT MAX(trade_date) FROM t_stock_signal").fetchone()[0]
    # 若 t_stock_signal 当天没有数据，则用 t_stock_feat 的最大交易日兜底
    if latest_dt is None:
        alt_dt = conn.execute("SELECT MAX(trade_date) FROM t_stock_feat").fetchone()[0]
        latest_dt = alt_dt
        log(f"t_stock_signal 无记录，改用 t_stock_feat 的最新交易日: {latest_dt}")
    else:
        log(f"最新交易日={latest_dt}")

    # ==============================
    # 1) 原始信号（当日）
    # ==============================
    sig_df = pd.read_sql_query(
        "SELECT trade_date, stock_code, signal_name, signal_value "
        "FROM t_stock_signal WHERE trade_date=?",
        conn, params=(latest_dt,)
    )
    log(f"当天原始信号数={len(sig_df)}")
    if not sig_df.empty:
        top_sig_pred = (sig_df.groupby("signal_name")["signal_value"]
                        .size().sort_values(ascending=False).head(20))
        log("当日最常见原始信号TOP-20：")
        print(top_sig_pred)

    # 统一 join 键的类型
    if not sig_df.empty:
        sig_df["trade_date"] = sig_df["trade_date"].astype(int)
        sig_df["stock_code"] = sig_df["stock_code"].astype(str)

    # 透视为宽表（若 sig_df 为空，得到的是只有键列的空框）
    wide = pivot_signals(sig_df, "signal_name", "signal_value", prefix="s_", use_min_df=False)

    # ==============================
    # 2) 并入 lag1(t_stock_feat)
    # ==============================
    log("开始加载 t_stock_feat（用于 lag1 特征） ...")
    feat_df = pd.read_sql_query(
        "SELECT stock_code, trade_date, turnover, amplitude, pct_chg FROM t_stock_feat",
        conn
    )
    if not feat_df.empty:
        feat_df["trade_date"] = feat_df["trade_date"].astype(int)
        feat_df["stock_code"] = feat_df["stock_code"].astype(str)

        feat_df = feat_df.sort_values(["stock_code", "trade_date"])
        for c in ["turnover", "amplitude", "pct_chg"]:
            feat_df[f"lag1_{c}"] = feat_df.groupby("stock_code")[c].shift(1)

        feat_df = feat_df[["stock_code", "trade_date",
                           "lag1_turnover", "lag1_amplitude", "lag1_pct_chg"]]

        # 合并 lag1 特征
        wide = pd.merge(wide, feat_df, on=["trade_date", "stock_code"], how="left")

        for c in ["lag1_turnover", "lag1_amplitude", "lag1_pct_chg"]:
            if c in wide.columns:
                wide[c] = wide[c].fillna(0.0)
        log("预测日 lag1(t_stock_feat) 特征并入完成")
    else:
        log("⚠️ t_stock_feat 为空，未并入 lag1 特征")

    # 兜底：若当日无任何信号导致 wide 为空，仍然用 lag1_* 做预测底座
    if wide.empty and not feat_df.empty:
        log("当日无信号，使用 lag1 特征作为预测底座")
        base = (feat_df[feat_df["trade_date"] == int(latest_dt)]
                [["trade_date", "stock_code", "lag1_turnover", "lag1_amplitude", "lag1_pct_chg"]]
                .drop_duplicates())
        if not base.empty:
            wide = base.copy()
        else:
            log("⚠️ 当日也缺少 lag1 特征可用样本，预测集为空")
            return pd.DataFrame(columns=["trade_date", "stock_code", "pred_up_prob",
                                         "rank_in_day", "is_topk", "hit_pairs", "hit_triples"])

    # ==============================
    # 3) 对齐训练列（缺失补0）
    # ==============================
    for col in feature_cols:
        if col not in wide.columns:
            wide[col] = 0.0

    X_new = wide[feature_cols].values

    # 覆盖率统计
    nz_day = (X_new != 0).sum(axis=1)
    log(f"[预测集] 当日标的数={len(wide)}")
    if len(nz_day) > 0:
        log(f"  P50={np.percentile(nz_day, 50)}, P90={np.percentile(nz_day, 90)}, max={nz_day.max()}")
        log(f"  全零样本比例={(nz_day == 0).mean():.2%}")

    # ==============================
    # 4) 预测 + 排序 + TopK
    # ==============================
    log("开始预测最新交易日 ...")
    proba = clf.predict_proba(X_new)[:, 1]
    out = wide[["trade_date", "stock_code"]].copy()
    out["pred_up_prob"] = proba

    # 占位（暂不启用组合）
    out["hit_pairs"] = None
    out["hit_triples"] = None

    # 排序 & 标注TopK
    out = out.sort_values("pred_up_prob", ascending=False).reset_index(drop=True)
    out["rank_in_day"] = np.arange(1, len(out) + 1, dtype=int)
    out["is_topk"] = (out["rank_in_day"] <= int(topk)).astype(int)

    # 打印TopK
    log(f"[预测日] Top-{topk} 清单：")
    print(out.head(topk)[["trade_date", "stock_code", "pred_up_prob",
                          "rank_in_day", "hit_pairs", "hit_triples"]])

    # ==============================
    # 5) 批量落库 t_model_pred
    # ==============================
    rows = [
        (
            model_version,
            int(r.trade_date),
            str(r.stock_code),
            float(r.pred_up_prob),
            int(r.rank_in_day),
            int(r.is_topk),
            r.hit_pairs if pd.notna(r.hit_pairs) else None,
            r.hit_triples if pd.notna(r.hit_triples) else None,
        )
        for r in out.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO t_model_pred
        (model_version, trade_date, stock_code, pred_up_prob, rank_in_day, is_topk, hit_pairs, hit_triples)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows
    )
    conn.commit()
    log(f"[DB] 已写入 t_model_pred：{len(rows)} 行（model_version={model_version}, trade_date={latest_dt}）")

    # 返回 TopK DataFrame，方便外部使用
    return out.head(topk).copy()



if __name__ == "__main__":
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"数据库文件不存在：{DB_PATH}")

    with sqlite3.connect(DB_PATH) as conn:
        clf, feature_cols = train_and_eval(conn)
        # 这里如果你在 train_and_eval 里创建了 model_version，可 return 回来；
        # 假设我们在那里保存为了全局变量或直接再查最近一条：
        model_version = conn.execute(
            "SELECT model_version FROM t_model_meta ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
        predict_latest_day(conn, clf, feature_cols, model_version=model_version, topk=20)
