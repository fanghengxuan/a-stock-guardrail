# Guardrail

> A 股投资决策辅助系统 — 基于 Agentic 取数的深度价值分析框架，防止本金永久性损失。

## 现在的主线：去卡直出 + 代码解析看板（2026-09-06 起）

一次分析的完整链路（约 2–5 分钟，全程流式）：

```
输入代码/名称
  → L1 代码层并发取数（腾讯/东财/Baostock 三源交叉）+ 下钻 Agent 循环（≤6 轮工具调用，tool_trace.json 快照）
  → LLM 按《执行手册》流式直写 20 节 Markdown 报告（无 JSON schema——markdown 是其天然输出形态）
  → validate_report 20 节校验 → 落盘 analysis_reports/<代码>-<名称>/report_direct.md
  → dashboard 解析器（纯代码，零 LLM）提取 DashboardData → dashboard.json 同目录落盘
  → SSE 逐节闭合逐区块点亮前端看板 → done 权威整量替换
```

- **报告是唯一真相源**：看板 23 个区块的每个数字由确定性正则从报告原文提取（100% 忠实，不经 LLM 转录），解析失败按三层降级（结构化→回退链→原文透出，永不空屏、永不造数）。
- **历史秒开**：`/api/reports` 扫描落盘的 dashboard.json，点开纯渲染、不再跑模型。
- **视觉合同**：前端版式 = decision-card-dashboard 技能的银行审批单设计系统（宋体标题/台账数字/印章/估值标尺/红涨绿跌），template.html `<style>` 原样移植，零 UI 组件库。

并存但非主线：决策卡 JSON 管线（`orchestrator.run_analysis` → StockDecisionCard，端点 `/api/analyze*`）原样保留、不受看板重构影响。

## HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/report/stream` | 去卡直出分析 SSE：connected → phase → thought（当前节+尾部摘录）→ section_done×N（逐区块）→ heartbeat → report_saved → done（DashboardData 全文） |
| GET | `/api/reports` | 历史列表（判据=目录含 dashboard.json，按 mtime 降序） |
| GET | `/api/reports/{code}` | 看板 DashboardData（6 位代码校验，防路径穿越） |
| GET | `/api/reports/{code}/report.md` | 报告 MD 原文（看板数字核验入口） |
| POST/GET | `/api/analyze*` | 决策卡 JSON 管线（保留兼容） |

## 一键启动

```bash
./start.sh            # 后端 :8000 + 前端 Vite :5173（启动前刷东财 Cookie）
# 或手动：
cd backend && uv run uvicorn main:app --reload --port 8000
cd frontend && npm install && npm run dev
```

打开 http://localhost:5173 输入代码或名称（如 600036 / 招商银行）；`/reports` 为历史记录。
LLM 端点必填 `backend/.env` 的 `OPENAI_BASE_URL`（OpenAI 兼容，无默认值——缺失启动即报错）；分析链路全程流式（网关晚高峰对非流式大请求会积压挂死，流式可幸存）。

## 目录结构

