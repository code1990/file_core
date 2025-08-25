CREATE TABLE IF NOT EXISTS t_stock_calendar (
  trade_date INTEGER NOT NULL PRIMARY KEY,
  is_open    INTEGER NOT NULL
);

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;   -- 或 OFF，更快但有风险
PRAGMA locking_mode = NORMAL;  -- 默认值，多个连接可同时访问

CREATE TABLE IF NOT EXISTS t_stock_signal (
  trade_date    INTEGER    NOT NULL,
  stock_code    TEXT       NOT NULL,
  signal_name   TEXT       NOT NULL,
  signal_value  REAL       NOT NULL DEFAULT 1,
  PRIMARY KEY (trade_date, stock_code, signal_name)
);

-- 索引（单独建）
CREATE INDEX IF NOT EXISTS idx_dt ON t_stock_signal(trade_date);
CREATE INDEX IF NOT EXISTS idx_dt_code ON t_stock_signal(trade_date, stock_code);
CREATE INDEX IF NOT EXISTS idx_xg ON t_stock_signal(signal_name);

CREATE TABLE IF NOT EXISTS t_stock_stat (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- 主键ID
  idx INTEGER,                            -- 时间序列
  stock_code TEXT,                        -- 股票代码
  trade_date INTEGER,                     -- 交易日
  high REAL,                              -- 最高价
  close REAL,                             -- 收盘价
  percent REAL,                           -- 收盘涨幅
  v_0_percent REAL,                       -- 第0天最高涨幅
  v_1_percent REAL,                       -- 第1天最高涨幅
  v_2_percent REAL,                       -- 第2天最高涨幅
  v_3_percent REAL,                       -- 第3天最高涨幅
  v_5_percent REAL,                       -- 第5天最高涨幅
  v_10_percent REAL,                      -- 第10天最高涨幅
  is_zdt INTEGER,                         -- 是否涨跌停
  is_high INTEGER                         -- 是否高买低买
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_stock_stat_code_date
  ON t_stock_stat(stock_code, trade_date);


DROP VIEW IF EXISTS t_stock_label_1;

CREATE VIEW t_stock_label_1 AS
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

DROP VIEW IF EXISTS t_stock_label_2;

CREATE VIEW t_stock_label_2 AS
SELECT
  ts.trade_date,
  ts.stock_code,
  CASE
    WHEN (
       ts.v_1_percent >= 2.0
      OR ts.v_2_percent >= 2.0
      OR ts.v_3_percent >= 2.0
    ) THEN 1
    ELSE 0
  END AS label
FROM t_stock_stat ts;

DROP VIEW IF EXISTS t_stock_label_3;

CREATE VIEW t_stock_label_3 AS
SELECT
  ts.trade_date,
  ts.stock_code,
  CASE
    WHEN (
       ts.v_1_percent >= 3.0
      OR ts.v_2_percent >= 3.0
      OR ts.v_3_percent >= 3.0
    ) THEN 1
    ELSE 0
  END AS label
FROM t_stock_stat ts;


CREATE TABLE IF NOT EXISTS t_stock_change (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market INTEGER,
    trade_date INTEGER,
    change_count INTEGER,  -- 当天异动次数
    UNIQUE(stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS t_stock_change_detail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market INTEGER,
    trade_date INTEGER,
    trade_time TEXT,        -- 10:31:48 这种格式
    signal_code INTEGER,    -- 对应 position.json 中的 key，例如 128
    signal_name TEXT,       -- 对应 position.json 的中文，如 有大卖盘
    price REAL,             -- 股价
    change_percent REAL,    -- 涨跌幅（u）
    extra_info TEXT,        -- 原始字段 i (备用)
    volume INTEGER,         -- 成交量（可解析自 i）
    amount REAL,            -- 成交额（可解析自 i）
    UNIQUE(stock_code, trade_date, trade_time, signal_code)
);

CREATE TABLE IF NOT EXISTS t_stock_quote (
    stock_code TEXT PRIMARY KEY,   -- 股票代码，如 300004.SZ
    iopv REAL,
    current_amount REAL,
    last_px REAL,
    vol_ratio REAL,
    dyn_pb_rate REAL,
    amplitude REAL,
    min5_chgpct REAL,
    wavg_px REAL,
    prod_name TEXT,
    shares_per_hand REAL,
    debt_fund_value REAL,
    market_value REAL,
    bps REAL,
    amount REAL,
    turnover_ratio REAL,
    entrust_rate REAL,
    entrust_diff REAL,
    circulation_amount REAL,
    circulation_value REAL,
    eps REAL,
    prev_amount REAL,
    preclose_px REAL,
    business_last_closedate TEXT,
    market_date TEXT,
    updown_days REAL,
    px_change_rate_5days REAL,
    px_change_rate_10days REAL,
    px_change_rate_20days REAL,
    px_change_rate_60days REAL,
    min1_chgpct REAL,
    min3_chgpct REAL,
    turnover_1mins REAL,
    turnover_3mins REAL,
    turnover_5mins REAL,
    mrq_pb_rate REAL,
    high_px REAL,
    low_px REAL,
    business_amount REAL,
    premium_rate REAL,
    business_count REAL,
    business_balance REAL,
    open_px REAL,
    bid_grp TEXT,
    offer_grp TEXT,
    trade_status TEXT,
    data_timestamp REAL,
    close_px REAL,
    up_px REAL,
    down_px REAL,
    business_amount_in REAL,
    business_amount_out REAL,
    w52_low_px REAL,
    w52_high_px REAL,
    px_change REAL,
    px_change_rate REAL,
    trade_mins REAL,
    total_shares REAL,
    pe_rate REAL,
    special_marker REAL,
    business_balance_scale REAL,
    addi_tradetime_bits REAL,
    fund_discount_value REAL,
    open_flag REAL,
    prod_name_ext TEXT,
    business_amount_am REAL,
    business_balance_am REAL,
    ttm_pe_rate REAL,
    static_pe_rate REAL,
    eps_ttm REAL,
    eps_year REAL,
    osov_rate REAL,
    year_pxchange_rate REAL
);

-- 删除 t_stock_signal
select count(*) FROM t_stock_signal
WHERE stock_code NOT IN (
    SELECT substr(stock_code, 1, length(stock_code)-3)
    FROM t_stock_quote
    WHERE stock_code LIKE '%.SZ' OR stock_code LIKE '%.SH'
);

-- 删除 t_stock_stat
select count(*) FROM t_stock_stat
WHERE stock_code NOT IN (
    SELECT substr(stock_code, 1, length(stock_code)-3)
    FROM t_stock_quote
    WHERE stock_code LIKE '%.SZ' OR stock_code LIKE '%.SH'
);

DROP VIEW IF EXISTS t_stock_signal_2;
CREATE VIEW t_stock_signal_2 AS
SELECT
    a.trade_date,
    a.stock_code,
    a.signal_name || '&' || b.signal_name AS combo_name,
    MIN(a.signal_value, b.signal_value) AS combo_value
FROM t_stock_signal a
JOIN t_stock_signal b
  ON a.trade_date = b.trade_date
 AND a.stock_code = b.stock_code
 AND a.signal_name < b.signal_name    -- 避免重复 & 自连
GROUP BY a.trade_date, a.stock_code, combo_name;


DROP VIEW IF EXISTS t_stock_signal_3;
CREATE VIEW t_stock_signal_3 AS
SELECT
    a.trade_date,
    a.stock_code,
    a.signal_name || '&' || b.signal_name || '&' || c.signal_name AS combo_name,
    MIN(MIN(a.signal_value, b.signal_value), c.signal_value) AS combo_value
FROM t_stock_signal a
JOIN t_stock_signal b
  ON a.trade_date = b.trade_date
 AND a.stock_code = b.stock_code
 AND a.signal_name < b.signal_name
JOIN t_stock_signal c
  ON a.trade_date = c.trade_date
 AND a.stock_code = c.stock_code
 AND b.signal_name < c.signal_name
GROUP BY a.trade_date, a.stock_code, combo_name;

CREATE TABLE IF NOT EXISTS t_formula (
    id INTEGER PRIMARY KEY,              -- 唯一ID
    name TEXT,                           -- 公式名称
    source_code TEXT,                    -- 公式源码
    label_name TEXT,                     -- 标签名称
    uploader_name TEXT,                  -- 上传者
    upload_time TEXT,                    -- 上传时间
    instruction TEXT,                    -- 说明/简介
    hot_val INTEGER,                     -- 热度
    click_times INTEGER,                 -- 点击次数
    discuss_number INTEGER,              -- 评论数
    avg_star REAL,                       -- 平均评分
    market_list TEXT,                    -- 支持的市场（JSON）
    extra_json TEXT                      -- 其他原始字段（JSON存储）
);
