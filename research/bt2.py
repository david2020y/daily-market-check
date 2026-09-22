import numpy as np, pandas as pd
from bt import df, indicators, FEE
def run2(d, e1=20,e2=50,k=2.0,smax=99,mode="trail_close",regime="stack",exit_ema="ema2",start="2013-01-01",end=None,veto=None):
    x=indicators(d,e1,e2).loc[start:end]
    o,h,l,c=x.open.values,x.high.values,x.low.values,x.close.values
    ema1,ema2,atr,st=x.ema1.values,x.ema2.values,x.atr.values,x.stretch.values
    bull=x.bull.values if regime=="stack" else (c>ema2)
    exl=ema2 if exit_ema=="ema2" else ema1
    v=np.zeros(len(x),bool) if veto is None else veto.reindex(x.index).fillna(False).values.astype(bool)
    pos=0;cash=1.0;units=0;stop=-1;hi=0;pend=None;eq=[1.0];tr=[];expo=0
    for i in range(1,len(x)):
        if pos==0 and pend is not None:
            lvl,stp=pend
            if l[i]<=lvl and bull[i-1]:
                fill=min(o[i],lvl)*(1+FEE);units=cash/fill;cash=0;pos=1;stop=stp;hi=c[i];tr.append([x.index[i],fill,None,None])
            pend=None
        if pos==1:
            if mode!="none" and l[i]<=stop:
                px=min(o[i],stop)*(1-FEE);cash=units*px;pos=0;tr[-1][2:4]=[x.index[i],px]
            elif c[i]<exl[i]:
                px=c[i]*(1-FEE);cash=units*px;pos=0;tr[-1][2:4]=[x.index[i],px]
            else:
                expo+=1;hi=max(hi,c[i])
                if mode=="trail_close": stop=max(stop,c[i]-k*atr[i])
                elif mode=="chandelier": stop=max(stop,hi-k*atr[i])
        if pos==0 and bull[i] and not v[i]:
            if st[i]<=smax:
                fill=c[i]*(1+FEE);units=cash/fill;cash=0;pos=1;stop=c[i]-k*atr[i];hi=c[i];tr.append([x.index[i],fill,None,None])
            else: pend=(ema1[i],ema1[i]-k*atr[i])
        eq.append(cash if pos==0 else units*c[i])
    eq=pd.Series(eq,index=x.index)
    if pos==1: tr[-1][2:4]=[x.index[-1],c[-1]]
    t=pd.DataFrame(tr,columns=["in","pin","out","pout"]);t["r"]=t.pout/t.pin-1
    yrs=(eq.index[-1]-eq.index[0]).days/365.25;ret=eq.pct_change().dropna()
    return dict(cagr=eq.iloc[-1]**(1/yrs)-1,mdd=(eq/eq.cummax()-1).min(),sh=ret.mean()/ret.std()*np.sqrt(365),expo=expo/len(x),n=len(t),win=(t.r>0).mean(),pf=t.r[t.r>0].sum()/-t.r[t.r<0].sum()),eq,t
def s(m): return f"CAGR {m['cagr']*100:5.1f}% MDD {m['mdd']*100:5.1f}% Sh {m['sh']:.2f} 持仓{m['expo']*100:3.0f}% 笔{m['n']:3d} 胜{m['win']*100:3.0f}% PF {m['pf']:.2f}"
V={"A 现规则:2ATR逐日追踪+破EMA50":dict(mode="trail_close",k=2),
   "B 初始2ATR不追踪+破EMA50":dict(mode="fixed",k=2),
   "C 无ATR止损,只破EMA50出":dict(mode="none"),
   "D 无ATR止损,破EMA20出":dict(mode="none",exit_ema="ema1"),
   "E 吊灯3ATR(最高收盘-3ATR)+破EMA50":dict(mode="chandelier",k=3),
   "F 吊灯4ATR+破EMA50":dict(mode="chandelier",k=4),
   "G 只看价>EMA50进/破EMA50出":dict(mode="none",regime="ema2")}
per=[("2013-01-01","2016-12-31"),("2017-01-01","2020-12-31"),("2021-01-01","2023-12-31"),("2024-01-01","2026-09-21")]
print("全期 2013→2026（不等回踩）")
for n,kw in V.items():
    m,_,_=run2(df,**kw);print(f"{n:<28}",s(m))
print("\n全期：等回踩(拉伸≤2) 对比")
for n in ["A 现规则:2ATR逐日追踪+破EMA50","C 无ATR止损,只破EMA50出","E 吊灯3ATR(最高收盘-3ATR)+破EMA50"]:
    m,_,_=run2(df,smax=2,**V[n]);print(f"{n:<28}",s(m))
print("\n分时段 Sharpe（不等回踩）  B&H | A | C | E | G")
for a,b in per:
    x=df.loc[a:b].close.pct_change().dropna();bh=x.mean()/x.std()*np.sqrt(365)
    r=[run2(df,start=a,end=b,**V[n])[0] for n in ["A 现规则:2ATR逐日追踪+破EMA50","C 无ATR止损,只破EMA50出","E 吊灯3ATR(最高收盘-3ATR)+破EMA50","G 只看价>EMA50进/破EMA50出"]]
    print(a[:4],b[:4],f"B&H {bh:.2f} |"," | ".join(f"{m['sh']:.2f}/{m['mdd']*100:.0f}%" for m in r))
print("\n年度收益 E vs B&H")
m,eq,t=run2(df,**V["E 吊灯3ATR(最高收盘-3ATR)+破EMA50"])
y=eq.resample("YE").last().pct_change().dropna();yb=df.close.loc["2013":].resample("YE").last().pct_change().dropna()
print(pd.DataFrame({"E":y.round(2),"B&H":yb.round(2)}).T.to_string())
eq.to_csv("eq_E.csv");t.to_csv("trades_E.csv")
