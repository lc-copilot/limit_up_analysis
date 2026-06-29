#!/usr/bin/env node
/**
 * gen_豆包模板.js — 从 LLM 分析后的 JSON 生成调研模板。
 *
 * 读入 limit_up_分析/{date}.json，
 * 为每只股票生成一个简洁的调研段落，聚焦三个核心问题：
 *   1. 为什么涨停？
 *   2. 目前的猜测是什么，依据是什么？
 *   3. 要求参考的资料真实可靠
 *
 * 同时读取 limit_up_中间/{date}_涨停概念分组_中间.json 获取原始 concepts。
 *
 * 用法:
 *   node gen_豆包模板.js 2026-06-26
 *   node gen_豆包模板.js              # 处理最新一天
 *   node gen_豆包模板.js --open       # 处理完成后自动打开（macOS）
 */
const fs = require("fs");
const path = require("path");

// ── 路径配置 ──────────────────────────────
const BASE = path.join(__dirname, '..');
const ANALYSIS_DIR = path.join(BASE, "limit_up_分析");
const INTERMEDIATE_DIR = path.join(BASE, "limit_up_中间");
const OUTPUT_DIR = path.join(BASE, "豆包调研模板");

// ── 个股调研模板 ────────────────────
function generateTemplate(stock, midData) {
    const {
        symbol = "",
        name = "",
        note = "",
    } = stock;

    const mid = midData.find((s) => s.code === symbol || s.code === symbol);
    const rawConcepts = mid?.concepts || [];
    const highDays = mid?.high_days || stock.high_days || "";
    const consecutiveBoards = mid?.consecutive_boards || stock.consecutive_boards || "";
    const turnoverRate = mid?.turnover_rate || stock.turnover_rate || "";
    const changeRate = mid?.change_rate || stock.change_rate || "";
    const currencyValue = mid?.currency_value || stock.currency_value || "";

    const moneyStr = currencyValue ? `${(currencyValue / 1e8).toFixed(1)}亿` : "—";

    return `

---

## ${symbol} ${name}

> ${highDays} · ${consecutiveBoards || "首板"} · 涨幅 ${changeRate || "—"}% · 换手 ${turnoverRate || "—"}% · 流通 ${moneyStr}

### 1️⃣ 为什么涨停？

**📊 市场标签：** ${rawConcepts.length > 0 ? rawConcepts.join("、") : "暂无"}

${note ? `**📝 备注：** ${note}` : ""}

### 3️⃣ 参考资料的可靠性要求

> ⚠️ **重要：** 所有分析必须基于真实可靠的资料，请标注可信度等级：
>
> | 等级 | 含义 | 示例 |
> |------|------|------|
> | ✅ 可靠 | 官方一手信息 | 公司公告、财报、交易所数据、官方政策文件 |
> | ⚠️ 待验证 | 二手信息，需交叉验证 | 媒体报道、财经资讯、网络传闻 |
> | ❌ 存疑 | 无法证实，不可作为依据 | 小道消息、猜测、社交媒体传言 |
>
> **💡 交叉验证方法：**
> - 同一信息是否有多个独立来源证实？
> - 信息来源是否权威？（官方 > 专业媒体 > 自媒体）
> - 信息时效性如何？（是否仍是当前有效信息？）
>
> **📌 注意事项：**
> - 题材分类库是历史静态数据，可能存在滞后
> - AI 初步分类基于公开信息和历史数据，需人工复核
> - **最终结论必须结合最新公告和市场动态，注明"确认"或"推测"**

`;
}

function main() {
    const args = process.argv.slice(2);
    const openAfter = args.includes("--open");

    // 确定日期
    let dateStr = args.find((a) => /^\d{4}-\d{2}-\d{2}$/.test(a));
    if (!dateStr) {
        // 找最新一天
        const files = fs
            .readdirSync(ANALYSIS_DIR)
            .filter((f) => /^\d{4}-\d{2}-\d{2}\.json$/.test(f))
            .sort()
            .reverse();
        if (files.length === 0) {
            console.error("❌ limit_up_分析/ 下没有分析结果文件");
            process.exit(1);
        }
        dateStr = files[0].replace(".json", "");
        console.log(`ℹ️  未指定日期，使用最新: ${dateStr}`);
    }

    // 读分析结果
    let analysisPath = path.join(
        ANALYSIS_DIR,
        `${dateStr}.json`
    );
    const analysisData = JSON.parse(fs.readFileSync(analysisPath, "utf-8"));
    // 支持新旧两种格式：新格式有 limit_up_stocks/limit_down_stocks，旧格式有 stocks
    const stocks = [
        ...(analysisData.limit_up_stocks || []),
        ...(analysisData.limit_down_stocks || []),
        ...(analysisData.stocks || []),
    ];

    if (stocks.length === 0) {
        console.error(`❌ ${analysisPath} 中没有股票数据`);
        process.exit(1);
    }

    // 读中间数据（获取原始 concepts）
    const midPath = path.join(
        INTERMEDIATE_DIR,
        `${dateStr}_涨停概念分组_中间.json`
    );
    let midStocks = [];
    if (fs.existsSync(midPath)) {
        const midData = JSON.parse(fs.readFileSync(midPath, "utf-8"));
        midStocks = [
            ...(midData.limit_up_stocks || []),
            ...(midData.limit_down_stocks || []),
            ...(midData.stocks_with_concepts || []),
        ];
    } else {
        console.warn(`  ⚠ 中间数据不存在，原始 concepts 将为空: ${midPath}`);
    }

    // 按 symbol 去重分组（同一个股票可能有多条概念记录，合并到一个调研段落）
    const stockGroups = new Map();
    for (const stock of stocks) {
        const key = stock.symbol;
        if (!stockGroups.has(key)) {
            stockGroups.set(key, []);
        }
        stockGroups.get(key).push(stock);
    }

    // 重新生成：一个股票一个段落（合并多条概念记录）
    const mergedSections = [];
    for (const [sym, entries] of stockGroups) {
        const section = generateTemplate(
            entries[0],
            midStocks
        );
        mergedSections.push(section);
    }

    // 构建完整内容
    const total = stockGroups.size;
    const refCount = stocks.filter((s) => s._source === "reference").length;
    const aiCount = stocks.filter((s) => s._source !== "reference").length;

    const header = `# 📊 ${dateStr} 涨停概念分组 — 调研模板

生成时间: ${new Date().toLocaleString("zh-CN")}
股票总数: ${total} 只（参考匹配 ${refCount} 只，AI 补充 ${aiCount} 只）

---

`;

    const footer = `
`;

    const fullContent = header + mergedSections.join("\n\n---\n\n") + footer;

    // 写入
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    const outPath = path.join(OUTPUT_DIR, `${dateStr}_豆包调研模板.md`);
    fs.writeFileSync(outPath, fullContent, "utf-8");

    console.log(`\n✅ 调研模板已生成`);
    console.log(`   输出: ${outPath}`);
    console.log(`   股票: ${total} 只（参考 ${refCount} + AI ${aiCount}）`);
    console.log(`   大小: ${(Buffer.byteLength(fullContent, "utf-8") / 1024).toFixed(0)} KB`);

    if (openAfter) {
        const { execSync } = require("child_process");
        execSync(`open "${outPath}"`);
    }
}

main();