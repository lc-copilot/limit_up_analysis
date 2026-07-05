#!/usr/bin/env python3
"""
涨停/跌停数据获取脚本（独立版，复制出去直接运行）。

数据源：同花顺 data.10jqka.com.cn 公开 API，无需 token、无需登录。

用法：
    python fetch_limit_data.py 2026-06-26
    python fetch_limit_data.py 2026-06-26 -o 涨停板.json
    python fetch_limit_data.py               # 默认取最近交易日

依赖：仅 Python 标准库（Python 3.9+），无需 pip install。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

# ── 常量 ─────────────────────────────────────────────────

# 涨停板池 API
LIMIT_UP_URL = (
    "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
    "?page={page}&limit={limit}"
    "&field=199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004"
    "&filter=HS,GEM2STAR"
    "&order_field=330324&order_type=0"
    "&date={date}&_={ts}"
)

# 跌停板池 API
LIMIT_DOWN_URL = (
    "https://data.10jqka.com.cn/dataapi/limit_up/lower_limit_pool"
    "?page={page}&limit={limit}"
    "&field=199112,10,330333,330334,1968584,3475914,9004"
    "&filter=HS,GEM2STAR"
    "&order_field=330334&order_type=0"
    "&date={date}&_={ts}"
)

PAGE_SIZE = 100
TIMEOUT = 15

# 板块分组 API（涨停简图 → 概念归属）
BOARD_GROUP_URL = (
    "https://ozone.10jqka.com.cn/open/api/draw_lots/v1/rank/board_group_data"
    "?date={date}"
)

# 板块涨停排行 API（涨停原因详情）
BLOCK_TOP_URL = (
    "https://data.10jqka.com.cn/dataapi/limit_up/block_top"
    "?filter=HS%2CGEM2STAR&date={date}"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── 字段说明（与参考文件一致） ───────────────────────────

FIELDS_DOC: dict[str, str] = {
    "limit_up": "涨停板数据",
    "limit_up.count": "涨停数量",
    "limit_up.items": "涨停股票列表",
    "limit_up.items[].symbol": "股票代码",
    "limit_up.items[].name": "股票名称",
    "limit_up.items[].open_num": "开板次数",
    "limit_up.items[].first_limit_up_time": "首次涨停时间",
    "limit_up.items[].last_limit_up_time": "最后涨停时间",
    "limit_up.items[].limit_up_type": "涨停类型",
    "limit_up.items[].order_volume": "封单量",
    "limit_up.items[].is_new": "是否新股",
    "limit_up.items[].limit_up_suc_rate": "涨停成功率",
    "limit_up.items[].currency_value": "流通市值",
    "limit_up.items[].market_id": "市场ID",
    "limit_up.items[].is_again_limit": "是否回封",
    "limit_up.items[].change_rate": "涨跌幅",
    "limit_up.items[].turnover_rate": "换手率",
    "limit_up.items[].reason_type": "涨停原因类型",
    "limit_up.items[].order_amount": "封单金额",
    "limit_up.items[].high_days": "连板描述(如 首板/3天2板)",
    "limit_up.items[].high_days_value": "连板编码(非真实天数)",
    "limit_up.items[].change_tag": "价格变动标签",
    "limit_up.items[].market_type": "市场类型",
    "limit_up.items[].latest": "最新报价快照",
    "limit_down": "跌停板数据(来自 lower_limit_pool API)",
    "limit_down.count": "跌停数量",
    "limit_down.items": "跌停股票列表",
    "limit_down.items[].symbol": "股票代码",
    "limit_down.items[].name": "股票名称",
    "limit_down.items[].first_seal_time": "首次封跌时间(HH:MM:SS，可为null)",
    "limit_down.items[].open_num": "炸板次数(跌停被打开次数)",
    "limit_down.items[].order_amount": "封单金额(元)",
    "limit_down.items[].turnover_rate": "换手率(%)",
    "limit_down.items[].change_rate": "跌幅(%，负值)",
    "limit_down.market": "跌停市场汇总",
    "limit_down.market.limit_down_num": "今日跌停数",
    "limit_down.market.limit_down_history_num": "昨日跌停数",
    "limit_down.market.limit_down_open_num": "今日炸跌板数",
    "limit_down.error": "API 错误信息(null=正常)",
    "limit_up.items[].concept": "所属概念(来自涨停简图)",
    "limit_up.items[].reason_info": "涨停原因详情(来自板块排行)",
}


# ── 工具函数 ─────────────────────────────────────────────

def code_to_symbol(code: str) -> str:
    """6 位数字代码 → 'CCCCCC.SH' / 'CCCCCC.SZ' / 'CCCCCC.BJ'。"""
    code = str(code).strip()
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return code  # fallback


def symbol_to_code(symbol: str) -> str:
    """'CCCCCC.SH' \u2192 'CCCCCC'\uff086 \u4f4d\u6570\u5b57\u4ee3\u7801\uff09"""
    return symbol.split(".")[0]


def parse_high_days(s: str | None) -> int:
    """解析 THS high_days 字段 → 连板数。

    '首板' → 1, 'X天Y板' → Y, 纯数字 → int, 其他 → 1（默认首板）。
    """
    if not s:
        return 1
    s = str(s).strip()
    if s == "首板":
        return 1
    # '3天2板' → 取 Y（板数）
    if "天" in s and "板" in s:
        try:
            return int(s.split("天")[1].split("板")[0])
        except (ValueError, IndexError):
            pass
    # 纯数字
    try:
        return int(s)
    except ValueError:
        pass
    return 1  # 默认首板


def ts_to_hms(ts) -> str | None:
    """unix 秒 → 'HH:MM:SS' 字符串。"""
    if ts is None:
        return None
    try:
        ts_int = int(ts)
        if ts_int <= 0:
            return None
        return datetime.fromtimestamp(ts_int).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def to_float(v) -> float | None:
    """安全转 float。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_int(v) -> int | None:
    """安全转 int。"""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ── HTTP 请求 ────────────────────────────────────────────

