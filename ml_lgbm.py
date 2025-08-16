# rk_lgbm_from_rf.py
# 以“RF筛选的特征”为列空间，训练 LightGBM；
# 训练登记到 t_model_meta；预测写入 t_combo_pick_stock_lgbm（不碰RF结果表）。
import os, json
import pandas as pd
import numpy as np
from datetime import date
from dateutil.relativedelta import relativedelta
from scipy import sparse
from sqlalchemy import create_engine, text
from joblib import dump
from sklearn.metrics import roc_auc_score
import lightgbm as lgb

# ============= 配置 =============
DB_URI = "mysql+pymysql://user:pwd@host:3306/yourdb?charset=utf8mb4"
ENGINE = create_engine(DB_URI)

MODEL_FAMILY   = "combo_lgbm"   # LGBM 系列名
LOOKBACK_M     = 18             # 训练回看月数
VALID_LAST_M   = 1              # 验证末月
MODEL_DIR      = "models"       # 模型文件目录

# 训练参数（按需调整）
LGBM_PARAMS = dict(
    objective="binary",
    boosting_type="gbdt",
    num_leaves=64,
    max_depth=-1,
    learning_rate=0.05,
    n_estimators=5000,          # 配合早停
    subsample=0.9,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    random_state=42,
    n_jobs=-1
)
EARLY_STOP = 200                # 早停

LABEL_RULE = "label = (max(ret_high over {v1,v2,v3}) >= 1%)"

# ============= 工具函数 =============
def latest_trading_day(upto: date) -> date:
    sql = "SELECT MAX(trade_date) d FROM t_trade_calendar WHERE is_open=1 AND trade_date<=:u"
    d = pd.read_sql(text(sql), ENGINE, params={"u": upto})["d"].iloc[0]
    return d.date() if pd.notna(d) else None

def next_trading_day(d: date) -> date:
    sql = "SELECT MIN(trade_date) d FROM t_trade_calendar WHERE is_open=1 AND trade_date>:d"
    nd = pd.read_sql(text(sql), ENGINE, params={"d": d})["d"].iloc[0]
    return nd.date() if pd.notna(nd) else None

def load_signal_matrix(start_dt, end_dt):
    """
    从 t_signal_events 取 (trade_date, stock_code, xg_id, val)
    → (日-股) x (xg_id_onehot) 稀疏矩阵；未出现=0
    """
    ev = pd.read_sql(text("""
      SELECT trade_date, stock_code, xg_id, val
      FROM t_signal_events
      WHERE trade_date BETWEEN :s AND :e
    """), ENGINE, params={"s": start_dt, "e": end_dt})

    if ev.empty:
        return None, None, None

    ev["k"] = ev["trade_date"].astype(str) + "|" + ev["stock_code"]
    ev["col"] = "xg_" + ev["xg_id"].astype(str)

    mat = pd.crosstab(index=ev["k"], columns=ev["col"],
                      values=ev["val"], aggfunc="max", dropna=False).fillna(0.0)
    X = sparse.csr_matrix(mat.values)
    idx = mat.index.str.split("|", expand=True)
    index_df = pd.DataFrame({
        "trade_date": pd.to_datetime(idx.get_level_values(0)).date,
        "stock_code": idx.get_level_values(1)
    })
    cols = mat.columns.tolist()
    return index_df, X, cols

def load_labels_for(index_df: pd.DataFrame):
    if index_df is None or index_df.empty:
        return None
    lab = pd.read_sql(text("""
      SELECT trade_date, stock_code, label
      FROM vw_sample_label
      WHERE trade_date BETWEEN :s AND :e
    """), ENGINE, params={"s": index_df["trade_date"].min(), "e": index_df["trade_date"].max()})
    out = index_df.merge(lab, on=["trade_date","stock_code"], how="left").fillna({"label":0})
    return out["label"].astype(int).values

