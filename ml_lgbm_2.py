# portfolio_build_min.py
# pip install pandas numpy SQLAlchemy pymysql shap joblib
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
import joblib, shap
from scipy import sparse
import os

DB_URI = "mysql+pymysql://user:pwd@host:3306/yourdb?charset=utf8mb4"
ENGINE = create_engine(DB_URI)

TOP_N = 30          # 组合成分数
TEMP  = 8.0         # softmax 温度
SAVE_SHAP_TOPK = 5  # 每只股票保存前K个解释；=0则跳过
EXPORT_DIR = "exports"  # 导出CSV目录

def softmax_weight(scores: np.ndarray, tau: float = 8.0) -> np.ndarray:
    """把分数映射为权重；tau 越大越尖锐。"""
    z = (scores - scores.mean()) / (scores.std() + 1e-9)
    e = np.exp(z * tau)
    return e / e.sum()

def load_signal_matrix(trade_date: str):
    """读取某日的 (日-股)×(xg_id) one-hot 稀疏矩阵与列名，用于SHAP解释对齐"""
    ev = pd.read_sql(text("""
      SELECT trade_date, stock_code, CONCAT('xg_', xg_id) AS col, val
      FROM t_signal_events
      WHERE trade_date = :d
    """), ENGINE, params={"d": trade_date})
    if ev.empty:
        return None, None, None
    ev["k"] = ev["trade_date"].astype(str) + "|" + ev["stock_code"]
    mat = pd.crosstab(index=ev["k"], columns=ev["col"],
                      values=ev["val"], aggfunc="max", dropna=False).fillna(0.0)
    idx = mat.index.str.split("|", expand=True)
    index_df = pd.DataFrame({"trade_date": idx.get_level_values(0), "stock_code": idx.get_level_values(1)})
    cols = mat.columns.tolist()
    X = sparse.csr_matrix(mat.values)
    return index_df, X, cols

def build_portfolio(trade_date: str, model_id: int):
    # 1) 取当日 LGBM 分数 TopN
    df = pd.read_sql(text("""
      SELECT stock_code, score
      FROM t_combo_pick_stock_lgbm
      WHERE trade_date=:d AND model_id=:m
      ORDER BY score DESC
      LIMIT :n
    """), ENGINE, params={"d": trade_date, "m": model_id, "n": TOP_N})
    if df.empty:
        print("no rows"); return

    # 2) 生成权重并落表
    df["rank_no"] = np.arange(1, len(df)+1)
    df["weight"]  = softmax_weight(df["score"].values, tau=TEMP)
    df["trade_date"] = trade_date
    df["model_id"]   = model_id

    with ENGINE.begin() as conn:
        conn.execute(text("""
          INSERT INTO t_portfolio_lgbm(trade_date, model_id, stock_code, rank_no, score, weight)
          VALUES (:trade_date,:model_id,:stock_code,:rank_no,:score,:weight)
          ON DUPLICATE KEY UPDATE rank_no=VALUES(rank_no), score=VALUES(score),
                                  weight=VALUES(weight), updated_at=NOW()
        """), df[["trade_date","model_id","stock_code","rank_no","score","weight"]].to_dict("records"))
    print(f"[{trade_date}] portfolio upsert: {len(df)} rows")

    # 3) 导出 CSV（供任何外部软件导入）
    os.makedirs(EXPORT_DIR, exist_ok=True)
    csv_path = os.path.join(EXPORT_DIR, f"portfolio_{trade_date}_{model_id}.csv")
    df[["stock_code","weight","score","rank_no"]].to_csv(csv_path, index=False, encoding="utf-8")
    print(f"CSV exported -> {csv_path}")

    # 4) （可选）保存 SHAP Top-K 解释（仅对组合内成分计算，避免太重）
    if SAVE_SHAP_TOPK > 0:
        # 载入模型与列空间
        meta = pd.read_sql(text("""
          SELECT artifact_path, features_json FROM t_model_meta WHERE model_id=:m
        """), ENGINE, params={"m": model_id}).iloc[0]
        blob = joblib.load(meta["artifact_path"])
        model = blob["model"]; cols = blob["cols"]  # 训练时使用的列顺序

        # 取当日特征（全列）→ 对齐到训练列
        idx_all, X_all, pred_cols = load_signal_matrix(trade_date)
        if X_all is None:
            print("no features for shap"); return
        # 只保留组合内股票顺序
        want = pd.Index(idx_all["stock_code"]).get_indexer(df["stock_code"])
        mask = want >= 0
        if not mask.all():
            # 极少数对不上时，做一下过滤
            df = df[mask].reset_index(drop=True)
            want = want[mask]
        X_sel_all = X_all[want]  # 组合内的样本
        # 列对齐（缺列补0，多列丢弃）
        pred_index = pd.Index(pred_cols)
        pos = pred_index.get_indexer(cols)
        exists = pos >= 0
        X_sel = X_sel_all[:, pos[exists]]
        miss_cnt = (~exists).sum()
        if miss_cnt > 0:
            X_sel = sparse.hstack([X_sel, sparse.csr_matrix((X_sel.shape[0], int(miss_cnt)))], format="csr")

        # 计算 SHAP
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X_sel, check_additivity=False)  # shape=(N, n_feats)

        payload = []
        for i, sc in enumerate(df["stock_code"]):
            vals = sv[i]
            # 注意：sv列与“对齐后列”的顺序一致（即 cols 中“存在的列”+补零列）
            # 我们只取存在列的前K贡献
            real_cols = np.array(cols + ["__missing__"]*miss_cnt)  # 对齐名称
            topk = np.argsort(-np.abs(vals))[:SAVE_SHAP_TOPK]
            for r, j in enumerate(topk, 1):
                if real_cols[j] == "__missing__":  # 补零列不写库
                    continue
                payload.append({
                    "trade_date": trade_date,
                    "model_id": int(model_id),
                    "stock_code": sc,
                    "feat_name": real_cols[j],
                    "shap_value": float(vals[j]),
                    "rank_in_stock": int(r)
                })
        if payload:
            with ENGINE.begin() as conn:
                conn.execute(text("""
                  INSERT INTO t_lgbm_explain_topk(trade_date, model_id, stock_code, feat_name, shap_value, rank_in_stock)
                  VALUES (:trade_date,:model_id,:stock_code,:feat_name,:shap_value,:rank_in_stock)
                  ON DUPLICATE KEY UPDATE shap_value=VALUES(shap_value),
                                          rank_in_stock=VALUES(rank_in_stock), updated_at=NOW()
                """), payload)
            print(f"SHAP top-{SAVE_SHAP_TOPK} saved for {len(df)} stocks")

if __name__ == '__main__':
    # 例：对 2025-08-12、模型ID=123 的分数生成组合 & 解释，并导出CSV
    build_portfolio("2025-08-12", 123)