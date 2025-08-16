/* 1) 组合权重表：可被外部软件直接消费 */
CREATE TABLE IF NOT EXISTS t_portfolio_lgbm (
  trade_date DATE        NOT NULL COMMENT '组合生效日',
  model_id   BIGINT      NOT NULL COMMENT 'LGBM模型ID',
  stock_code VARCHAR(16) NOT NULL COMMENT '股票代码',
  rank_no    INT         NOT NULL COMMENT '当日排名(1=最高分)',
  score      DOUBLE      NOT NULL COMMENT 'LGBM分数/概率',
  weight     DOUBLE      NOT NULL COMMENT '组合权重(0-1，总和=1)',
  created_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (trade_date, model_id, stock_code),
  KEY idx_dt_model (trade_date, model_id),
  KEY idx_dt_rank  (trade_date, rank_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LGBM 组合成分与权重';

/* 2) 局部可解释表（可选）：仅存Top-K特征贡献，方便溯源 */
CREATE TABLE IF NOT EXISTS t_lgbm_explain_topk (
  trade_date  DATE        NOT NULL,
  model_id    BIGINT      NOT NULL,
  stock_code  VARCHAR(16) NOT NULL,
  feat_name   VARCHAR(128) NOT NULL,
  shap_value  DOUBLE      NOT NULL COMMENT '该特征对分数的贡献(可正可负)',
  rank_in_stock INT       NOT NULL COMMENT '在该股票中的解释排名(1=最大贡献)',
  created_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (trade_date, model_id, stock_code, feat_name),
  KEY idx_dt_model_stock (trade_date, model_id, stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LGBM 局部解释 Top-K';
