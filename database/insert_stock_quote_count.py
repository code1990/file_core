import time
import requests

def get_stock_total_count():
    url = "https://quotedata.cnfin.com/quote/v1/sort"
    params = {
        "sort_field_name": "px_change_rate",
        "sort_type": "undefined",
        "start_pos": 0,
        "data_count": 15,
        "en_hq_type_code": "SS.ESA.M,SZ.ESA.M,SZ.ESA.SMSE,SZ.ESA.GEM,SS.KSH,SZ.ESA.SMSE",
        "fields": "null",
        "request_sort_count": 1,
        "localDate": str(int(time.time() * 1000))  # 当前毫秒时间戳
    }

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": "auth_id:null;user_token:null;app_key:null",
        "Origin": "https://www.cnfin.com",
        "Referer": "https://www.cnfin.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0"
    }

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data["data"]["sort"]["sort_result_count"]
    except Exception as e:
        print("获取失败:", e)
        return None



if __name__ == "__main__":
    total = get_stock_total_count()
    print("股票总数:", total)
