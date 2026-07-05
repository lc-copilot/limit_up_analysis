#!/usr/bin/env python3
"""run_中间.py — 噪音过滤 + 匹配 concepts_all.json 产出 real_concepts。

工作流:
  1. 读取 limit_up/{date}_涨停板.json
  2. 噪音过滤（去掉事件型噪音标签）
  3. 匹配 concepts_all.json，为每只股票产出 real_concepts
  4. 输出 limit_up_中间/{date}_涨停概念分组_中间.json

LLM 后续读取中间 JSON，分析 real_concepts + primary_concept + concepts 三个字段，
结合 热门概念.json，得出最终的 sub_concept 和 note。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ── 路径配置 ──────────────────────────────────
BASE = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE / "limit_up"
OUTPUT_DIR = BASE / "limit_up_中间"
CONCEPTS_PATH = BASE / "concepts_all.json"

# ═══════════════════════════════════════════════
# 噪音过滤规则
# ═══════════════════════════════════════════════

_RE_DIMING_GUOZI = re.compile(r"^(?:[\u4e00-\u9fa5]{2,3})国资$")

NOISE_RULES: list[dict] = [
    {
        "category": "财务业绩",
        "patterns": [
            "一季报增长", "一季报扭亏", "半年报预增", "年报增长",
            "业绩增长", "预增", "扭亏为盈", "扭亏",
            "分红实施", "高分红", "分红派息", "权益分派", "拟实施分红",
            "转入深证100", "调入深证100",
        ],
    },
    {
        "category": "公司属性",
        "patterns": ["国企", "央企", "国企改革"],
        "regex_match": lambda t: bool(_RE_DIMING_GUOZI.match(t)),
    },
    {
        "category": "股权事件",
        "patterns": [
            "定增", "定增通过", "定增完成", "定增受理", "定增募资",
            "协议转让", "资产置换", "资产注入预期", "重组进展",
            "资产重组", "股份转让", "拟收购整合",
            "回购", "减持终止", "减持", "增持",
            "质押解除",
            "员工持股计划", "股权激励",
            "转债强赎", "注册资本减少",
            "递表港交所", "募投完成",
        ],
    },
    {
        "category": "经营杂项",
        "patterns": [
            "中标", "海外大单", "中标水利大单", "中标中国移动",
            "技改试产", "产能满产", "产能扩张",
            "品牌战略升级", "券商看好", "ASML验证",
            "追索",
            "实控人代偿应收款",
            "豪宅项目获奖",
        ],
    },
    {
        "category": "ST变动",
        "patterns": ["摘帽", "ST摘帽", "申请摘帽", "撤销风险警示"],
    },
    {
        "category": "通用词",
        "patterns": ["龙头", "行业龙头", "涨价", "订单饱满", "纯概念", "订单增长"],
    },
]


def _is_noise(tag: str) -> dict | None:
    for rule in NOISE_RULES:
        for pat in rule.get("patterns", []):
            if tag == pat or (pat.endswith("...") and tag.startswith(pat[:-3])):
                return {"category": rule["category"], "matched": pat}
            if tag.startswith(pat) and pat in ("追索",):
                return {"category": rule["category"], "matched": pat}
        regex_fn = rule.get("regex_match")
        if regex_fn and regex_fn(tag):
            return {"category": rule["category"], "matched": "regex"}
    return None


def load_reference() -> dict[str, list[dict]]:
    """加载 concepts_all.json，构建 symbol → [{sub_concept, note, name}] 映射。

    concepts_all.json 新格式:
      [{"symbol":"...", "name":"...", "memberships":[{"sub_concept":["一级","二级"], "strength":5, "note":"..."}]}]
    memberships 数组中的每个元素包含 sub_concept 层级路径。
    """
    ref_map: dict[str, list[dict]] = {}

    if not CONCEPTS_PATH.exists():
        print(f"  ⚠ 概念数据文件不存在: {CONCEPTS_PATH}")
        return ref_map

    try:
        data = json.loads(CONCEPTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠ 无法读取 concepts_all.json: {e}")
        return ref_map

    if not isinstance(data, list):
        return ref_map

    for item in data:
        sym = item.get("symbol", "")
        if not sym:
            continue
        memberships = item.get("memberships", [])
        if not memberships or not isinstance(memberships, list):
            continue

        if sym not in ref_map:
            ref_map[sym] = []
        for m in memberships:
            sc = m.get("sub_concept", [])
            if not sc or not isinstance(sc, list):
                continue
            ref_map[sym].append({
                "sub_concept": sc.copy(),
                "note": m.get("note", ""),
                "name": item.get("name", ""),
            })

    return ref_map


def _parse_tags(reason_type: str) -> list[str]:
    if not reason_type:
        return []
    return [t.strip() for t in reason_type.split("+") if t.strip()]


def process_one(filepath: Path, ref_map: dict[str, list[dict]]) -> dict | None:
    """处理单个 YYYY-MM-DD_涨停板.json，返回结果 dict。"""
    # 从文件名提取日期：2026-06-26_涨停板.json → 2026-06-26
    fname = filepath.name
    date_str = fname.replace("_涨停板.json", "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        print(f"  ⚠ 跳过（文件名不匹配日期格式）: {fname}")
        return None

    lu = json.loads(filepath.read_text(encoding="utf-8"))
    up_items = lu.get("limit_up", {}).get("items", [])
    down_items = lu.get("limit_down", {}).get("items", [])
    if not up_items and not down_items:
        print(f"  ⚠ {date_str} 无涨跌停数据")
        return None

    # ── 第1遍：解析标签 + 噪音过滤（仅涨停有 reason_type）──
    stock_info: dict[str, dict] = {}
    all_signal_tags: list[str] = []
    noise_tag_counter: Counter = Counter()
    stock_order: list[str] = []

    # ── 处理涨停股 ──
    for item in up_items:
        code = item.get("symbol", item.get("code", ""))
        if not code:
            continue
        tags = _parse_tags(item.get("reason_type", ""))

        tags_signal: list[str] = []
        tags_noise: list[dict] = []
        for t in tags:
            noise = _is_noise(t)
            if noise:
                tags_noise.append({"tag": t, **noise})
                noise_tag_counter[t] += 1
            else:
                tags_signal.append(t)

        cb = item.get("consecutive_boards")
        if cb == 1 or cb == "1":
            hd_text = "首板"
        elif cb:
            hd_text = f"{cb}板"
        else:
            hd_text = ""

        stock_info[code] = {
            "code": code,
            "name": item.get("name", ""),
            "high_days": hd_text,
            "consecutive_boards": cb,
            "first_limit_up_time": item.get("first_limit_up_time", ""),
            "turnover_rate": item.get("turnover_rate"),
            "change_rate": item.get("change_rate"),
            "currency_value": item.get("currency_value"),
            "order_amount": item.get("order_amount"),
            "reason_type": item.get("reason_type", ""),
            "tags_raw": tags,
            "tags_signal": tags_signal,
            "tags_noise": tags_noise,
            "is_noise_only": len(tags_signal) == 0 and len(tags) > 0,
        }
        all_signal_tags.extend(tags_signal)
        if code not in stock_order:
            stock_order.append(code)

    # ── 处理跌停股（无 reason_type，直接保留）──
    for item in down_items:
        code = item.get("symbol", item.get("code", ""))
        if not code:
            continue

        stock_info[code] = {
            "code": code,
            "name": item.get("name", ""),
            "high_days": "",
            "consecutive_boards": None,
            "first_limit_up_time": item.get("first_seal_time", ""),
            "turnover_rate": item.get("turnover_rate"),
            "change_rate": item.get("change_rate"),
            "currency_value": None,
            "order_amount": item.get("order_amount"),
            "reason_type": "",
            "tags_raw": [],
            "tags_signal": [],
            "tags_noise": [],
            "is_noise_only": False,
        }
        if code not in stock_order:
            stock_order.append(code)

    # ── 第2遍：基于 signal tags 构建概念分组 ──
    signal_tag_count = Counter(all_signal_tags)
    MAIN_CONCEPT_THRESHOLD = 2
    main_tags = {t for t, c in signal_tag_count.items() if c >= MAIN_CONCEPT_THRESHOLD}

    limit_up_stocks: list[dict[str, Any]] = []
    limit_down_stocks: list[dict[str, Any]] = []
    signal_concepts: dict[str, dict] = {}
    unclassified: list[dict] = []
    noise_only_stocks: list[dict] = []

    for code in stock_order:
        info = stock_info[code]
        tags_signal = info["tags_signal"]

        # ── 匹配 concepts_all.json 产出 real_concepts（所有股票都做）──
        real_concepts: list[dict] = []
        if code in ref_map:
            for entry in ref_map[code]:
                real_concepts.append({
                    "sub_concept": entry["sub_concept"],
                    "note": entry["note"],
                })

        # ── 判断涨跌停类型 ──
        is_limit_down = (not info.get("tags_raw") and not info.get("reason_type"))

        # ── 标记噪音only / 未分类（仅用于统计，不丢弃股票）──
        if info["is_noise_only"]:
            noise_only_stocks.append({
                "code": code, "name": info["name"],
                "change_rate": info["change_rate"], "type": "跌停" if is_limit_down else "涨停",
            })

        matched = [t for t in tags_signal if t in main_tags]
        if not tags_signal or not matched:
            unclassified.append({
                "code": code, "name": info["name"],
                "change_rate": info["change_rate"], "type": "跌停" if is_limit_down else "涨停", "tags": tags_signal,
            })

        primary = max(matched, key=lambda t: signal_tag_count[t]) if matched else None

        sc_entry = {
            "code": code, "name": info["name"],
            "concepts": tags_signal,
            "primary_concept": primary,
            "real_concepts": real_concepts,
            "change_rate": info["change_rate"], "type": "跌停" if is_limit_down else "涨停",
            "first_limit_up_time": info["first_limit_up_time"],
            "high_days": info["high_days"],
            "consecutive_boards": info["consecutive_boards"],
            "turnover_rate": info["turnover_rate"],
            "currency_value": info["currency_value"],
            "order_amount": info["order_amount"],
            "tags_noise": info["tags_noise"],  # 保留噪音标签信息供 LLM 参考
        }

        if is_limit_down:
            limit_down_stocks.append(sc_entry)
        else:
            limit_up_stocks.append(sc_entry)

        for tag in matched:
            if tag not in signal_concepts:
                signal_concepts[tag] = {"stock_count": 0, "limit_up_count": 0, "limit_down_count": 0, "stocks": []}
            signal_concepts[tag]["stock_count"] += 1
            if is_limit_down:
                signal_concepts[tag]["limit_down_count"] += 1
            else:
                signal_concepts[tag]["limit_up_count"] += 1
            signal_concepts[tag]["stocks"].append(code)

    # ── 噪音统计 ──────────────────────────
    noise_by_category: dict[str, dict] = {}
    for rule in NOISE_RULES:
        cat = rule["category"]
        noise_by_category[cat] = {"count": 0, "tags": {}, "股票数": 0}

    affected_codes: set[str] = set()
    for code, info in stock_info.items():
        for n in info.get("tags_noise", []):
            cat = n.get("category", "未知")
            tag = n.get("tag", "")
            if cat in noise_by_category:
                noise_by_category[cat]["count"] += 1
                noise_by_category[cat]["tags"][tag] = noise_by_category[cat]["tags"].get(tag, 0) + 1
                affected_codes.add(code)

    for cat in noise_by_category:
        noise_by_category[cat]["股票数"] = len(affected_codes)
        noise_by_category[cat]["tags"] = dict(
            sorted(noise_by_category[cat]["tags"].items(), key=lambda x: -x[1])
        )

    noise_stats = {
        "过滤标签总数": sum(noise_tag_counter.values()),
        "涉及股票数": len(affected_codes),
        "高频噪音标签": dict(noise_tag_counter.most_common(15)),
        "按类别": noise_by_category,
    }

    # ── 排序（涨停和跌停各自按时间排序）────────────────
    def _sort_key(s):
        t = s.get("first_limit_up_time")
        if t is None:
            return (1, 0, "")
        if isinstance(t, str):
            # 跌停用 HH:MM:SS 字符串
            return (0, 0, t)
        # 涨停用 unix 时间戳
        return (0, t, "")
    limit_up_stocks.sort(key=_sort_key)
    limit_down_stocks.sort(key=_sort_key)

    # ── 统计参考匹配覆盖率 ────────────────
    all_stocks = limit_up_stocks + limit_down_stocks
    ref_matched_count = sum(1 for s in all_stocks if s["real_concepts"])
    ref_unmatched_count = len(all_stocks) - ref_matched_count

    # ── 统计 ──────────────────────────────
    total_limit_up = len(up_items)
    total_limit_down = len(down_items)
    group_count = len(signal_concepts)

    large = sorted(k for k, v in signal_concepts.items() if v["stock_count"] >= 5)
    medium = sorted(k for k, v in signal_concepts.items() if 3 <= v["stock_count"] <= 4)
    small = sorted(k for k, v in signal_concepts.items() if v["stock_count"] < 3)

    strongest = {}
    if signal_concepts:
        top_name = max(signal_concepts, key=lambda k: signal_concepts[k]["stock_count"])
        tc = signal_concepts[top_name]
        avg_cr = 0.0
        cr_count = 0
        for sc in all_stocks:
            if top_name in sc.get("concepts", []) and sc.get("change_rate") is not None:
                avg_cr += sc["change_rate"]
                cr_count += 1
        avg_cr = round(avg_cr / cr_count, 2) if cr_count > 0 else 0.0
        strongest = {
            "concept": top_name, "stock_count": tc["stock_count"],
            "limit_up_count": tc["limit_up_count"], "avg_change_rate": avg_cr,
        }

    has_both, only_first, only_multi = [], [], []
    code_hd = {sc["code"]: sc.get("high_days", "") for sc in all_stocks}
    for cname, cdata in signal_concepts.items():
        has_f = any(code_hd.get(sc, "") == "首板" for sc in cdata["stocks"])
        has_m = any(code_hd.get(sc, "") and "板" in code_hd[sc] and code_hd[sc] != "首板" for sc in cdata["stocks"])
        if has_f and has_m:
            has_both.append(cname)
        elif has_f:
            only_first.append(cname)
        elif has_m:
            only_multi.append(cname)

    # 情绪指标（从当前文件 market 信息获取）
    market_data = lu.get("market", {})
    luc = market_data.get("limit_up_count", {}).get("today", {})
    hit = luc.get("num", 0)
    broke = luc.get("open_num", 0)
    broken_rate = round(broke / hit * 100, 1) if hit > 0 else 0.0
    seal_rate = round((hit - broke) / hit * 100, 1) if hit > 0 else 0.0

    tr_sum = sum(sc.get("turnover_rate") or 0 for sc in all_stocks)
    tr_cnt = sum(1 for sc in all_stocks if sc.get("turnover_rate") is not None)
    avg_tr = round(tr_sum / max(tr_cnt, 1), 2) if tr_cnt > 0 else 0.0

    top3 = sorted(signal_concepts.values(), key=lambda v: v["stock_count"], reverse=True)[:3]
    top3_stock_cnt = sum(v["stock_count"] for v in top3)
    stock_conc = round(top3_stock_cnt / max(total_limit_up, 1) * 100, 1)
    limitup_conc = round(top3_stock_cnt / max(total_limit_up, 1) * 100, 1)

    statistics = {
        "涨停家数": total_limit_up,
        "跌停家数": total_limit_down,
        "信号分组数": group_count,
        "噪音过滤标签数": sum(noise_tag_counter.values()),
        "噪音only股票数": len(noise_only_stocks),
        "unclassified股票数": len(unclassified),
        "题材规模分布": {
            "大题材（≥5只）": large,
            "中等题材（3-4只）": medium,
            "小题材（<3只）": small,
        },
        "最强题材": strongest,
        "梯队完整性": {
            "有首板有连板": has_both,
            "纯首板": only_first,
            "纯连板": only_multi,
        },
        "资金集中度": {
            "前三大题材股票数占比_%": stock_conc,
            "前三大题材涨停数占比_%": limitup_conc,
        },
        "情绪指标": {
            "炸板率_%": broken_rate,
            "封板率_%": seal_rate,
            "平均换手率_%": avg_tr,
        },
    }

    return {
        "_meta": {
            "date": date_str,
            "stage": "中间产物 — 噪音过滤 + concepts_all.json 匹配完成，待 LLM 分析产出最终 sub_concept",
            "spec_ref": "specs/数据处理/02-数据加工/02-LLM加工/涨停题材分组.md",
            "reference_coverage": {
                "matched": ref_matched_count,
                "unmatched": ref_unmatched_count,
                "rate": round(ref_matched_count / max(len(all_stocks), 1) * 100, 1),
            },
        },
        "limit_up_stocks": limit_up_stocks,
        "limit_down_stocks": limit_down_stocks,
        "signal_concepts": signal_concepts,
        "noise_only_stocks": noise_only_stocks,
        "unclassified": unclassified,
        "noise_stats": noise_stats,
        "统计": statistics,
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 加载 concepts_all.json 参考数据
    print("📂 加载参考数据...")
    ref_map = load_reference()
    print(f"   → 参考数据覆盖 {len(ref_map)} 只股票")

    # 解析可选日期参数
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target_date = args[0] if args else None

    # 收集所有输入文件，按日期排序
    if target_date:
        files = sorted(INPUT_DIR.glob(f"{target_date}_涨停板.json"))
        if not files:
            print(f"❌ {INPUT_DIR}/ 下没有 {target_date}_涨停板.json 文件")
            sys.exit(1)
    else:
        files = sorted(INPUT_DIR.glob("*_涨停板.json"))
        if not files:
            print(f"❌ {INPUT_DIR}/ 下没有 *_涨停板.json 文件")
            sys.exit(1)

    label = f"仅 {target_date}" if target_date else f"共 {len(files)} 个文件"
    print(f"═══ 加工 limit_up → limit_up_中间（{label}）═══")

    total_ok = 0
    total_skip = 0
    for fp in files:
        result = process_one(fp, ref_map)
        if result is None:
            total_skip += 1
            continue

        date_str = result["_meta"]["date"]
        out_path = OUTPUT_DIR / f"{date_str}_涨停概念分组_中间.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        stats = result["统计"]
        ns = result["noise_stats"]
        rc = result["_meta"].get("reference_coverage", {})
        print(f"  ✓ {date_str}: {stats['涨停家数']}只涨停+{stats['跌停家数']}只跌停, "
              f"{stats['信号分组数']}个信号组, "
              f"噪音{ns['过滤标签总数']}个标签, "
              f"最强={stats['最强题材'].get('concept','N/A')}, "
              f"参考匹配={rc.get('matched',0)}/{rc.get('rate','N/A')}%")
        total_ok += 1

    print(f"\n═══ 完成: {total_ok} 个成功, {total_skip} 个跳过 ═══")
    print(f"输出目录: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()