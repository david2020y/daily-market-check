# research — 回测脚本（2026-09-22）
- bt.py：取 Bitstamp BTC/USD 日线；v1 规则（等回踩 + 2ATR 追踪）回测与参数邻域
- bt2.py：7 种出场变体对比（结论：顺势进 / 破 EMA50 出 最稳）
- bt3.py：入场拉伸分桶、波动率目标仓位、ETF 刹车叠加（2024→）
- 周期层、合约窗口、分批出场的测试代码见对话记录，可让 Claude Code 按 CLAUDE.md 的结论复现
运行：python3 research/bt.py（需 pandas numpy，联网取数）
