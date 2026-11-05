# Guardrail

**A 股投资决策辅助系统：多源取数 → LLM 流式直写 26 节研究报告 → 确定性解析成决策备忘录。**

第一原则：**防止本金永久性损失**。看板上的每个数字都来自报告原文的正则捕获，而不是 LLM 的二次提取或换算。

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)

## 简介

Guardrail 是一个本地运行的研究工作台。输入股票代码或名称，系统并发拉取行情、财务、公告等多源数据，由 LLM 按内置《执行手册》流式写出分层报告，再由纯标准库正则解析器把报告拆成结构化看板，前端逐节点亮，最终呈现为“银行审批单”风格的决策备忘录。

面向希望看到**可复核的推理过程与数据出处**、而不只是一句涨跌结论的个人投资者。

> 界面截图待补充（仓库暂未附带真实运行截图）。

## 核心能力

| 能力 | 说明 |
|---|---|
| 多源交叉取数 | 腾讯财经 / 东方财富 / Baostock / akshare 并发拉取，带熔断、TTL 缓存与指数退避 |
| 下钻 Agent | 最多 6 轮工具循环，覆盖融资融券、银行资产质量、新闻、行业估值、52 周高低、公告、研报 7 类数据 |
| 26 节分层报告 | L0 数据底稿 8 节 + L2 分析计算 16 节 + L3 决策输出 2 节，`validate_report` 校验结构 |
| 报告即唯一真相源 | 看板变量由正则从报告原文捕获；解析失败按“结构化 → 回退 → 原文透出”三层降级，不空屏、不造数 |
| 全程流式 | 对 LLM 的调用统一走 SSE，前端逐节点亮；历史报告直接读取落盘 `dashboard.json`，不再调用模型 |
| 零组件库前端 | React 19 + TypeScript，版式由纯 CSS 实现，不引入 UI / 图表组件库 |

## 工作流程

```text
输入代码 / 名称
  → L0 数据底稿：多源并发取数 + 下钻 Agent 循环
  → LLM 流式直写 26 节 Markdown 报告（validate_report 校验）
  → 落盘 analysis_reports/<代码>-<名称>/{report_direct.md, dashboard.json}
  → 确定性正则解析器提取全部看板变量
  → 前端 SSE 逐节点亮，完成后渲染六段决策备忘录
```

决策备忘录包含六段：决策头、估值、风险、论证、操作、明细。

## 快速开始

前置：Python ≥ 3.11、[uv](https://docs.astral.sh/uv/)、Node.js 与 npm。

**1. 配置 `backend/.env`**（参照 [`backend/.env.example`](backend/.env.example)）：

```bash
cp backend/.env.example backend/.env
```

| 变量 | 说明 |
|---|---|
| `OPENAI_API_KEY` | OpenAI 兼容端点的 API Key |
| `OPENAI_BASE_URL` | 必填，缺失时后端启动即报错 |
| `LLM_MODEL` | 模型名，默认 `deepseek-v4-flash` |
| `LLM_API_MODE` | 默认 `responses`；`chat_completions` 仅作应急回滚 |
| `LLM_DEEP_THINK` | 默认 `false`；开启后保留深度推理，但首字更慢 |

**2. 启动：**

```bash
./start.sh          # Linux / macOS 终端：后端 :8000 + 前端 :5173
```

macOS 也可双击 `start.command`。脚本会检查 venv，失效时按 `uv.lock` 重建，然后打开 `http://localhost:5173`。

手动启动：

```bash
cd backend && uv run uvicorn main:app --reload --port 8000
cd frontend && npm install && npm run dev
```

**3. 验证：**输入 `600036` 或“招商银行”开始分析；`/reports` 查看历史。后端根路径 `/` 为健康检查。

**4. 测试：**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
```

## 系统要求与验证范围

| 项目 | 状态 |
|---|---|
| 运行环境 | Python ≥ 3.11（`backend/pyproject.toml` 声明）；Node.js 版本未在仓库中锁定 |
| LLM 端点 | 需支持 OpenAI Responses API；Chat Completions 仅作应急回滚，会禁用 `web_search` 与结构化输出主路径 |
| 后端测试 | 仓库包含 11 个 pytest 测试文件，含真实报告 golden 夹具 |
| 前端测试 | 当前没有测试文件 |
| 界面截图 | 待补充 |

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 健康检查 |
| GET | `/api/config` | 前端运行配置（深度思考默认状态） |
| POST | `/api/report/stream` | 报告直出 SSE：`connected → phase → thought → section_done×N → report_saved → done` |
| GET | `/api/reports` | 历史列表（判据：目录含 `dashboard.json`） |
| GET | `/api/reports/{code}` | 单份看板数据 |
| GET | `/api/reports/{code}/report.md` | 报告 Markdown 原文（数字核验入口） |
| POST | `/api/analyze` | 决策卡 JSON（同步） |
| GET/POST | `/api/analyze/stream` | 决策卡 JSON（SSE 流式） |

## 目录结构

```text
├── A股投资决策系统执行手册.md    # 硬门槛、反向清单、一票否决等规则
├── backend/
│   ├── main.py                   # FastAPI 入口
│   ├── report_service.py         # 报告直出：SSE 事件流 + 落盘 + 历史扫描
│   ├── report_generator.py       # 26 节契约与校验
│   ├── dashboard/                # 报告 → 看板变量的确定性解析层
│   ├── decision_agents/          # L0 取数、下钻工具与路由台账
│   └── tests/                    # pytest（golden 报告、流式、取数回归）
├── frontend/                     # React 19 + Vite + TypeScript
├── analysis_reports/             # 运行产物（git 忽略）
└── start.sh / start.command      # 一键启动
```

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + openai-agents（Responses API，流式） |
| 数据 | 腾讯财经 · 东方财富 · Baostock · akshare |
| 看板解析 | 纯 Python 标准库正则，无额外解析依赖 |
| 前端 | React 19 + Vite + TypeScript，无 UI / 图表组件库 |

## 已知边界

- 一次分析绑定一条 SSE：刷新或关闭页面会中断当次分析，没有作业队列。
- 同一标的的并发提交会被进程内去重拒绝。
- 历史列表只收录含 `dashboard.json` 的目录；报告产物不入 git。
- 看板与报告原文不一致时，以 `report.md` 为准。
- 输出仅为研究辅助，不构成投资建议。

## 参与贡献

欢迎提交 Issue 与 Pull Request。修改解析规则前，请先用真实报告在 `backend/tests/fixtures/` 固化 golden 用例，再放宽正则。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
