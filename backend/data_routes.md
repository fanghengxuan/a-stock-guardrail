# 数据路由文档

> 本文件只记录"方法"，验证证据在 data_routes_state.json（自动维护）。
> 路径变更时更新本文件并 git commit；端点勘误必须写进对应小节的"勘误"。

## 行情（必取，代码层）
- 主路径：腾讯 qt.gtimg.cn + 东财 push2 + Baostock，cross_validate 三源（容差1%）
- 校验：内置（价格/PE 三源、PB/ROE 双源）

## 财务（必取，代码层）
- 主路径：东财 datacenter RPT_LICO_FN_CPD → Baostock profit 备
- 校验：双源交叉（扣非口径差异注意）

## 股息率（必取，代码层）
- 主路径：Baostock 近12月派息合计 ÷ 价格（TTM 口径）
- 勘误：TTM=近12个月全部派息（含中期+末期），只算末期会低估一半（2026-07 参考文件事故）

## 融资融券
- [P1] 东财 datacenter-web reportName=RPTA_WEB_RZRQ_GGMX，filter 列名 SCODE/DATE（非 SECURITY_CODE），合计字段实为 RZRQYE（RZYE+RQYE），走 em_get；单股全历史+3/5/10日窗口，144ms
- [P2] akshare stock_margin_detail_sse/szse（沪深分开）；⚠️ 子集路由：无融券余额/合计列（仅融资余额列），降级时下游容忍缺字段
- 互校验：P1/P2 均存活且取硬门槛相关字段时双取对照，容差1%（实测逐分一致）

## 银行资产质量
- 单源声明：无独立第二路（新浪系无此字段），不造假冗余；阈值合理性检查（npl 0-5%、coverage 50-1000%）
- 勘误：RISK_COVERAGE 字段恒空勿用

## 新闻舆情
- [P1] ak.stock_news_em（近7日，≤10条）
- 单源声明：定性数据无量化校验；来源标注必须保留

## 行业估值
- [P1] push2 clist 直连（行业列表 fs=m:90+t:2；成分 fs=b:BKxxxx，f9 动态PE），全程走 em_get 节流
- 勘误：本机对 push2 主域遭 WAF 间歇 RST，现实际走 push2delay.eastmoney.com（行情延迟约15min，日级决策不受影响）；drilldown_tools._PUSH2 常量在封禁解除后可切回 push2
- 勘误：akshare 行业板块封装（stock_board_industry_*_em）本网络稳定断连——已列退役区，禁用

## 52周高低
- [P1] baostock query_history_k_data_plus 250日
- [P2] 东财 push2his kline（快3倍；⚠️复权基准与 P1 不同，混用须统一，需 ut+Referer+cookie）

## 公告
- [P1] np-anotice-stock.eastmoney.com/api/security/ann?stock_list=<code>（分类码 columns.column_code）
- [P2] ak.stock_zh_a_disclosure_report_cninfo（巨潮；⚠️ 无类型码列，负面筛选降级为标题关键词）
- [P3] ak.stock_notice_report（全市场日更表本地过滤；慢 ~1.5s/日，仅回看近5自然日，作时效性兜底）
- 勘误：/api/getAnns 端点不存在（返回0字节）——已列退役区，勿用

## 研报
- 勘误：akshare 封装丢弃 ratingChange 列 → P1 结果 rating_change 恒 None；真值需直连 reportapi（未列路由，二期候选）
- 备注：P1 与直连 reportapi 同一后端，不构成独立路由，不列 P2；与必取 RPT_WEB_RESPREDICT 互补（重叠约30%）

## F10（暂未接入，二期候选）
- 路径：emweb.securities.eastmoney.com/PC_HSF10/*/PageAjax（⚠️ PC_F10 已废弃）

## 研报(专用工具UNA时备援)
- [P1] tool=web_search;query=大华股份 002236 研报 评级 目标价 2026 [auto·试用 2026-09-05]

---
## 退役区（自动移入，人工可复活）
- 行业估值·akshare 行业板块封装：本网络稳定断连（2026-09-05 实测 3 次 RemoteDisconnected）
- 公告·np-anotice-stock/api/getAnns：端点不存在，返回 0 字节（2026-09-05）
- 东财 F10·PC_F10/* 路径：已废弃，返回错误页（2026-09-05）
- 银行资产质量·ak.stock_financial_analysis_indicator_em（字段：NONPERLOAN/BLDKBBL/NET_INTEREST_MARGIN/FIRST_ADEQUACY_RATIO）（退役 2026-09-06：UNA）
- 研报·ak.stock_research_report_em（后端 reportapi.eastmoney.com/report/list）（退役 2026-09-06：UNA）