def get_rf_selected_cols(rf_model_id: int = None):
    """
    返回 RF 入选特征列名（列表，形如 ['xg_12','xg_87',...]）
    优先从 t_rf_selected_features 读取；若无，再从 t_model_meta.features_json 读取。
    rf_model_id 为空时，自动选择最近的 RF 模型。
    """
    # 1) 定位 RF 模型
    if rf_model_id is None:
        rf_model_id = pd.read_sql(text("""
          SELECT model_id FROM t_model_meta
          WHERE model_type='RandomForest'
          ORDER BY created_at DESC LIMIT 1
        """), ENGINE).squeeze()
        if rf_model_id is None or pd.isna(rf_model_id):
            raise RuntimeError("未找到任何 RandomForest 模型（请先完成RF筛选与登记）")
        rf_model_id = int(rf_model_id)

    # 2) 优先读行式表
    df = pd.read_sql(text("""
      SELECT feature_name FROM t_rf_selected_features
      WHERE model_id=:mid ORDER BY rank_order ASC
    """), ENGINE, params={"mid": rf_model_id})
    if not df.empty:
        return rf_model_id, df["feature_name"].tolist()

    # 3) 退回到 t_model_meta.features_json
    meta = pd.read_sql(text("""
      SELECT features_json FROM t_model_meta WHERE model_id=:mid
    """), ENGINE, params={"mid": rf_model_id}).squeeze()
    if pd.isna(meta) or not meta:
        raise RuntimeError(f"RF模型 {rf_model_id} 未找到入选特征（t_rf_selected_features / features_json 均为空）")
    try:
        feats = json.loads(meta)
        if not isinstance(feats, list) or len(feats) == 0:
            raise ValueError
        return rf_model_id, feats
    except Exception:
        raise RuntimeError(f"RF模型 {rf_model_id} 的 features_json 不是有效JSON数组")

def register_model_meta(model_version: str, train_start, train_end, valid_start, valid_end,
                        features, params_dict, metrics_dict, artifact_path: str):
    """登记 LGBM 模型到 t_model_meta，返回 model_id"""
    with ENGINE.begin() as conn:
        conn.execute(text("""
          INSERT INTO t_model_meta(
            model_version, model_type,
            train_start, train_end, valid_start, valid_end,
            label_rule, features_json, params_json, metrics_json, artifact_path
          ) VALUES(
            :v, 'LightGBM',
            :ts, :te, :vs, :ve,
            :lr, CAST(:fj AS JSON), CAST(:pj AS JSON), CAST(:mj AS JSON), :ap
          )
          ON DUPLICATE KEY UPDATE
            train_start=:ts, train_end=:te, valid_start=:vs, valid_end=:ve,
            label_rule=:lr, features_json=CAST(:fj AS JSON),
            params_json=CAST(:pj AS JSON), metrics_json=CAST(:mj AS JSON), artifact_path=:ap
        """), dict(
            v=model_version, ts=train_start, te=train_end, vs=valid_start, ve=valid_end,
            lr=LABEL_RULE, fj=json.dumps(features, ensure_ascii=False),
            pj=json.dumps(params_dict, ensure_ascii=False),
            mj=json.dumps(metrics_dict, ensure_ascii=False), ap=artifact_path
        ))
        model_id = conn.execute(text("SELECT model_id FROM t_model_meta WHERE model_version=:v"),
                                dict(v=model_version)).scalar()
    return int(model_id)

def upsert_lgbm_scores(model_id: int, pred_df: pd.DataFrame):
    """写入 LGBM 结果表：t_combo_pick_stock_lgbm"""
    rows = pred_df[["trade_date","stock_code","score"]].to_dict("records")
    sql = """
      INSERT INTO t_combo_pick_stock_lgbm(trade_date, stock_code, model_id, score)
      VALUES (:trade_date, :stock_code, :model_id, :score)
      ON DUPLICATE KEY UPDATE score=VALUES(score), updated_at=NOW()
    """
    with ENGINE.begin() as conn:
        conn.execute(text(sql), [dict(**r, model_id=model_id) for r in rows])

