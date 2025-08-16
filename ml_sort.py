# ltr_train_and_pick.py
import os, json
import numpy as np
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine, text
from joblib import dump
from lightgbm import LGBMRanker
from sklearn.metrics import ndcg_score

# ========= 配置 =========
DB_URI = "mysql+pymysql://user:pwd@host:3306/yourdb?charset=utf8mb4"
ENGINE = create_engine(DB_URI)

MODEL_FAMILY   = "ltr_lgbm"     # 模型系列名
LOOKBACK_MONTH = 24             # 训练回看窗口（按需要）
VALID_RATIO    = 0.2            # 末端 20% 交易日做验证
MODEL_DIR      = "models"       # 模型文件存放目录
TOP1_THRESHOLD = None           # 可选：Top1阈值（LambdaRank分数阈值）；None 表示不设阈值

FEATURE_COLS = [f"score_{i}" for i in range(1, 31)]

LGBM_PARAMS = dict(
    objective="lambdarank",
    metric="ndcg",
    learning_rate=0.05,
    num_leaves=63,
    n_estimators=2000,          # 可加早停回调（sklearn接口不内置早停）
    subsample=0.9,
    colsample_bytree=0.8,
    random_state=42,
    n_jobs=-1
)

# ========= 工具函数 =========
def time_split_by_date(df: pd.DataFrame, valid_ratio=0.2):
    """按交易日做时间切分：后 valid_ratio 的交易日作为验证集"""
    dates = np.array(sorted(df["trade_date"].unique()))
    cut = int(len(dates) * (1 - valid_ratio))
    tr_days, va_days = dates[:cut], dates[cut:]
    tr = df[df["trade_date"].isin(tr_days)].copy()
    va = df[df["trade_date"].isin(va_days)].copy()
    return tr, va

def ltr_group_counts(df: pd.DataFrame) -> list:
    """LightGBM ranker 的 group = 每个 query（每日）的样本数"""
    return df.groupby("trade_date").size().astype(int).tolist()

def register_model(model_version, train_start, train_end, valid_start, valid_end,
                   params_json, metrics_json, artifact_path) -> int:
    with ENGINE.begin() as conn:
        conn.execute(text("""
          INSERT INTO t_ltr_model_meta(
            model_version, model_type, train_start, train_end, valid_start, valid_end,
            params_json, metrics_json, artifact_path
          ) VALUES(
            :v, 'LGBMRanker', :ts, :te, :vs, :ve,
            CAST(:pj AS JSON), CAST(:mj AS JSON), :ap
          )
          ON DUPLICATE KEY UPDATE
            train_start=:ts, train_end=:te, valid_start=:vs, valid_end=:ve,
            params_json=CAST(:pj AS JSON), metrics_json=CAST(:mj AS JSON), artifact_path=:ap
        """), dict(v=model_version, ts=train_start, te=train_end,
                   vs=valid_start, ve=valid_end,
                   pj=json.dumps(params_json, ensure_ascii=False),
                   mj=json.dumps(metrics_json, ensure_ascii=False),
                   ap=artifact_path))
        mid = conn.execute(text("SELECT model_id FROM t_ltr_model_meta WHERE model_version=:v"),
                           dict(v=model_version)).scalar()
    return int(mid)

def upsert_pred_scores(model_id: int, pred_df: pd.DataFrame):
    """写入 t_ltr_pred_score（幂等）"""
    rows = pred_df[["trade_date","stock_code","score","rank_no"]].to_dict("records")
    with ENGINE.begin() as conn:
        conn.execute(text("""
          INSERT INTO t_ltr_pred_score(trade_date, stock_code, model_id, score, rank_no)
          VALUES (:trade_date,:stock_code,:model_id,:score,:rank_no)
          ON DUPLICATE KEY UPDATE score=VALUES(score), rank_no=VALUES(rank_no), updated_at=NOW()
        """), [dict(**r, model_id=model_id) for r in rows])

def upsert_top1_pick(model_id: int, pick_df: pd.DataFrame, threshold_used):
    """写入每日 Top1（或 SKIP）到 t_ltr_pick_top1"""
    rows = pick_df.to_dict("records")
    with ENGINE.begin() as conn:
        conn.execute(text("""
          INSERT INTO t_ltr_pick_top1(trade_date, model_id, stock_code, score, rank_no, threshold_used, decision)
          VALUES (:trade_date,:model_id,:stock_code,:score,:rank_no,:threshold_used,:decision)
          ON DUPLICATE KEY UPDATE stock_code=VALUES(stock_code), score=VALUES(score),
                                  rank_no=VALUES(rank_no), threshold_used=VALUES(threshold_used),
                                  decision=VALUES(decision), updated_at=NOW()
        """), rows)