def _fetch_page(url_template: str, trade_date: str, page: int) -> dict:
    """请求单页数据，返回 JSON 的 data 字段。"""
    url = url_template.format(
        page=page,
        limit=PAGE_SIZE,
        date=trade_date.replace("-", ""),
        ts=int(time.time() * 1000),
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://data.10jqka.com.cn/",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"连接失败: {e.reason}")
    except TimeoutError:
        raise RuntimeError(f"请求超时 ({TIMEOUT}s)")

    return body.get("data") or {}


def fetch_all_pages(url_template: str, trade_date: str) -> dict:
    """分页拉取全部数据，返回合并后的 data 字典。"""
    first = _fetch_page(url_template, trade_date, page=1)
    info = list(first.get("info") or [])
    total = (first.get("page") or {}).get("total") or 0

    page = 2
    while len(info) < total:
        nxt = _fetch_page(url_template, trade_date, page=page)
        rows = nxt.get("info") or []
        if not rows:
            break
        info.extend(rows)
        page += 1

    first["info"] = info
    return first


# ── 数据转换 ─────────────────────────────────────────────

def _fetch_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """\u8bf7\u6c42\u5355\u6b21 JSON \u63a5\u53e3\uff0c\u8fd4\u56de\u5b8c\u6574 body\uff08\u4e0d\u505a .get('data') \u63d0\u53d6\uff09"""
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"\u8fde\u63a5\u5931\u8d25: {e.reason}")
    except TimeoutError:
        raise RuntimeError(f"\u8bf7\u6c42\u8d85\u65f6 ({TIMEOUT}s)")


# \u2500\u2500 \u65b0\u589e API\uff1a\u677f\u5757\u5206\u7ec4 & \u677f\u5757\u6392\u884c \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def fetch_board_group_data(trade_date: str) -> dict[str, str]:
    """\u83b7\u53d6\u6da8\u505c\u7b80\u56fe\u677f\u5757\u5206\u7ec4\u6570\u636e \u2192 {6\u4f4d\u4ee3\u7801: \u6982\u5ff5\u540d\u79f0}"""
    url = BOARD_GROUP_URL.format(date=trade_date.replace("-", ""))
    headers = {
        "User-Agent": UA,
        "Origin": "https://data.10jqka.com.cn",
        "x-requested-with": "com.hexin.plat.android",
        "Referer": "https://data.10jqka.com.cn/",
    }
    body = _fetch_json(url, headers=headers)
    if body.get("status_code") != 0:
        print(f"  \u26a0 board_group_data API \u8fd4\u56de\u5f02\u5e38: {body.get('status_msg')}", file=sys.stderr)
        return {}

    code_to_concept: dict[str, str] = {}
    tab_list = (body.get("data") or {}).get("tab_list") or []
    for tab in tab_list:
        concept_name = tab.get("tab_name", "")
        board_list = tab.get("board_list") or []
        for board in board_list:
            tab_data = board.get("tab_data") or []
            for item in tab_data:
                stock_code = str(item.get("stock_code", "")).strip()
                if stock_code:
                    if stock_code in code_to_concept:
                        existing = code_to_concept[stock_code]
                        if concept_name not in existing:
                            code_to_concept[stock_code] = f"{existing},{concept_name}"
                    else:
                        code_to_concept[stock_code] = concept_name
    return code_to_concept


