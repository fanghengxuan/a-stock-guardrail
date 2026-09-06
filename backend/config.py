"""运行时配置：从环境变量 / .env 读取 OpenAI 兼容端点，并全局设置 openai-agents SDK。

API 模式由环境变量 LLM_API_MODE 控制——**默认 responses**：系统设计主线（LLM 下钻取数 +
内置 web_search + output_type）全依赖 Responses API，不许静默回退。
- responses（默认）：走 OpenAI Responses API，支持内置 web_search 等 hosted tool；
- chat_completions：仅供应急回滚的第三方端点兼容格式——将禁用 web_search 与 output_type 主路径
  （SDK 对 ChatCompletions 拒绝 hosted tool，见 chatcmpl_converter.py:987）。

参考官方文档：https://openai.github.io/openai-agents-python/
        官方示例：examples/model_providers/custom_example_global.py
"""
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import set_default_openai_api, set_default_openai_client, set_tracing_disabled

# 从 .env 加载（开发期；生产由环境变量直接注入）
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")

# 端点必须显式配置（无默认值）：缺失时宁可在启动即报错，也不静默回退到错误端点。
if not OPENAI_BASE_URL:
    raise RuntimeError(
        "OPENAI_BASE_URL 未设置：请在 backend/.env 配置 OpenAI 兼容端点（无默认值）。")

# API 模式开关：默认 responses（系统主线依赖）；env 变量仅作应急回滚，无需改代码。
LLM_API_MODE = os.getenv("LLM_API_MODE", "responses")  # responses（默认）| chat_completions（应急回滚）
assert LLM_API_MODE in ("chat_completions", "responses")

# 深度思考开关（默认关闭）。DeepSeek v4 默认开思考：报告直写前仅产思维链 token、
# 正文零字节，前端实时渲染会长时间无输出，静默熔断还可能误杀活流。
# 关闭 → reasoning.effort=none（无思维链 token、秒级出字、逐节点亮）；
# 开启 → reasoning.effort=high（深度思考，更缜密但慢，需配大 max_tokens）。
# 参考：https://api-docs.deepseek.com/zh-cn/api/create-response（effort ∈ none/minimal/low/medium/high/xhigh/max）
LLM_DEEP_THINK = os.getenv("LLM_DEEP_THINK", "false").strip().lower() in ("1", "true", "yes", "on")
# Responses API reasoning 配置（映射到请求体 reasoning 字段；None=不传=端点默认思考行为）。
LLM_REASONING = {"effort": "none"} if not LLM_DEEP_THINK else {"effort": "high"}
# 深度思考关时无思维链 token，max_tokens 只需容纳正文（≈20-25K tokens）；开时须留思维链空间。
LLM_MAX_TOKENS = 65536 if LLM_DEEP_THINK else 32768


# 全局设置自定义 OpenAI 兼容客户端。
# import 本模块即生效，所有 Agent 默认使用此 client + LLM_API_MODE 指定的 API 格式。
_client = AsyncOpenAI(
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY or "missing-api-key",
    # 客户端超时 200s：超过会让单次挂起吃满 SSE 分析窗口、fallback 轮不到；
    # 真挂起（>200s 无响应）按时转入 fallback。
    timeout=200.0,
    max_retries=1,  # 网关瞬时 RST 时快速重试即可，SDK 默认 2 次白耗窗口
)
set_default_openai_client(client=_client, use_for_tracing=False)  # 自定义 client 不用于 tracing
set_default_openai_api(LLM_API_MODE)
set_tracing_disabled(disabled=True)


def has_api_key() -> bool:
    """是否已配置 OPENAI_API_KEY。"""
    return bool(OPENAI_API_KEY)
