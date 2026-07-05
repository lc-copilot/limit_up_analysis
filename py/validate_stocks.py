#!/usr/bin/env python3
"""验证 limit_up 原始数据与 limit_up_分析 最终输出的个股一致性。

用法:
    python py/validate_stocks.py 2026-06-29
    python py/validate_stocks.py              # 默认取 limit_up_分析/ 中最新的日期
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def load_raw_stocks(date_str: str) -> dict[str, set[str]]:
    """从原始数据中提取所有涨跌停股票代码，返回 {"limit_up": set, "limit_down": set}。"""
    raw_path = BASE / "limit_up" / f"{date_str}_涨停板.json"
    if not raw_path.exists():
        print(f"❌ 原始数据不存在: {raw_path}")
        sys.exit(1)

    data = json.loads(raw_path.read_text(encoding="utf-8"))
    up_items = data.get("limit_up", {}).get("items", [])
    down_items = data.get("limit_down", {}).get("items", [])

    return {
        "limit_up": {it["symbol"] for it in up_items if it.get("symbol")},
        "limit_down": {it["symbol"] for it in down_items if it.get("symbol")},
    }


def load_output_stocks(date_str: str) -> dict[str, set[str]]:
    """从最终输出中提取所有涨跌停股票代码，返回 {"limit_up": set, "limit_down": set}。"""
    out_path = BASE / "limit_up_分析" / f"{date_str}.json"
    if not out_path.exists():
        print(f"❌ 最终输出不存在: {out_path}")
        sys.exit(1)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    result = {"limit_up": set(), "limit_down": set()}

    # stocks 数组，每只股票有 symbol + type 字段
    for s in data.get("stocks", []):
        sym = s.get("symbol", "")
        typ = s.get("type", "")
        if sym and typ in result:
            result[typ].add(sym)
    return result


def main():
    # 解析日期
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        # 默认取 limit_up_分析/ 中最新的日期
        files = sorted(BASE.glob("limit_up_分析/*.json"))
        if not files:
            print("❌ limit_up_分析/ 下没有输出文件")
            sys.exit(1)
        date_str = files[-1].stem
        print(f"📅 自动选择最新日期: {date_str}")

    raw = load_raw_stocks(date_str)
    out = load_output_stocks(date_str)

    all_ok = True

    for typ, label in [("limit_up", "涨停"), ("limit_down", "跌停")]:
        raw_set = raw[typ]
        out_set = out[typ]

        # 统计
        raw_count = len(raw_set)
        out_count = len(out_set)
        missing = raw_set - out_set
        extra = out_set - raw_set

        print(f"\n{'='*50}")
        print(f"  {label} — {date_str}")
        print(f"{'='*50}")
        print(f"  原始数据: {raw_count} 只")
        print(f"  最终输出: {out_count} 只")

        if missing:
            all_ok = False
            print(f"  ❌ 缺失 ({len(missing)} 只):")
            for sym in sorted(missing):
                # 尝试从原始数据中找名字
                name = ""
                raw_path = BASE / "limit_up" / f"{date_str}_涨停板.json"
                data = json.loads(raw_path.read_text(encoding="utf-8"))
                for it in data.get(typ, {}).get("items", []):
                    if it.get("symbol") == sym:
                        name = it.get("name", "")
                        break
                print(f"     {sym} {name}")
        else:
            print(f"  ✅ 无缺失")

        if extra:
            print(f"  ⚠ 多余 ({len(extra)} 只，原始数据中不存在):")
            for sym in sorted(extra):
                print(f"     {sym}")
        else:
            print(f"  ✅ 无多余")

        if raw_count != out_count:
            all_ok = False

    # ── 汇总 ──
    print(f"\n{'='*50}")
    if all_ok:
        print("✅ 全部通过：涨跌停个股数量一致，无缺失无多余")
    else:
        raw_total = len(raw["limit_up"]) + len(raw["limit_down"])
        out_total = len(out["limit_up"]) + len(out["limit_down"])
        print(f"❌ 验证失败！")
        print(f"   原始总计: {raw_total} 只（涨停 {len(raw['limit_up'])} + 跌停 {len(raw['limit_down'])}）")
        print(f"   输出总计: {out_total} 只（涨停 {len(out['limit_up'])} + 跌停 {len(out['limit_down'])}）")
        sys.exit(1)


if __name__ == "__main__":
    main()