```
Guardrail/
├── A股投资决策系统执行手册.md      # 核心规则手册（唯一标准，L2/L3 判定规则注入直写 prompt）
├── analysis_reports/               # 报告产物（git 忽略）：每标的一个目录
│   └── 600036-招商银行/{report_direct.md, dashboard.json, tool_trace.json}
├── backend/
│   ├── main.py                     # FastAPI 入口（旧 /api/analyze* + 新看板四端点）
│   ├── report_service.py           # 看板服务层：SSE 事件流 / dashboard.json 落盘 / 历史扫描
│   ├── dashboard/                  # ★ MD→DashboardData 确定性解析层
│   │   ├── scanner.py              #   21 节 SPEC 定位 + 流式/一次性共用闭节判据
│   │   └── parser.py               #   23 区块提取器 + 三层降级链（数字只出自正则捕获组）
│   ├── report_generator.py         # 20 节契约 REQUIRED_SECTIONS / validate_report / 决策卡→MD writer
│   ├── orchestrator.py             # 决策卡 JSON 管线（保留）+ resolve_stock_query
│   ├── decision_agents/            # L1 取数（并发/熔断/缓存）+ 下钻工具 ×7 + 路由台账
│   ├── scripts/
│   │   ├── report_direct.py        # 去卡直出（CLI 与服务端共用 run_report_direct）
│   │   └── ...                     # 抓样/验收/看门狗脚本
│   ├── tests/                      # pytest：解析器 golden（三份实跑报告）+ 流式/历史端点 + 取数回归
│   └── data_routes.md              # 取数路由沉淀台账（多路由权重 + 勘误）
├── frontend/
│   └── src/
│       ├── pages/                  # WorkspacePage（工作台）/ HistoryPage / ReportPage
│       ├── dashboard/              # DashboardView 23 区块组件（模板版式保真移植）
│       ├── lib/                    # dashReducer（SSE→逐区块点亮）/ sse / reportStream / md-lite
│       ├── styles/dashboard.css    # 设计系统单一真相（自 template.html 移植，禁改色语义）
│       └── fixtures/               # golden-600036.json（测试固件）
├── deliverables/                   # 单文件 HTML 看板交付件存档（重构前的技能产物）
├── docs/superpowers/               # 历史设计档案（plans/specs，非现役）
└── 标的分析结果参考/                # 人工分析基准样本（校验清单蒸馏来源）
```

## 技术栈

| 层 | 技术 |
|------|------|
| **后端** | FastAPI + OpenAI Agents SDK（Responses API 流式）+ deepseek-v4-flash |
| **数据** | 腾讯财经 · 东方财富 · Baostock · akshare（多源交叉验证 + 熔断 + TTL 缓存） |
| **看板解析** | 纯 Python 标准库正则（零新依赖，golden 测试锁行为） |
| **前端** | React 19 + Vite 8 + TypeScript；无 UI 组件库 / 无图表库（版式全纯 CSS） |

## 数据源

采用多源策略模式（借鉴 [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) 58k⭐），内置熔断器 + TTL 缓存 + 指数退避重试 + UA 轮换 + 并发控制。

| 数据源 | 用途 | 精度 | 费用 |
|------|------|------|------|
| 腾讯财经 `qt.gtimg.cn` | 行情（最新价 / PE / PB / 市值） | 主源 | 免费 |
| 东方财富 `push2` / `datacenter` | 财务（ROE / 净利润 / 营收 / 资金流） | 主源 | 免费 |
| Baostock（证券宝） | 股息率 / 分红 / 财报备源 | 主源 | 免费 |

下钻工具 7 类数据源（2026-09-05 实测，明细与勘误见 `backend/data_routes.md`）：融资融券、银行资产质量、新闻舆情、行业估值、52周高低、公告、研报。

**客观数据精度：** 最新价 / PE / 股息率均 < 1% 偏差（经验证：招商银行 0.0%/0.3%/0.0%，格力电器 0.0%/0.4%/0.1%）。

## 已知边界

- 一次分析绑一条 SSE：整页刷新/关页会中断该次分析（无作业队列，by design）；同标的并发提交会被去重拒绝。
- 报告格式漂移由三层降级链兜底（每消化一种新实跑形态即固化进 tests/fixtures golden）；看板与报告不一致时以 `report.md` 原文为准。
- 历史仅收录看板化之后的新分析；重构前的存量目录（含尾杠 `600036-` 等）不出现在列表。

## 团队分工

| 角色 | 职责 |
|------|------|
| **Claude Code（Lead）** | 任务编排、架构决策、质量把控 |
| **小A** | 前端开发（React 组件 / SSE 渐进式渲染 / UI 交互） |
| **小B** | 后端开发（Agent 链路 / 数据获取 / LLM 校准） |
| **小C** | 代码审查、联调测试、验收验证、调研报告 |

## 许可证

MIT
