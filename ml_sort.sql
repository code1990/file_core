/* =========================================================
 * 1) 候选个股与特征（训练/预测统一来源）
 *    - 每行 = 某交易日、某只股票的 30 个排序分数 + 次日收益
 *    - next_ret 可为 NULL（表示还未发生的次日，用于在线预测）
 *    - PK 覆盖 trade_date + stock_code，便于幂等
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_candidate_stocks (
  trade_date  DATE         NOT NULL COMMENT '交易日（信号日）',
  stock_code  VARCHAR(16)  NOT NULL COMMENT '股票代码',
  score_1     DOUBLE       NULL,
  score_2     DOUBLE       NULL,
  score_3     DOUBLE       NULL,
  score_4     DOUBLE       NULL,
  score_5     DOUBLE       NULL,
  score_6     DOUBLE       NULL,
  score_7     DOUBLE       NULL,
  score_8     DOUBLE       NULL,
  score_9     DOUBLE       NULL,
  score_10    DOUBLE       NULL,
  score_11    DOUBLE       NULL,
  score_12    DOUBLE       NULL,
  score_13    DOUBLE       NULL,
  score_14    DOUBLE       NULL,
  score_15    DOUBLE       NULL,
  score_16    DOUBLE       NULL,
  score_17    DOUBLE       NULL,
  score_18    DOUBLE       NULL,
  score_19    DOUBLE       NULL,
  score_20    DOUBLE       NULL,
  score_21    DOUBLE       NULL,
  score_22    DOUBLE       NULL,
  score_23    DOUBLE       NULL,
  score_24    DOUBLE       NULL,
  score_25    DOUBLE       NULL,
  score_26    DOUBLE       NULL,
  score_27    DOUBLE       NULL,
  score_28    DOUBLE       NULL,
  score_29    DOUBLE       NULL,
  score_30    DOUBLE       NULL,
  next_ret    DOUBLE       NULL COMMENT '次日收益（训练期用，预测期可为NULL）',
  PRIMARY KEY (trade_date, stock_code),
  KEY idx_dt (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='候选个股+30个排序分数字段+次日收益';

/* =========================================================
 * 2) 标签视图：是否达标（次日收益 >= 1%）
 *    - 训练集用；预测集 next_ret 为 NULL 不会出现在此视图
 * ========================================================= */
CREATE OR REPLACE VIEW v_candidate_with_label AS
SELECT
  trade_date,
  stock_code,
  score_1,score_2,score_3,score_4,score_5,score_6,score_7,score_8,score_9,score_10,
  score_11,score_12,score_13,score_14,score_15,score_16,score_17,score_18,score_19,score_20,
  score_21,score_22,score_23,score_24,score_25,score_26,score_27,score_28,score_29,score_30,
  next_ret,
  CASE WHEN next_ret >= 0.01 THEN 1 ELSE 0 END AS label
FROM t_candidate_stocks
WHERE next_ret IS NOT NULL;

/* =========================================================
 * 3) 模型元数据（可追溯）
 *    - 保存训练窗口、参数、指标、模型文件路径
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_ltr_model_meta (
  model_id      BIGINT       NOT NULL PRIMARY KEY AUTO_INCREMENT,
  model_version VARCHAR(64)  NOT NULL UNIQUE COMMENT '如 ltr_lgbm-2025-08-12',
  model_type    VARCHAR(32)  NOT NULL DEFAULT 'LGBMRanker',
  train_start   DATE         NOT NULL,
  train_end     DATE         NOT NULL,
  valid_start   DATE         NOT NULL,
  valid_end     DATE         NOT NULL,
  params_json   JSON         NOT NULL,
  metrics_json  JSON         NULL,
  artifact_path VARCHAR(255) NULL,
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LambdaRank模型元数据';

/* =========================================================
 * 4) 逐日逐股的预测分数（LambdaRank 输出）
 *    - score: 预测的相关性分数（用于排序，不是概率）
 *    - rank_no 可在落库时一并写入，便于直接查询 Top1
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_ltr_pred_score (
  trade_date DATE         NOT NULL,
  stock_code VARCHAR(16)  NOT NULL,
  model_id   BIGINT       NOT NULL,
  score      DOUBLE       NOT NULL,
  rank_no    INT          NOT NULL,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (trade_date, stock_code, model_id),
  KEY idx_dt_model (trade_date, model_id),
  KEY idx_dt_rank  (trade_date, rank_no),
  CONSTRAINT fk_ltr_pred_model FOREIGN KEY (model_id) REFERENCES t_ltr_model_meta(model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LambdaRank逐日评分与名次';

/* =========================================================
 * 5) 每日 Top1 拣选结果（可设置阈值，不满足则“跳过交易”）
 *    - decision: 'TRADE' or 'SKIP'
 *    - threshold_used: 本日采用的阈值（用于复盘）
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_ltr_pick_top1 (
  trade_date     DATE         NOT NULL,
  model_id       BIGINT       NOT NULL,
  stock_code     VARCHAR(16)  NULL,
  score          DOUBLE       NULL,
  rank_no        INT          NULL,
  threshold_used DOUBLE       NOT NULL,
  decision       ENUM('TRADE','SKIP') NOT NULL,
  created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (trade_date, model_id),
  KEY idx_dt (trade_date),
  CONSTRAINT fk_ltr_pick_model FOREIGN KEY (model_id) REFERENCES t_ltr_model_meta(model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LambdaRank每日Top1拣选（含跳过逻辑）';
