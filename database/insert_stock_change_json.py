from bs4 import BeautifulSoup
import json

html = """<div class="change_select self_clearfix"><div class="up"><div class="up_all active">全选中</div><ul><li><label><input type="checkbox" autocomplete="off" checked="" value="8201"><span class="price_up">火箭发射</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8202"><span class="price_up">快速反弹</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8193"><span class="price_up">大笔买入</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="4"><span class="price_up">封涨停板</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="32"><span class="price_up">打开跌停板</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="64"><span class="price_up">有大买盘</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8207"><span class="price_up">竞价上涨</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8209"><span class="price_up">高开5日线</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8211"><span class="price_up">向上缺口</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8213"><span class="price_up">60日新高</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8215"><span class="price_up">60日大幅上涨</span></label></li></ul></div><div class="down"><div class="down_all ">全取消</div><ul><li><label><input type="checkbox" autocomplete="off" checked="" value="8204"><span class="price_down">加速下跌</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8203"><span class="price_down">高台跳水</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8194"><span class="price_down">大笔卖出</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8"><span class="price_down">封跌停板</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="16"><span class="price_down">打开涨停板</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="128"><span class="price_down">有大卖盘</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8208"><span class="price_down">竞价下跌</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8210"><span class="price_down">低开5日线</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8212"><span class="price_down">向下缺口</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8214"><span class="price_down">60日新低</span></label></li><li><label><input type="checkbox" autocomplete="off" checked="" value="8216"><span class="price_down">60日大幅下跌</span></label></li></ul></div></div>"""

soup = BeautifulSoup(html, "html.parser")

def parse_signals(container_class, trend_type):
    signals = {}
    # 找 class=up / down 下的所有 label
    container = soup.find("div", {"class": container_class})
    if not container:
        return signals
    for label in container.find_all("label"):
        input_tag = label.find("input")
        span_tag = label.find("span")
        if input_tag and span_tag:
            signals[input_tag["value"]] = {
                "name": span_tag.get_text(strip=True),
                "trend": trend_type
            }
    return signals

# 上涨信号
up_signals = parse_signals("up", "up")
# 下跌信号
down_signals = parse_signals("down", "down")

# 合并
signal_dict = {**up_signals, **down_signals}
print(signal_dict)
print("共提取信号：", len(signal_dict))
print(signal_dict.get("8201", "8201 不存在映射表中"))

# 保存到 JSON 文件
output_path = "./data/postition.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(signal_dict, f, ensure_ascii=False, indent=2)

print(f"✅ 保存完成: {output_path}")