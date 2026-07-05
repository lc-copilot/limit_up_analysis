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
        memberships = [],
    } = stock;

    const mid = midData.find((s) => s.code === symbol || s.code === symbol);
    const rawConcepts = mid?.concepts || [];
    const highDays = mid?.high_days || stock.high_days || "";
    const consecutiveBoards = mid?.consecutive_boards || stock.consecutive_boards || "";
    const turnoverRate = mid?.turnover_rate || stock.turnover_rate || "";
    const changeRate = mid?.change_rate || stock.change_rate || "";
    const currencyValue = mid?.currency_value || stock.currency_value || "";

    const moneyStr = currencyValue ? `${(currencyValue / 1e8).toFixed(1)}亿` : "—";

    // 从 memberships 中提取所有备注（新格式）
    // 兼容旧格式：如果 stock 有直接的 note/sub_concept 字段也展示
    const notes = [];
    if (memberships.length > 0) {
        for (const m of memberships) {
            const path = m.sub_concept ? m.sub_concept.join(" → ") : "";
            if (m.note) {
                notes.push(`**${path}**：${m.note}`);
            } else if (path) {
                notes.push(`**${path}**`);
            }
        }
    } else if (stock.note) {
        // 旧格式兼容
        const path = stock.sub_concept ? stock.sub_concept.join(" → ") : "";
        notes.push(`${path ? `**${path}**：` : ""}${stock.note}`);
    }

    return `

---

## ${symbol} ${name}

> ${highDays} · ${consecutiveBoards || "首板"} · 涨幅 ${changeRate || "—"}% · 换手 ${turnoverRate || "—"}% · 流通 ${moneyStr}

### 1️⃣ 为什么涨停？

**📊 市场标签：** ${rawConcepts.length > 0 ? rawConcepts.join("、") : "暂无"}

${notes.length > 0 ? `**📝 概念分类与备注：**\n\n${notes.join("\n\n")}` : ""}

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
    // 新格式：stocks 数组，每只股票一个条目，包含 memberships 数组
    const stocks = analysisData.stocks || [];

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

    // 新格式：每只股票已是单一条目（memberships 数组承载多概念），直接遍历
    const mergedSections = [];
    for (const stock of stocks) {
        const section = generateTemplate(stock, midStocks);
        mergedSections.push(section);
    }

    // 构建完整内容
    const total = stocks.length;
    const totalMemberships = stocks.reduce((sum, s) => sum + (s.memberships || []).length, 0);

    const header = `# 📊 ${dateStr} 涨停概念分组 — 调研模板

生成时间: ${new Date().toLocaleString("zh-CN")}
股票总数: ${total} 只，概念记录总数: ${totalMemberships} 条

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
    console.log(`   股票: ${total} 只，概念记录: ${totalMemberships} 条`);
    console.log(`   大小: ${(Buffer.byteLength(fullContent, "utf-8") / 1024).toFixed(0)} KB`);

    if (openAfter) {
        const { execSync } = require("child_process");
        execSync(`open "${outPath}"`);
    }
}

main();