def fetch_block_top_data(trade_date: str) -> dict[str, str]:
    """\u83b7\u53d6\u677f\u5757\u6da8\u505c\u6392\u884c\u6570\u636e \u2192 {6\u4f4d\u4ee3\u7801: reason_info}"""
    url = BLOCK_TOP_URL.format(date=trade_date.replace("-", ""))
    headers = {
        "User-Agent": UA,
        "Referer": "https://data.10jqka.com.cn/mobile/limitup/v2/index.html",
        "x-requested-with": "com.hexin.plat.android",
    }
    body = _fetch_json(url, headers=headers)
    if body.get("status_code") != 0:
        print(f"  \u26a0 block_top API \u8fd4\u56de\u5f02\u5e38: {body.get('status_msg')}", file=sys.stderr)
        return {}

    code_to_reason: dict[str, str] = {}
    concepts = body.get("data") or []
    for concept in concepts:
        stock_list = concept.get("stock_list") or []
        for item in stock_list:
            stock_code = str(item.get("code", "")).strip()
            reason_info = item.get("reason_info")
            if stock_code and reason_info:
                if stock_code not in code_to_reason:
                    code_to_reason[stock_code] = reason_info
    return code_to_reason


# \u2500\u2500 \u6570\u636e\u8f6c\u6362 \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def transform_limit_up(
    raw: dict,
    code_to_concept: dict[str, str] | None = None,
    code_to_reason: dict[str, str] | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """\u8f6c\u6362\u6da8\u505c\u677f\u539f\u59cb\u6570\u636e \u2192 (items, streak_distribution)\u3002

    \u53ef\u9009\u6ce8\u5165 concept \u548c reason_info \u5b57\u6bb5\u3002
    """
    items: list[dict] = []
    streak_counter: dict[int, int] = {}

    for it in raw.get("info") or []:
        code = it.get("code", "")
        if not code or len(str(code)) != 6:
            continue

        high_days_raw = it.get("high_days")
        consecutive_boards = parse_high_days(high_days_raw)

        code_str = str(code)
        concept = (code_to_concept or {}).get(code_str)
        reason_info = (code_to_reason or {}).get(code_str)

        item = {
            "symbol":              code_to_symbol(code_str),
            "name":                it.get("name"),
            "first_limit_up_time": to_int(it.get("first_limit_up_time")),
            "last_limit_up_time":  to_int(it.get("last_limit_up_time")),
            "order_amount":        to_float(it.get("order_amount")),
            "currency_value":      to_float(it.get("currency_value")),
            "turnover_rate":       to_float(it.get("turnover_rate")),
            "change_rate":         to_float(it.get("change_rate")),
            "reason_type":         it.get("reason_type"),
            "open_num":            int(it.get("open_num") or 0),
            "is_again_limit":      int(bool(it.get("is_again_limit"))),
            "consecutive_boards":  consecutive_boards,
            "concept":             concept,
            "reason_info":         reason_info,
        }
        items.append(item)
        streak_counter[consecutive_boards] = streak_counter.get(consecutive_boards, 0) + 1

    # 按连板数降序排列
    streak_distribution = {
        str(k): v
        for k, v in sorted(streak_counter.items(), key=lambda x: -x[0])
    }
    # 按首次涨停时间升序（与参考文件一致），无时间的排最后
    items.sort(key=lambda x: (x["first_limit_up_time"] is None, x["first_limit_up_time"] or 0, x["symbol"]))

    return items, streak_distribution


def transform_limit_down(raw: dict) -> list[dict]:
    """转换跌停板原始数据 → items。"""
    items: list[dict] = []
    for it in raw.get("info") or []:
        code = it.get("code", "")
        if not code or len(str(code)) != 6:
            continue

        first_limit_down = it.get("first_limit_down_time")
        # first_limit_down_time 可能是 unix 秒（字符串或数字）
        first_seal = ts_to_hms(first_limit_down)

        items.append({
            "symbol":          code_to_symbol(str(code)),
            "name":            it.get("name"),
            "first_seal_time": first_seal,
            "open_num":        int(it.get("open_num") or 0),
            "order_amount":    to_float(it.get("order_amount") or it.get("currency_value")),
            "turnover_rate":   to_float(it.get("turnover_rate")),
            "change_rate":     to_float(it.get("change_rate")),
        })
    return items


