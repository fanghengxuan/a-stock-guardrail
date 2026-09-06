# Guardrail

> A 股投资决策辅助系统 —— Agentic 多源取数 + LLM 流式直写分层研究报告 + 确定性解析成"银行审批单"式决策备忘录。
> **第一原则:防止本金永久性损失。**

## 工作方式

一次分析(约 2–5 分钟,全程流式):

```
输入代码/名称
  → L0 数据底稿:多源并发取数(腾讯/东财/Baostock/akshare)交叉验证 + 下钻 Agent 循环(≤6 轮、7 类工具)
  → LLM 按《执行手册》流式直写分层 Markdown 报告(L0 数据底稿 8 节 → L2 分析计算 16 节 → L3 决策输出 2 节,共 26 节,validate_report 校验)
  → 落盘 analysis_reports/<代码>-<名称>/{report_direct.md, dashboard.json}
  → dashboard 确定性解析器(纯标准库正则,零 LLM)从报告原文提取全部看板变量
  → 前端 SSE 逐节点亮进度,完成后渲染六段决策备忘录(决策头/估值/风险/论证/操作/明细)
```

- **报告是唯一真相源**:看板每个数字由确定性正则从报告原文捕获(100% 忠实,不经 LLM 二次提取或换算);解析失败按三层降级(结构化 → 回退链 → 原文透出)——**永不空屏、永不造数**。
- **历史秒开**:`/api/reports` 扫描落盘的 `dashboard.json`,点开纯渲染、不再调模型。
- **视觉契约**:前端为银行审批单风格备忘录版式(宋体标题/台账数字/印章/估值标尺/红涨绿跌),**零 UI 组件库**,版式全部集中在 `frontend/src/styles/memo.css`。
- **全程流式**:对 LLM 的一切调用走流式(SSE),无一次性大请求,弱网/高延迟网关下不积压。

## 快速开始

