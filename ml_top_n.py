# rk_pipeline.py
# pip install pandas scikit-learn SQLAlchemy pymysql joblib python-dateutil scipy
import os, json
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
from joblib import dump
from datetime import date
from dateutil.relativedelta import relativedelta
from scipy import sparse

# ========= 配置 =========
DB_URI = "mysql+pymysql://user:pwd@host:3306/yourdb?charset=utf8mb4"
ENGINE = create_engine(DB_URI)

MODEL_NAME   = "combo_rf"       # 系列名
LOOKBACK_M   = 18               # 训练回看月数
VALID_LAST_M = 1                # 验证末月
MODEL_DIR    = "models"         # 模型文件目录
RF_PARAMS = dict(
    n_estimators=400,
    max_depth=16,
    min_samples_leaf=20,
    class_weight="balanced",
    n_jobs=-1,
    random_state=42
)
LABEL_RULE = "label = (max(ret_high(v1,v2,v3)) >= 1%)"

# ========= 工具函数 =========
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
    从长表构造 (日-股) x (xg_id_onehot) 稀疏特征矩阵
    未出现=0；同一(日-股, xg_id) 取 val 的 max(默认1)
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

    mat = pd.crosstab(index=ev["k"], columns=ev["col"], values=ev["val"], aggfunc="max", dropna=False).fillna(0.0)
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

def register_model_meta(model_version: str, model_type: str,
                        train_start, train_end, valid_start, valid_end,
                        features, params_dict, metrics_dict, artifact_path: str):
    """写 t_model_meta，返回 model_id（若版本存在则更新并查询其 id）"""
    with ENGINE.begin() as conn:
        conn.execute(text("""
          INSERT INTO t_model_meta(
            model_version, model_type,
            train_start, train_end, valid_start, valid_end,
            label_rule, features_json, params_json, metrics_json, artifact_path
          ) VALUES(
            :v, :t, :ts, :te, :vs, :ve,
            :lr, CAST(:fj AS JSON), CAST(:pj AS JSON), CAST(:mj AS JSON), :ap
          )
          ON DUPLICATE KEY UPDATE
            model_type=:t, train_start=:ts, train_end=:te, valid_start=:vs, valid_end=:ve,
            label_rule=:lr, features_json=CAST(:fj AS JSON),
            params_json=CAST(:pj AS JSON), metrics_json=CAST(:mj AS JSON), artifact_path=:ap
        """), dict(
            v=model_version, t=model_type,
            ts=train_start, te=train_end, vs=valid_start, ve=valid_end,
            lr=LABEL_RULE, fj=json.dumps(features, ensure_ascii=False),
            pj=json.dumps(params_dict, ensure_ascii=False),
            mj=json.dumps(metrics_dict, ensure_ascii=False), ap=artifact_path
        ))
        # 查回 model_id
        model_id = conn.execute(text("SELECT model_id FROM t_model_meta WHERE model_version=:v"),
                                dict(v=model_version)).scalar()
    return int(model_id)

def upsert_combo_scores(model_id: int, pred_df: pd.DataFrame):
    """写 t_combo_pick_stock：trade_date, stock_code, model_id, score"""
    rows = pred_df[["trade_date","stock_code","score"]].to_dict("records")
    with ENGINE.begin() as conn:
        conn.execute(text("""
          INSERT INTO t_combo_pick_stock(trade_date, stock_code, model_id, score)
          VALUES (:trade_date, :stock_code, :model_id, :score)
          ON DUPLICATE KEY UPDATE score=VALUES(score), updated_at=NOW()
        """), [dict(**r, model_id=model_id) for r in rows])

