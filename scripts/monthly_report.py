#!/usr/bin/env python3
"""月报生成：汇总 docs/log.csv 中某月的日报记录 → docs/monthly-YYYY-MM.md
用法：python3 scripts/monthly_report.py --month 2026-09 [--log docs/log.csv] [--out docs]
数据部分由脚本生成；「月度策略」由 routine 或你按模板填写，不改数字。"""
import argparse, os, sys, time
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from daily_check import cycle, EXIT_ARM, EXIT_HARD, REENTRY_OPEN, REENTRY_HARD

ap = argparse.ArgumentParser()
ap.add_argument("--month", default=time.strftime("%Y-%m"))
ap.add_argument("--log", default="docs/log.csv")
ap.add_argument("--out", default="docs")
a = ap.parse_args()
df = pd.read_csv(a.log, parse_dates=["date"])
m = df[df.date.dt.strftime("%Y-%m") == a.month].sort_values("date")
if m.empty:
    sys.exit(f"log 中没有 {a.month} 的记录")
f = lambda x: "—" if pd.isna(x) else f"{x:,.0f}"
first, last = m.iloc[0], m.iloc[-1]
cy = cycle(last.date.strftime("%Y-%m-%d"))
def pct(a_, b_): return "—" if pd.isna(a_) or pd.isna(b_) or a_ == 0 else f"{(b_/a_-1)*100:+.1f}%"
states = m.btc_state.value_counts().to_dict()
brake_days = (m.brake.fillna("") != "").sum()
brake_kinds = m.brake.fillna("").str.split("/").explode().value_counts().drop("", errors="ignore").to_dict()
etf_sum = m.etf_day.sum(skipna=True)
actions = m.action_btc.value_counts().head(5).to_dict()
lev_days = m.overlay.str.contains("可持").sum()
# 未来 90 天里程碑
nxt = []
for lab, d in (("合约窗口开放", cy["lev_open"]), ("出场武装", cy["exit_arm"]), ("硬出场", cy["exit_hard"]),
               ("再入场窗口开放", cy["reentry_open"]), ("硬买回", cy["reentry_hard"]), ("下一减半(估)", cy["next_halving"])):
    if d:
        days = (pd.Timestamp(d) - last.date).days
        if 0 <= days <= 90:
            nxt.append(f"{d} {lab}（{days} 天后）")
md = f"""# 月报 {a.month}（数据：{first.date.date()} → {last.date.date()}，{len(m)} 个交易日记录）

## 1. 价格与趋势
- BTC：{f(first.btc_close)} → {f(last.btc_close)}（{pct(first.btc_close, last.btc_close)}）；月内最高 {f(m.btc_close.max())} / 最低 {f(m.btc_close.min())}
- ETH：{f(first.eth_close)} → {f(last.eth_close)}（{pct(first.eth_close, last.eth_close)}）
- BTC 状态天数：{states}
- 月末 EMA50（失效/参考）：{f(last.btc_ema50)}

## 2. ETF 资金流
- 当月净流入合计：{etf_sum:+.1f} 亿美元；月末 20 日流入 {last.etf_in20 if not pd.isna(last.etf_in20) else '—'}/20
- 刹车命中 {brake_days} 天：{brake_kinds or '无'}

## 3. 周期位置
- 月末：减半后 {cy['months']:.1f} 月，阶段「{cy['phase']}」；合约窗口 {'开放' if cy['lev_ok'] else cy['lev_open'] + ' 起'}
- 未来 90 天里程碑：{'；'.join(nxt) or '无'}
- 下一出场武装 {cy['exit_arm']} / 硬出场 {cy['exit_hard']}；再入场窗口 {cy['reentry_open']}–{cy['reentry_hard']}

## 4. 系统给出的动作（当月）
- BTC 动作分布：{actions}
- 合约「可持1x」天数：{lev_days}；月末合约状态：{last.overlay}
- 月末资金费率年化：{'—' if pd.isna(last.funding_annual) else f'{last.funding_annual:.0f}%'}

## 5. 月度策略（按模板填写，不改上面的数字）
- 下月阶段与武装状态：
- 现货：持有 / 可入场 / 出场武装 —— 依据第 3 节
- 合约上限：0x / 1x —— 依据第 3、4 节（窗口 + ETF + 费率）
- 发钱桶：从长大桶搬入比例 ___%（牛市默认 10% 利润）
- 彩票桶：本月补充 ___（年上限内）
- 需要人工确认的事：减半日估算是否校正；周期底/顶实测日期是否更新
"""
os.makedirs(a.out, exist_ok=True)
p = os.path.join(a.out, f"monthly-{a.month}.md")
open(p, "w").write(md)
print(md)
