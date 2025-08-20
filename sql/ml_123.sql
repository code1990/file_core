/* =========================================================
 * 交易日历：is_open=1 表示开市
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_stock_calendar (
  trade_date INT        NOT NULL PRIMARY KEY COMMENT '交易日',
  is_open    TINYINT(1)  NOT NULL COMMENT '是否开市(1/0)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易日历';

/* =========================================================
 * 信号事件流（长表）
 * 含义：某日-某股-某 xg_id 触发了一次信号（未记录即0）
 * val：可存1(触发)或强度分数
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_stock_signal (
  trade_date  int         NOT NULL COMMENT '交易日',
  stock_code  VARCHAR(16)  NOT NULL COMMENT '股票代码',
  signal_name       VARCHAR(100)          NOT NULL COMMENT '信号/因子/选股器',
  signal_value         DOUBLE       NOT NULL DEFAULT 1 COMMENT '信号值(1=触发; 也可为强度)',
  PRIMARY KEY (trade_date, stock_code, signal_name),
  KEY idx_dt (trade_date),
  KEY idx_dt_code (trade_date, stock_code),
  KEY idx_xg (signal_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='选股/因子信号事件(长表)';
/* =========================================================
 * 前瞻收益（监督标签数据源）
 * horizon: v1/v2/v3 等持有期标识
 * ret_high: 对应持有期内“最高收益”（小数，0.01=1%）
 * 注：可由你的行情K线ETL计算后写入
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_forward_return (
  trade_date  DATE         NOT NULL COMMENT '信号发生日(回测起点)',
  stock_code  VARCHAR(16)  NOT NULL COMMENT '股票代码',
  horizon     ENUM('v1','v2','v3') NOT NULL COMMENT '持有期标识',
  ret_high    DOUBLE       NOT NULL COMMENT '该持有期内最高收益(0.01=1%)',
  PRIMARY KEY (trade_date, stock_code, horizon),
  KEY idx_dt_code (trade_date, stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='基于未来价格计算的前瞻收益';

/* =========================================================
 * 样本标签视图（按“日-股”聚合）
 * 定义：若 v1/v2/v3 任一 ret_high >=1%，则 label=1，否则0
 * ========================================================= */
CREATE OR REPLACE VIEW vw_sample_label AS
SELECT
  ts.trade_date,
  ts.stock_code,
  CASE
    WHEN (
      ts.v_1_percent >= 1.0
      OR ts.v_2_percent >= 1.0
      OR ts.v_3_percent >= 1.0
    ) THEN 1
    ELSE 0
  END AS label
FROM t_stock_stat ts;

/* =========================================================
 * 模型元数据（用于可复现与回溯）
 * - 保存训练/验证窗口、特征列、超参数、评测指标、模型文件路径
 * - 训练完成后插入一条，返回自增 model_id
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_model_meta (
  model_id     BIGINT       NOT NULL PRIMARY KEY AUTO_INCREMENT COMMENT '模型ID',
  model_version VARCHAR(64) NOT NULL COMMENT '模型版本(如 combo_rf-2025-08-12)',
  model_type    VARCHAR(32) NOT NULL COMMENT '模型类型(如 RandomForest)',
  train_start   DATE        NOT NULL COMMENT '训练起始日(含)',
  train_end     DATE        NOT NULL COMMENT '训练截止日(含)',
  valid_start   DATE        NOT NULL COMMENT '验证起始日(含)',
  valid_end     DATE        NOT NULL COMMENT '验证截止日(含)',
  label_rule    TEXT        NULL  COMMENT '标签规则说明',
  features_json JSON        NOT NULL COMMENT '特征列集合/编码规则',
  params_json   JSON        NOT NULL COMMENT '超参数(如n_estimators/max_depth/…)',
  metrics_json  JSON        NULL  COMMENT '评测指标(AUC/PR等)',
  artifact_path VARCHAR(255) NULL COMMENT '模型文件路径(如本地或对象存储URL)',
  created_at    TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_model_version (model_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型元数据登记';

/* =========================================================
 * 组合选股器预测结果（按“日-股”，引用 model_id）
 * - score: 该股在该日被选中的概率/评分
 * - 主键含 model_id，便于不同版本并存与对比
 * ========================================================= */
CREATE TABLE IF NOT EXISTS t_combo_pick_stock (
  trade_date DATE         NOT NULL COMMENT '预测所属交易日(通常D+1)',
  stock_code VARCHAR(16)  NOT NULL COMMENT '股票代码',
  model_id   BIGINT       NOT NULL COMMENT '引用 t_model_meta.model_id',
  score      DOUBLE       NOT NULL COMMENT '模型评分/概率',
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (trade_date, stock_code, model_id),
  KEY idx_dt_score (trade_date, score),
  KEY idx_model (model_id),
  CONSTRAINT fk_combo_model FOREIGN KEY (model_id) REFERENCES t_model_meta(model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='组合选股器评分(按日-股-模型)';
