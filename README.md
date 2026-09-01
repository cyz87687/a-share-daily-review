# A股每日自动复盘工具（adr）v0.1

收盘后一键跑批，生成**自包含、零外链**的每日复盘 HTML，每条判断均带数据溯源与证伪条件。
技术栈：Python 3.13 + mootdx（在线行情）+ pandas/numpy（计算）+ Jinja2（模板）+ 原生 HTML/CSS/JS（渲染）。
**无 Web 框架、无数据库、无外链 CDN。**

> 板块分类口径：通达信 `industry` 编码（非申万），页面全局标注「通达信行业编码口径」。
> 量比口径：当日量 ÷ 5 日均量（日线近似），每处标注 `[口径:日线近似]`。
> 涨跌幅：不复权口径（真实涨幅）；均线/形态/关键价位：自实现前复权。

## 1. 安装

运行环境：Python 3.13。建议使用独立 venv 安装依赖（本地 / CI 通用）：

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

依赖清单见 `requirements.txt`，核心为 mootdx 0.11.7（在线行情）+ pandas 3.0.3 + numpy 2.4.6 + Jinja2 + PyYAML。

## 2. 首次运行（prepare 模式，无需 API key）

```bash
cd a_share_daily_review
TQDM_DISABLE=1 python review.py run --date 2026-08-31 --mode prepare
```

产出：
- `output/2026-08-31/review-2026-08-31.html`（双击即开，机器筛选结果 + 个股卡片）
- `output/2026-08-31/prompt.txt`（候选数据包 + 提示词，粘贴到任意对话式 AI）
- `output/2026-08-31/datapack.json`（结构化候选数据包）
- `data/logs/holdings-2026-08-31.csv`（留存日志，按日幂等覆盖）

## 3. auto 模式（本地复盘引擎，零外部 LLM）

`auto` 模式默认走内置**本地复盘引擎**（`src/adr/local_review.py`）：将单股 8 项复盘 + 市场总结方法论固化为确定性规则，全部输入来自真实行情数据包（mootdx 字段），**零外部 LLM、零 api_key**，因此完全离线自治、可在 CI 中每日自动运行。

```bash
TQDM_DISABLE=1 python review.py run --date 2026-08-31 --mode auto
```

- 每只会自动产出：量价结构 / 资金定性 / 板块定位 / 关键价位 / 介入三件套 / 证伪条件 / 核心风险（共 8 项），逐条可溯源。
- 主题主线优先取已落盘真实题材榜 `data/thematic/{date}.json`（经 westock/wind 核验）；缺失则退化为通达信宽基口径并明确标注，绝不联网编造。
- 引擎异常时整轮降级为「卡片待补充」，`prepare` 模式不受影响。

## 4. 人工终审与回填

打开 `review-{date}.html`，逐只核对数字与原始行情，剔除误判 / 加入观察池（上限 5，localStorage 持久化），
导出 `review-{date}.json` 后回填：

```bash
TQDM_DISABLE=1 python review.py finalize --date 2026-08-31 --review output/2026-08-31/review-2026-08-31.json
```

## 5. 重建索引

```bash
TQDM_DISABLE=1 python review.py index
```

## 6. 退出码

| 码 | 含义 |
|---|---|
| 1 | 数据可用性断言失败（不写任何输出文件） |
| 2 | 质量门未通过（仍发布，HTML 顶部红色警示条） |
| 0 | 正常完成 / 单批 AI 失败降级 |

调度（cron / launchd）模板见 `docs/CRON.md`（P1 前哨，P0 手动执行即可）。

## 7. 已知限制（DESIGN 第九章 A1–A7）

- A1/A2 板块名称显示 `TDX#{code}`，行业编码→名称映射待补齐（P1 数据驱动校准）。
- A3 全市场快照约 210 只缺失，记入 `missing_log` 并在页脚公示。
- A4 最高连板高度 P0 输出 `N/A`（需跨日状态机，后续补齐）。
- A5 两市成交额环比首日 `N/A`（需前一日基准）。
- A6 排除清单第 5 类（公告/事件）P0 未启用，页面标注。
- A7 本地复盘引擎异常时 auto 降级为「卡片待补充」，prepare 模式不受影响；auto 模式无需任何 key。

## 8. 每日自动更新（GitHub Actions）

仓库已内置 `.github/workflows/daily.yml`：每个交易日 **16:30 北京时间（UTC 08:30，仅周一至周五）** 自动触发，联网拉取通达信当日行情 → 本地引擎生成复盘 → 提交回 `main` 分支。支持手动触发（`workflow_dispatch`）。

- 引擎为本地规则、零 key，故 CI 可完全自治运行。
- 联网失败时自动重试 3 次；非交易日 / 当日行情未就绪则不产出报告、跳过提交（CI 不报错）。
- 主题主线在 CI 环境无 westock/wind 核验源，自动退化为通达信宽基口径（页面已标注），保持零幻觉。
- 可选：在仓库 Settings → Pages 将源设为 `main` 分支 `/output` 目录，即可获得公开访问 URL（每次推送自动更新）。