# ============= 主流程 =============
def run_lgbm_from_rf(asof: date=None, rf_model_id: int=None):
    """
    以 RF 入选特征为列空间训练 LGBM：
    - 训练截止=asof（最近交易日）；预测 asof 的下一交易日
    - 只写 t_combo_pick_stock_lgbm，不写 RF 的结果表
    """
    if asof is None:
        asof = latest_trading_day(date.today())
    if not asof:
        print("未找到最近交易日"); return

    train_start = (pd.to_datetime(asof) - relativedelta(months=LOOKBACK_M)).date()
    valid_start = (pd.to_datetime(asof) - relativedelta(months=VALID_LAST_M)).date()

    # A) 拿到 RF 入选特征
    rf_model_id, selected_cols = get_rf_selected_cols(rf_model_id)
    if not selected_cols:
        raise RuntimeError("RF 入选特征为空，无法训练 LGBM")

    # B) 加载训练窗口特征矩阵（全量列），再裁剪到 selected_cols
    idx_all, X_all, cols_all = load_signal_matrix(train_start, asof)
    if X_all is None or X_all.shape[0] == 0:
        print(f"[{asof}] 无训练信号"); return
    y_all = load_labels_for(idx_all)
    if y_all is None or y_all.sum() == 0:
        print(f"[{asof}] 无标签或正样本=0"); return

    # 列对齐（训练集合：全列→选列）
    col_index = pd.Index(cols_all)
    keep_pos = col_index.get_indexer(selected_cols)
    # get_indexer 对不存在列返回 -1，做一下过滤
    valid_mask = keep_pos >= 0
    keep_pos = keep_pos[valid_mask]
    used_cols = [selected_cols[i] for i, ok in enumerate(valid_mask) if ok]  # 实际可用的列
    if len(used_cols) == 0:
        raise RuntimeError("RF入选列在训练窗口内均不存在，请检查列名一致性（xg_前缀等）")

    X_all_sel = X_all[:, keep_pos]

    # 时间切分
    mask_tr = idx_all["trade_date"] < valid_start
    mask_va = idx_all["trade_date"] >= valid_start
    X_tr, y_tr = X_all_sel[mask_tr], y_all[mask_tr]
    X_va, y_va = X_all_sel[mask_va], y_all[mask_va]

    # 类不平衡权重
    pos = max(1, int(y_tr.sum()))
    neg = max(1, int(len(y_tr) - pos))
    scale_pos_weight = neg / pos

    # C) 训练 LightGBM
    lgbm = lgb.LGBMClassifier(**{**LGBM_PARAMS, "scale_pos_weight": scale_pos_weight})
    lgbm.fit(
        X_tr, y_tr,
        eval_set=[(X_va, y_va)],
        eval_metric="auc",
        callbacks=[lgb.early_stopping(stopping_rounds=EARLY_STOP, verbose=False)]
    )

    auc = float(roc_auc_score(y_va, lgbm.predict_proba(X_va)[:,1])) if X_va.shape[0]>0 and y_va.sum()>0 else None
    best_iter = int(lgbm.best_iteration_) if getattr(lgbm, "best_iteration_", None) else lgbm.n_estimators
    booster = lgbm.booster_
    fi_gain = booster.feature_importance(importance_type="gain")
    fi_pairs = sorted(zip(used_cols, fi_gain), key=lambda x: -x[1])
    top_fi = [{"feature": f, "gain": float(g)} for f, g in fi_pairs[:100]]

    metrics = {
        "auc": auc,
        "best_iteration": best_iter,
        "n_train": int(X_tr.shape[0]),
        "n_valid": int(X_va.shape[0]),
        "pos_rate_train": float(np.mean(y_tr)),
        "pos_rate_valid": float(np.mean(y_va)),
        "rf_model_id": int(rf_model_id),
        "top_importance_gain": top_fi
    }

    # D) 保存模型 + 登记元数据
    os.makedirs(MODEL_DIR, exist_ok=True)
    model_version = f"{MODEL_FAMILY}-{asof}"
    artifact_path = os.path.join(MODEL_DIR, f"{model_version}.joblib")
    dump({"model": lgbm, "cols": used_cols}, artifact_path)

    params_rec = {
        "lgbm_params": LGBM_PARAMS,
        "scale_pos_weight": scale_pos_weight,
        "early_stopping": EARLY_STOP,
        "rf_model_id": int(rf_model_id)
    }
    lgbm_model_id = register_model_meta(
        model_version=model_version,
        train_start=train_start, train_end=asof,
        valid_start=valid_start, valid_end=asof,
        features=used_cols, params_dict=params_rec,
        metrics_dict=metrics, artifact_path=artifact_path
    )
    print(f"[{asof}] LGBM model_id={lgbm_model_id}  AUC={auc}")

    # E) 预测 asof 的下一交易日，写入 LGBM 专用表
    pred_date = next_trading_day(asof)
    if not pred_date:
        print(f"[{asof}] 无下一交易日"); return

    idx_pred, X_pred_raw, pred_cols = load_signal_matrix(pred_date, pred_date)
    if X_pred_raw is None:
        print(f"[{pred_date}] 当日无任何信号记录"); return

    # 将预测矩阵对齐到 used_cols（缺列补0，多列丢弃）
    pred_index = pd.Index(pred_cols)
    pos_pred = pred_index.get_indexer(used_cols)
    valid2 = pos_pred >= 0
    # 已存在的列
    exists_pos = pos_pred[valid2]
    # 对不存在的列需要补 0（构造一个空列并 hstack）
    X_pred_sel = X_pred_raw[:, exists_pos]
    missing_cnt = (~valid2).sum()
    if missing_cnt > 0:
        # 追加缺失列（全零）
        zeros = sparse.csr_matrix((X_pred_sel.shape[0], int(missing_cnt)))
        X_pred_sel = sparse.hstack([X_pred_sel, zeros], format="csr")

    scores = lgbm.predict_proba(X_pred_sel)[:,1]
    out = idx_pred.copy()
    out["trade_date"] = pred_date
    out["score"] = scores
    upsert_lgbm_scores(lgbm_model_id, out)
    print(f"[{pred_date}] LGBM upsert {len(out)} rows (model_id={lgbm_model_id}).")

if __name__ == "__main__":
    run_lgbm_from_rf()  # 收盘后调度；可传 rf_model_id=123 指定RF版本
