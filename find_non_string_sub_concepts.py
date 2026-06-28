#!/usr/bin/env python3
"""
找出 concepts_all.json 中 sub_concept 里包含非字符串（即嵌套列表）的条目。

输出格式：
  - 总条目数、含非字符串 sub_concept 的条目数
  - 每个条目的 symbol、name、以及每个非字符串 sub_concept 的完整内容
  - 统计信息：哪些父概念下嵌套最多
"""

import json
from collections import Counter

def main():
    with open('concepts_all.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"总条目数: {len(data)}")
    print()

    results = []
    parent_counter = Counter()

    for idx, item in enumerate(data):
        sc = item.get('sub_concept', [])
        symbol = item.get('symbol', '?')
        name = item.get('name', '?')
        non_string_entries = []
        for j, val in enumerate(sc):
            if not isinstance(val, str):
                non_string_entries.append((j, val))
                if isinstance(val, list) and len(val) >= 2:
                    parent_counter[val[0]] += 1

        if non_string_entries:
            results.append((idx, symbol, name, non_string_entries))

    print(f"含非字符串 sub_concept 的条目数: {len(results)}")
    print(f"非字符串 sub_concept 总个数: {sum(len(r[3]) for r in results)}")
    print()

    # 按 symbol 排序输出
    results.sort(key=lambda x: x[1])

    for idx, symbol, name, entries in results:
        print(f"[{idx:4d}] {symbol:12s} {name}")
        for pos, val in entries:
            if isinstance(val, list):
                print(f"       sub_concept[{pos}] = {val}")
            else:
                print(f"       sub_concept[{pos}] = {repr(val)}")
        print()

    # 统计
    print("=" * 60)
    print("按一级概念统计（出现次数 ≥ 5）：")
    print("=" * 60)
    for concept, count in parent_counter.most_common():
        if count >= 5:
            print(f"  {concept}: {count}")

if __name__ == '__main__':
    main()