# ========= 主流程 =========
def run_once(asof: date=None):
    """
    asof：训练截止的“最近交易日”（若不传，则取 <=今天 最近的开市日）
    训练窗口：asof 往前 LOOKBACK_M 月；验证窗口：末尾 VALID_LAST_M 月
    预测：asof 的下一交易日
    """
    if asof is None:
        asof = latest_trading_day(date.today())
    if not asof:
        print("未找到最近交易日"); return

    train_start = (pd.to_datetime(asof) - relativedelta(months=LOOKBACK_M)).date()
    valid_start = (pd.to_datetime(asof) - relativedelta(months=VALID_LAST_M)).date()

    # 1) 训练窗口特征与标签
    idx_all, X_all, cols = load_signal_matrix(train_start, asof)
    if X_all is None or X_all.shape[0] == 0:
        print(f"[{asof}] 无训练信号"); return
    y_all = load_labels_for(idx_all)
    if y_all is None or y_all.sum() == 0:
        print(f"[{asof}] 无标签或正样本为0"); return

    # 2) 时间切分
    mask_tr = idx_all["trade_date"] < valid_start
    mask_va = idx_all["trade_date"] >= valid_start
    X_tr, y_tr = X_all[mask_tr], y_all[mask_tr]
    X_va, y_va = X_all[mask_va], y_all[mask_va]

    # 3) 训练模型
    clf = RandomForestClassifier(**RF_PARAMS)
    clf.fit(X_tr, y_tr)
    metrics = {}
    if X_va.shape[0] > 0 and y_va.sum() > 0:
        metrics["auc"] = float(roc_auc_score(y_va, clf.predict_proba(X_va)[:,1]))
        metrics["n_train"] = int(X_tr.shape[0])
        metrics["n_valid"] = int(X_va.shape[0])
        metrics["pos_rate_train"] = float(np.mean(y_tr))
        metrics["pos_rate_valid"] = float(np.mean(y_va))
        print(f"[{asof}] AUC={metrics['auc']:.4f}  train={X_tr.shape[0]}  valid={X_va.shape[0]}")

    # 4) 保存模型与列空间
    os.makedirs(MODEL_DIR, exist_ok=True)
    model_version = f"{MODEL_NAME}-{asof}"
    artifact_path = os.path.join(MODEL_DIR, f"{model_version}.joblib")
    dump({"model": clf, "cols": cols}, artifact_path)

    # 5) 登记模型元数据，获取 model_id
    model_id = register_model_meta(
        model_version=model_version,
        model_type="RandomForest",
        train_start=train_start, train_end=asof,
        valid_start=valid_start, valid_end=asof,
        features=cols, params_dict=RF_PARAMS, metrics_dict=metrics,
        artifact_path=artifact_path
    )

    # 6) 预测 asof 的下一交易日
    pred_date = next_trading_day(asof)
    if not pred_date:
        print(f"[{asof}] 无下一交易日"); return

    # D+1 的特征矩阵（并对齐列）
    idx_pred, X_pred_raw, pred_cols = load_signal_matrix(pred_date, pred_date)
    if X_pred_raw is None:
        print(f"[{pred_date}] 当日无任何信号记录"); return

    # 列对齐（训练列为基准：缺列补0，多列丢弃）
    need = pd.Index(cols)
    have = pd.Index(pred_cols)
    mat_pred = pd.DataFrame.sparse.from_spmatrix(X_pred_raw, index=idx_pred.index, columns=pred_cols)
    mat_pred = mat_pred.reindex(columns=need, fill_value=0.0)
    X_pred = sparse.csr_matrix(mat_pred.sparse.to_coo())

    proba = clf.predict_proba(X_pred)[:,1]
    out = idx_pred.copy()
    out["score"] = proba
    out["trade_date"] = pred_date  # 明确写回日期

    # 7) 回写预测（按 model_id 幂等 upsert）
    upsert_combo_scores(model_id, out)
    print(f"[{pred_date}] upsert {len(out)} rows (model_id={model_id}).")

if __name__ == "__main__":
    run_once()  # 日常调度：收盘后跑
    # 历史回补可循环 trading days 调用 run_once(asof=某日)