前置:Python ≥ 3.11 + [uv](https://docs.astral.sh/uv/),Node.js(React 19 / Vite 8)。

**1. 配置 `backend/.env`**(参照 [`backend/.env.example`](backend/.env.example)):

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | API Key(OpenAI 兼容端点) |
| `OPENAI_BASE_URL` | OpenAI 兼容端点,必填——缺失启动即报错 |
| `LLM_MODEL` | 模型名,如 `deepseek-v4-flash` |
| `LLM_API_MODE` | 必须 `responses`(设计依赖 Responses API,禁止静默回退) |
| `LLM_DEEP_THINK` | 深度思考开关,默认关(`thinking:disabled` → 秒级出字逐节点亮) |

**2. 启动:**

```bash
./start.sh            # 后端 :8000 + 前端 :5173(venv 失效时自动重建)
```

或手动:

```bash
cd backend && uv run uvicorn main:app --reload --port 8000
cd frontend && npm install && npm run dev
```

打开 http://localhost:5173 输入代码或名称(如 `600036` / 招商银行)开始分析;`/reports` 查看历史。

**3. 测试:**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
cd frontend && npx vitest run
```

## HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/report/stream` | 报告直出分析 SSE:`connected → phase → thought → section_done×N → heartbeat → report_saved → done`(done 附 DashboardData 全文) |
| GET | `/api/reports` | 历史列表(判据=目录含 `dashboard.json`,按修改时间降序) |
| GET | `/api/reports/{code}` | 单份 DashboardData(6 位代码校验,防路径穿越) |
| GET | `/api/reports/{code}/report.md` | 报告 Markdown 原文(看板数字核验入口) |
| GET | `/api/config` | 前端运行配置(深度思考默认状态回显) |
| POST | `/api/analyze` | 决策卡 JSON 报告(同步,完整 schema) |
| GET/POST | `/api/analyze/stream` | 决策卡 JSON 报告(SSE 流式) |

## 目录结构

```
├── A股投资决策系统执行手册.md      # 核心规则手册:硬门槛/反向清单/一票否决/状态机等(直写 prompt 的唯一标准)
├── CLAUDE.md                       # 项目规则与红线(供 Agent 协作)
├── analysis_reports/               # 运行产物(git 忽略):每标的一个目录
├── backend/
│   ├── main.py                     # FastAPI 入口:SSE 分析/历史/配置/决策卡端点
│   ├── config.py                   # 环境变量装配(端点/模型/深度思考)
│   ├── report_service.py           # 报告直出服务层:SSE 事件流 + 落盘 + 历史扫描
│   ├── report_generator.py         # 26 节契约 + validate_report + Markdown writer
│   ├── dashboard/                  # ★ MD → 看板变量确定性解析层(纯标准库正则)
│   │   ├── scanner.py              #   节定位与切分(流式/一次性共用同一 SPEC)
│   │   └── parser.py               #   看板变量提取 + 三层降级链(数字只出自捕获组)
│   ├── decision_agents/            # L0 取数(并发/熔断/TTL 缓存)+ 下钻工具 + 路由台账
│   ├── scripts/                    # report_direct / capture_fixtures / acceptance 等
│   ├── tests/                      # pytest:解析器 golden(实跑报告)+ 流式/端点 + 取数回归
│   ├── data_routes.md              # 取数路由沉淀台账(多路由权重 + 勘误)
│   ├── pyproject.toml / uv.lock    # Python ≥3.11,uv 管理
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── pages/                  # WorkspacePage(工作台)/ HistoryPage / ReportPage
│   │   ├── sections/               # 六段备忘录组件:决策头/估值/风险/论证/操作/明细
│   │   ├── components/             # Skeletons(骨架屏)/ SourceDrawer(数据源抽屉)
│   │   ├── hooks/                  # useReportStream / useScrollSpy(逐节点亮)
│   │   ├── lib/                    # reportStream / sse / dashReducer / md-lite 等
│   │   ├── styles/memo.css         # 设计系统唯一真相(视觉契约,禁改色语义)
│   │   └── types/memo.ts           # 看板类型契约
│   ├── public/favicon.svg
│   └── package.json 等
├── docs/superpowers/               # 设计档案(plans / specs)
├── start.sh / start.command        # 一键启动
└── README.md / LICENSE / .gitignore
```

## 技术栈

| 层 | 技术 |
|------|------|
| 后端 | FastAPI + [openai-agents](https://github.com/openai/openai-agents-python)(Responses API,流式)+ deepseek-v4-flash |
| 数据 | 腾讯财经 · 东方财富 · Baostock · akshare(多源交叉验证 + 熔断 + TTL 缓存) |
| 看板解析 | 纯 Python 标准库正则(零额外依赖,golden 测试锁行为) |
| 前端 | React 19 + Vite 8 + TypeScript;无 UI 组件库 / 无图表库(版式全纯 CSS) |

## 数据源与下钻工具

多源策略:主源 + 备源交叉验证,内置熔断器、TTL 缓存、指数退避重试、UA 轮换、并发控制。最新价 / PE / 股息率与权威渠道比对偏差 < 1%。

| 数据源 | 用途 |
|------|------|
| 腾讯财经 `qt.gtimg.cn` | 行情:最新价 / PE / PB / 市值 |
| 东方财富 `push2` / `datacenter` | 财务:ROE / 净利润 / 营收 / 资金流 |
| Baostock(证券宝) | 股息率 / 分红 / 财报备源 |
| akshare | 公告 / 研报 / 银行资产质量等辅助数据 |

下钻工具(7 类,明细见 `backend/data_routes.md`):融资融券 · 银行资产质量 · 新闻舆情 · 行业估值 · 52 周高低 · 公告 · 研报。

## 已知边界

- 一次分析绑一条 SSE:整页刷新/关页会中断该次分析(无作业队列);同标的并发提交会被去重拒绝。
- 报告格式漂移由三层降级链兜底(每消化一种新实跑形态即固化进 `tests/fixtures` golden);看板与报告不一致时以 `report.md` 原文为准。
- 历史列表只收录含 `dashboard.json` 的落盘目录(报告产物一律不入 git)。

## 许可证

MIT
