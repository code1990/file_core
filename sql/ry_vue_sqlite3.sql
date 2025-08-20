DROP TABLE IF EXISTS t_stock_daily;

CREATE TABLE t_stock_daily (
  id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
  stock_code TEXT,                      -- 股票代码
  stock_name TEXT,                      -- 股票名称
  trade_date INTEGER,                   -- 交易日
  open REAL,                            -- 开盘价
  high REAL,                            -- 最高价
  low REAL,                             -- 最低价
  close REAL,                           -- 收盘价
  vol REAL,                             -- 成交量
  amount REAL,                          -- 成交额
  vol_rate REAL,                        -- 换手率
  percent REAL,                         -- 涨跌幅
  changes REAL,                         -- 涨跌额
  pre_close REAL,                       -- 昨日收盘价
  remark TEXT DEFAULT ''                -- 备注
);

-- 索引
CREATE UNIQUE INDEX uniq_stock_trade ON t_stock_daily(stock_code, trade_date);
CREATE INDEX t_stock_daily_idx ON t_stock_daily(stock_code, trade_date);
