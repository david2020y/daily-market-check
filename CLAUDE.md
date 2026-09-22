# daily-market-check — 给 Claude Code 的项目说明

## 这是什么
David 的 BTC/ETH 看盘系统 v3：日报脚本、月报脚本、TradingView 指标、回测。规则以 SKILL.md 为准，参数不随行情改。
Routine 日报是另一套东西、独立运行，本仓库不负责也不改动它；本仓库只做 TradingView 上的详细分析（指标 + 回测）。

## 目录
- scripts/daily_check.py：日报（六行结论 + log.csv）；scripts/monthly_report.py：月报；scripts/etf_flow.py：ETF 数据
- tradingview/trend_cycle_v3.pine：与日报一致的图表指标
- research/：回测脚本；docs/：每日产物（latest.md/json、log.csv、monthly-*.md）
- DECISIONS.md：决策日志（结论 + 证据），改规则前必读
- .github/workflows/daily.yml：定时任务（若已用 Claude Code Routine，二选一，删掉此文件）

## 已定结论（不要重新推翻，除非有新回测）
1. 趋势层：顺势 = 价>EMA20>EMA50；入场不等回踩；无 ATR 追踪止损；出场触发 = 收盘 < EMA50
2. 周期层：减半后 15–20 月武装出场（每破 EMA50 卖 1/3，H+19 硬清）；26–34 月再入场；其余持有不卖
3. 合约：1 倍币本位，只在 [H−18, H+15]，且顺势 + 无 ETF 刹车 + 费率年化 <20%；永不 2x
4. ETF 只做刹车（熊市节奏 / 资金过热 / 波段末端），不做油门
5. 仓位 = 风险预算% ÷ 距 EMA50%；回撤靠仓位控制，不靠止损
6. 资产结构：发钱桶 30–40% / 长大桶 50–60% / 彩票桶 5–10%

## 改动规则
- 改规则先跑回测（research/），把结果写进 SKILL.md「规则依据」，再改脚本
- 减半日估算、周期底/顶日期每年只在月报里校正一次
- 每次改动写一行到下面的「变更记录」

## 变更记录
- 2026-09-22 v1→v2：去等回踩、去 2ATR 止损、加仓位公式与费率门槛
- 2026-09-22 v2→v3：加周期层、log.csv、月报、TradingView v3 指标
- 2026-09-22 从 交易策略工作库/daily-market-check-v3.zip 覆盖仓库（脚本内容无变化），git init 并首次提交；注明 Routine 日报独立于本仓库
- 2026-09-22 Pine v3 加「下一里程碑」倒计时与「本阶段策略」行（含 H+19 硬清 / H+34 硬买回，按 SKILL 规则文字），纯展示，规则参数未动