# ========= 主流程 =========
def main():
    # 1) 取训练窗口数据（可按 LOOKBACK_MONTH 裁剪；这里直接取所有训练样本）
    df = pd.read_sql(text("SELECT * FROM v_candidate_with_label")), ENGINE

    # 如果需要时间窗口限制，取消注释：
    # latest = df["trade_date"].max()
    # train_start = (pd.to_datetime(latest) - relativedelta(months=LOOKBACK_MONTH)).date()
    # df = df[df["trade_date"] >= pd.to_datetime(train_start)]

    df = df.sort_values(["trade_date", "stock_code"]).reset_index(drop=True)
    # 严格过滤掉缺失特征的行
    df = df.dropna(subset=FEATURE_COLS + ["label"])

    # 2) 时间切分（后 20% 交易日为验证集）
    tr, va = time_split_by_date(df, VALID_RATIO)

    X_tr = tr[FEATURE_COLS].values
    y_tr = tr["label"].values.astype(int)
    g_tr = ltr_group_counts(tr)

    X_va = va[FEATURE_COLS].values
    y_va = va["label"].values.astype(int)
    g_va = ltr_group_counts(va)

    # 3) 训练 LGBMRanker（LambdaRank）
    ranker = LGBMRanker(**LGBM_PARAMS)
    ranker.fit(
        X_tr, y_tr,
        group=g_tr,
        eval_set=[(X_va, y_va)],
        eval_group=[g_va],
        eval_at=[1]  # 关注@1（每天只选一只）
    )

    # 4) 简单评估：NDCG@1（用 sklearn 的 ndcg_score）
    # 需要按“每天”为一个 query 拆分
    def ndcg_at1(df_eval):
        scores = []
        labels = []
        for _, day_df in df_eval.groupby("trade_date"):
            Xd = day_df[FEATURE_COLS].values
            yd = day_df["label"].values.astype(int)
            sd = ranker.predict(Xd)
            scores.append(sd.reshape(1, -1))
            labels.append(yd.reshape(1, -1))
        if not scores:
            return None
        return float(np.mean([ndcg_score(l, s, k=1) for l, s in zip(labels, scores)]))

    ndcg1_val = ndcg_at1(va)
    metrics = {"ndcg@1_valid": ndcg1_val}
    print("NDCG@1 (valid):", ndcg1_val)

    # 5) 保存模型与元数据
    os.makedirs(MODEL_DIR, exist_ok=True)
    latest_day = df["trade_date"].max()
    earliest_day = df["trade_date"].min()

    # 验证窗口的起止
    va_days = sorted(va["trade_date"].unique())
    valid_start = va_days[0] if va_days else latest_day
    valid_end   = va_days[-1] if va_days else latest_day

    model_version = f"{MODEL_FAMILY}-{latest_day}"
    artifact_path = os.path.join(MODEL_DIR, f"{model_version}.joblib")
    dump({"model": ranker, "features": FEATURE_COLS}, artifact_path)

    model_id = register_model(
        model_version=model_version,
        train_start=earliest_day, train_end=latest_day,
        valid_start=valid_start, valid_end=valid_end,
        params_json=LGBM_PARAMS,
        metrics_json=metrics,
        artifact_path=artifact_path
    )
    print("model_id:", model_id)

    # 6) 预测并写入逐日分数与排名（这里演示对“整个表”做评分；
    #    你也可以改成只对 next_ret IS NULL 的“新数据日”预测）
    df_pred = pd.read_sql(text("""
      SELECT trade_date, stock_code, {cols}
      FROM t_candidate_stocks
    """.format(cols=",".join(FEATURE_COLS))), ENGINE).sort_values(["trade_date","stock_code"])
    # 若需仅预测 next_ret IS NULL 的，改为：
    # df_pred = pd.read_sql(text("""SELECT trade_date, stock_code, {cols}
    #                               FROM t_candidate_stocks WHERE next_ret IS NULL""".format(cols=",".join(FEATURE_COLS))), ENGINE)

    df_pred = df_pred.dropna(subset=FEATURE_COLS)
    df_pred["score"] = ranker.predict(df_pred[FEATURE_COLS].values)

    # 逐日排名
    df_pred["rank_no"] = df_pred.groupby("trade_date")["score"].rank(method="first", ascending=False).astype(int)

    # 落库：t_ltr_pred_score
    upsert_pred_scores(model_id, df_pred)

    # 7) 生成每日 Top1 拣选（可配阈值，不满足则 SKIP）
    picks = []
    for d, g in df_pred.groupby("trade_date"):
        g1 = g.sort_values("score", ascending=False).head(1)
        top_score = float(g1["score"].iloc[0])
        if TOP1_THRESHOLD is not None and top_score < TOP1_THRESHOLD:
            picks.append({
                "trade_date": d, "model_id": model_id, "stock_code": None,
                "score": None, "rank_no": None, "threshold_used": TOP1_THRESHOLD,
                "decision": "SKIP"
            })
        else:
            picks.append({
                "trade_date": d, "model_id": model_id,
                "stock_code": g1["stock_code"].iloc[0],
                "score": top_score, "rank_no": int(g1["rank_no"].iloc[0]),
                "threshold_used": float(TOP1_THRESHOLD) if TOP1_THRESHOLD is not None else 0.0,
                "decision": "TRADE"
            })
    picks_df = pd.DataFrame(picks)
    upsert_top1_pick(model_id, picks_df, threshold_used=float(TOP1_THRESHOLD) if TOP1_THRESHOLD is not None else 0.0)

    print("done.")

if __name__ == "__main__":
    main()
