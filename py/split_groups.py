#!/usr/bin/env python3
"""split_groups.py — 将中间 JSON 的股票分为 5 组，供 5 个子 LLM 并行分析。

用法:
  python3 py/split_groups.py 2026-06-26

输入:
  limit_up_中间/{date}_涨停概念分组_中间.json

输出:
  limit_up_中间/{date}_group_1.json  ~  group_5.json
  （每份包含: 该组的股票列表 + 热门概念.json 信息 + 分析指令）
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
INTERMEDIATE_DIR = BASE / "limit_up_中间"
HOT_CONCEPTS_PATH = BASE / "热门概念.json"
GROUP_COUNT = 5

# ── 分析指令（嵌入每份分组文件，子 LLM 直接读取） ──

ANALYSIS_INSTRUCTION = """
## 分析任务

请对以下每只股票，分析得出最终的 sub_concept 和 note。

### 输入字段说明

| 字段 | 说明 |
|---|---|
| `concepts` | 同花顺信号标签，市场给它的所有标签 |
| `primary_concept` | 同花顺信号标签，市场给它的主要标签（频次最高） |
| `real_concepts` | concepts_all.json 中已有的概念分类（可能为空） |

### 判断逻辑（按优先级）

1. **real_concepts 不为空** → 优先使用，判断是否仍然有效
2. **热门概念匹配** → 如果该股票与热门概念相关，优先使用热门概念的层级
3. **concepts / primary_concept 有线索** → 结合 AI 知识判断
4. **以上都没有** → 用 AI 知识自行判断，仍不确定则填 ["未知"]

### 输出格式

每只股票输出一条或多条记录（多概念时多条），格式如下：

```json
{
  "symbol": "股票代码",
  "name": "股票名称",
  "note": "概念相关说明，要写具体",
  "strength": 5,
  "sub_concept": ["一级", "二级", "(三级)"],
  "type": "limit_up"
}
```

### 规则

- sub_concept 至少 2 级，最多 3 级
- note 要写具体，说明为什么属于这个分类
- strength 全部填 5
- 一只股票可能有多个概念 → 输出多条记录
- type: "limit_up" 或 "limit_down"
"""


def load_hot_concepts() -> list[dict]:
    """加载热门概念.json，返回概念列表。"""
    if not HOT_CONCEPTS_PATH.exists():
        return []
    return json.loads(HOT_CONCEPTS_PATH.read_text(encoding="utf-8"))


def main():
    if len(sys.argv) < 2:
        print("用法: python3 py/split_groups.py YYYY-MM-DD")
        sys.exit(1)

    date_str = sys.argv[1]
    input_path = INTERMEDIATE_DIR / f"{date_str}_涨停概念分组_中间.json"

    if not input_path.exists():
        print(f"❌ 中间文件不存在: {input_path}")
        sys.exit(1)

    data = json.loads(input_path.read_text(encoding="utf-8"))
    up_stocks = data.get("limit_up_stocks", [])
    down_stocks = data.get("limit_down_stocks", [])
    unclassified = data.get("unclassified", [])
    noise_only = data.get("noise_only_stocks", [])

    if not up_stocks and not down_stocks:
        print(f"⚠ {date_str} 无股票数据")
        sys.exit(1)

    # 合并所有需要分析的股票（涨停在前，跌停在后）
    all_stocks = list(up_stocks) + list(down_stocks)

    # 加载热门概念
    hot_concepts = load_hot_concepts()

    # ── 分组策略：按涨停/跌停时间均匀分配 ──
    def sort_key(s):
        t = s.get("first_limit_up_time")
        if t is None:
            return (1, 0, "")
        if isinstance(t, str):
            return (0, 0, t)
        return (0, t, "")
    all_stocks.sort(key=sort_key)

    # 分成 5 组
    total = len(all_stocks)
    group_size = math.ceil(total / GROUP_COUNT)
    groups = []
    for i in range(GROUP_COUNT):
        start = i * group_size
        end = min(start + group_size, total)
        if start >= total:
            break
        groups.append(all_stocks[start:end])

    # ── 写入分组文件 ──
    meta = {
        "date": date_str,
        "total_stocks": total,
        "group_count": len(groups),
        "stage": "分组完成 — 待子 LLM 分析",
        "analysis_instruction": ANALYSIS_INSTRUCTION.strip(),
        "hot_concepts": hot_concepts,
    }

    for i, group_stocks in enumerate(groups):
        group_data = {
            "_meta": {
                **meta,
                "group": i + 1,
                "group_total": len(group_stocks),
            },
            "stocks": group_stocks,
        }
        out_path = INTERMEDIATE_DIR / f"{date_str}_group_{i+1}.json"
        out_path.write_text(
            json.dumps(group_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 打印统计 ──
    print(f"📊 {date_str} 分组结果")
    print(f"   总股票: {total} 只")
    print(f"   分组数: {len(groups)} 组")
    for i, g in enumerate(groups):
        print(f"   第{i+1}组: {len(g)} 只 → {date_str}_group_{i+1}.json")
    print(f"\n💡 将每组文件分别交给子 LLM 分析")
    print(f"   子 LLM 产出格式: {date_str}_group_{i+1}_result.json")
    print(f"   (文件名约定: 在 group 前加 result 后缀)")


if __name__ == "__main__":
    main()