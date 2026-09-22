import numpy as np, pandas as pd, sys
sys.path.insert(0,"/mnt/skills/plugins/daily-market-check/scripts")
from bt import df, indicators, FEE
from bt2 import run2, s, V
# 1) 入场拉伸 vs 该笔收益（变体C）
x=indicators(df,20,50)
m,eq,t=run2(df,**V["C 无ATR止损,只破EMA50出"])
t["st"]=x.stretch.reindex(t["in"]).values
t["hold"]=(t.out-t["in"]).dt.days
b=pd.cut(t.st,[-9,1,2,3,99],labels=["≤1","1-2","2-3",">3"])
print("变体C：入场拉伸 → 笔数 / 胜率 / 均收益 / 中位持仓天")
print(t.groupby(b,observed=True).agg(n=("r","size"),win=("r",lambda r:(r>0).mean()),avg=("r","mean"),med=("r","median"),days=("hold","median")).round(3).to_string())
# 2) 波动率目标仓位（C 的日收益 × 权重）
def voltarget(eq, tv):
    r=eq.pct_change().fillna(0); px=df.close.reindex(eq.index)
    vol=px.pct_change().rolling(20).std()*np.sqrt(365)
    w=(tv/vol).clip(upper=1.0).shift(1).fillna(0)
    rr=r*w; e=(1+rr).cumprod(); yrs=(e.index[-1]-e.index[0]).days/365.25
    return e.iloc[-1]**(1/yrs)-1,(e/e.cummax()-1).min(),rr.mean()/rr.std()*np.sqrt(365)
print("\n变体C + 波动率目标仓位（20日波动>目标则减仓）")
for tv in [0.3,0.4,0.5,0.6,9]:
    c,md,sh=voltarget(eq,tv); print(f"目标年化波动 {tv if tv<9 else '不限'}: CAGR {c*100:5.1f}% MDD {md*100:5.1f}% Sh {sh:.2f}")
for a,b_ in [("2021-01-01","2023-12-31"),("2024-01-01","2026-09-21")]:
    m2,eq2,_=run2(df,start=a,end=b_,**V["C 无ATR止损,只破EMA50出"])
    c,md,sh=voltarget(eq2,0.4); print(f"  {a[:4]}-{b_[:4]} C+目标40%: CAGR {c*100:5.1f}% MDD {md*100:5.1f}% Sh {sh:.2f}")
# 3) ETF 刹车叠加 2024-01-11→
import etf_flow
raw=etf_flow.fetch_all()
e=raw if isinstance(raw,pd.DataFrame) else raw[0]
e=e.rename(columns={c:c.lower() for c in e.columns}); e["date"]=pd.to_datetime(e["date"]); e=e.sort_values("date").set_index("date")["total"].astype(float)
print("\nETF 行数",len(e),e.index.min().date(),e.index.max().date())
daily=e.reindex(pd.date_range(e.index.min(),df.index[-1])).ffill()  # 周末沿用周五值(不新增流向)
sign=(e>0).astype(int)
in20=sign.rolling(20).sum()
wk=e.resample("W-FRI").sum(); wkin=(wk>0).astype(int)
cons=wkin.groupby((wkin!=wkin.shift()).cumsum()).cumsum()*wkin   # 连续流入周(截至该周五)
cons_d=cons.reindex(daily.index).ffill()  # 用最近已完成周
in20_d=in20.reindex(daily.index).ffill()
wk_d=e.groupby(e.index.to_period("W-FRI")).cumsum().reindex(daily.index).ffill()  # 本周累计
px=df.close; hi50=df.high.rolling(50).max(); dist=(hi50-px)/hi50*100
veto=(in20_d<=10)|((wk_d>2500)&(dist.reindex(daily.index)<10))|(cons_d>=5)
veto=veto.fillna(False)
print("刹车触发日占比 %.0f%%"%(veto.loc["2024-01-11":].mean()*100), "| 熊市节奏 %.0f%%"%((in20_d<=10).loc["2024-01-11":].mean()*100),"| 过热 %.0f%%"%(((wk_d>2500)&(dist.reindex(daily.index)<10)).loc["2024-01-11":].mean()*100),"| 波段末端 %.0f%%"%((cons_d>=5).loc["2024-01-11":].mean()*100))
for n in ["A 现规则:2ATR逐日追踪+破EMA50","C 无ATR止损,只破EMA50出"]:
    m0,e0,t0=run2(df,start="2024-02-15",end="2026-09-21",**V[n]); m1,e1,t1=run2(df,start="2024-02-15",end="2026-09-21",veto=veto,**V[n])
    print(f"{n[:2]} 无刹车 {s(m0)}\n{n[:2]} 有刹车 {s(m1)}")
bh=df.close.loc["2024-02-15":"2026-09-21"];r=bh.pct_change().dropna();yrs=(bh.index[-1]-bh.index[0]).days/365.25
print(f"B&H 同期 CAGR {((bh.iloc[-1]/bh.iloc[0])**(1/yrs)-1)*100:.1f}% MDD {((bh/bh.cummax()-1).min())*100:.1f}% Sh {r.mean()/r.std()*np.sqrt(365):.2f}")
