# Guardrail — 项目规则

A 股投资决策系统：Agentic 取数 → LLM 流式直写 20 节 MD 报告 → 确定性解析器提取看板变量 → 银行审批单风格前端逐区块点亮。防本金永久性损失是第一原则，所有看板数字必须忠实报告原文。

## 怎么跑
- 一键：`./start.sh`（后端 :8000 + 前端 :5173）；后端 `cd backend && uv run uvicorn main:app --reload --port 8000`
- `backend/.env` 必填 `OPENAI_BASE_URL`（OpenAI 兼容端点，**无默认值，缺失启动即报错**——历史排障教训，勿加默认端点）
- 测试：后端 `cd backend && .venv/bin/python -m pytest tests/ -q`；前端 `cd frontend && npx vitest run`
- 报告产物在仓库根 `analysis_reports/`（git 忽略），不在 backend/ 下

## 技术栈
FastAPI + openai-agents（Responses API）+ deepseek-v4-flash；akshare/东财/腾讯/Baostock 多源取数；React 19 + Vite + TS，**零 UI 组件库**（版式全在 `frontend/src/styles/dashboard.css`）；解析层纯标准库正则。

## 目录与红线
- `backend/dashboard/` = 报告→DashboardData 唯一解析层。**数字只允许出自正则捕获组，禁止 LLM 二次提取或换算**（决策卡巨型 JSON 不可靠是已验证教训）。
- 报告格式漂移处理：先拿实跑报告新增 `tests/fixtures/` golden + 回归用例，再放宽正则；降级链=结构化→回退→raw 透出，**永不空屏、永不造数**。
- 对 LLM 的一切调用必须走流式（run_streamed）。网关晚高峰对非流式大请求积压挂死（44s→181s→死），流式可幸存。
- `analysis_reports/` 落盘判据：目录含 `dashboard.json` 才进历史列表；目录名 `f"{code}-{name}".rstrip("-")`（防尾杠目录）。
- 旧决策卡管线（orchestrator / `/api/analyze*` / schemas）保留兼容，**不与看板管线混改**。
- 前端颜色语义（印泥红/台账绿/琥珀/红涨绿跌）与设计 token 不得改动；不引入 recharts/radix/shadcn 等重型库。
- SSE 端点超时口径：总窗 480s、静默熔断 240s、心跳 15s；同标的进程内去重。

## 当前状态与下一步
- 2026-09-06：看板全链路已上线本地（直出+解析+流式+历史），68 后端 + 21 前端测试绿；实跑已消化 5 种报告形态漂移。
- 未做/候选：整页刷新会断分析（如需再接线要引作业队列）；下钻阶段工具轨迹 teller（二期）；存量旧报告不看板化。
- 历史设计档案在 `docs/superpowers/`（非现役，勿按其施工）。