def build_market(raw: dict) -> dict:
    """从原始 API 返回中提取 market 汇总。"""
    lu = (raw.get("limit_up_count") or {}).get("today") or {}
    ld = (raw.get("limit_down_count") or {}).get("today") or {}

    def _i(d, key):
        try:
            return int(d[key]) if d.get(key) is not None else None
        except (TypeError, ValueError):
            return None

    lu_num = _i(lu, "num")
    lu_hist = _i(lu, "history_num")
    lu_open = _i(lu, "open_num")
    ld_num = _i(ld, "num")
    ld_hist = _i(ld, "history_num")
    ld_open = _i(ld, "open_num")

    return {
        "limit_up_count": {
            "today": {
                "num": lu_num,
                "history_num": lu_hist,
                "open_num": lu_open,
                "rate": round(lu_num / lu_hist, 4) if lu_hist else None,
            }
        },
        "limit_down_count": {
            "today": {
                "num": ld_num,
                "history_num": ld_hist,
                "open_num": ld_open,
                "rate": round(ld_num / ld_hist, 4) if ld_hist else None,
            }
        },
    }


# ── 主流程 ───────────────────────────────────────────────

def fetch_all(target_date: str) -> dict:
    """获取指定日期的涨跌停数据，返回完整 JSON 结构。"""
    print(f"→ 获取涨停板数据 (date={target_date})...", file=sys.stderr)
    up_raw = fetch_all_pages(LIMIT_UP_URL, target_date)
    # \u83b7\u53d6\u6982\u5ff5\u5f52\u5c5e\u548c\u6da8\u505c\u539f\u56e0\u8be6\u60c5
    print(f"\u2192 \u83b7\u53d6\u677f\u5757\u5206\u7ec4\u6570\u636e (date={target_date})...", file=sys.stderr)
    code_to_concept = fetch_board_group_data(target_date)
    print(f"\u2192 \u83b7\u53d6\u677f\u5757\u6da8\u505c\u6392\u884c\u6570\u636e (date={target_date})...", file=sys.stderr)
    code_to_reason = fetch_block_top_data(target_date)

    up_items, streak_dist = transform_limit_up(up_raw, code_to_concept, code_to_reason)

    print(f"→ 获取跌停板数据 (date={target_date})...", file=sys.stderr)
    down_error: str | None = None
    down_items: list[dict] = []
    down_market: dict = {}
    try:
        down_raw = fetch_all_pages(LIMIT_DOWN_URL, target_date)
        down_items = transform_limit_down(down_raw)
        # 跌停 market 汇总从涨停 API 的 limit_down_count 取
        down_market = {
            "limit_down_num": (up_raw.get("limit_down_count") or {}).get("today", {}).get("num"),
            "limit_down_history_num": (up_raw.get("limit_down_count") or {}).get("today", {}).get("history_num"),
            "limit_down_open_num": (up_raw.get("limit_down_count") or {}).get("today", {}).get("open_num"),
        }
    except Exception as e:
        down_error = str(e)
        print(f"  ⚠ 跌停板获取失败: {e}", file=sys.stderr)

    market = build_market(up_raw)

    output: dict[str, Any] = {
        "_fields": FIELDS_DOC,
        "date": target_date,
        "market": market,
        "streak_distribution": streak_dist,
        "source": "10jqka",
        "limit_up": {
            "items": up_items,
            "count": len(up_items),
        },
        "limit_down": {
            "error": down_error,
            "count": len(down_items),
            "items": down_items,
            "market": down_market,
        },
    }

    print(f"  涨停 {len(up_items)} 家，跌停 {len(down_items)} 家", file=sys.stderr)
    return output


# ── 命令行入口 ────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    target_date = date.today().isoformat()
    output_path: str | None = None

    i = 0
    while i < len(args):
        if args[i] in ("--output", "-o") and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        elif args[i] in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        elif not args[i].startswith("-"):
            target_date = args[i]
            i += 1
        else:
            print(f"未知参数: {args[i]}", file=sys.stderr)
            print(__doc__)
            sys.exit(1)

    # 校验日期格式
    try:
        date.fromisoformat(target_date)
    except ValueError:
        print(f"错误: 日期格式无效 '{target_date}'，应为 YYYY-MM-DD", file=sys.stderr)
        sys.exit(1)

    try:
        data = fetch_all(target_date)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    # 默认保存到 limit_up/{date}_涨停板.json
    if not output_path:
        output_path = f"limit_up/{target_date}_涨停板.json"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json_str, encoding="utf-8")
    print(f"✓ 已保存到: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()