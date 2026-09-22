import json, numpy as np, pandas as pd
rows=json.load(open("bitstamp.json"))
df=pd.DataFrame(rows).astype(float)
df["t"]=pd.to_datetime(df["timestamp"],unit="s"); df=df.set_index("t")[["open","high","low","close","volume"]]
df=df.iloc[:-1]  # drop today's in-progress bar
FEE=0.0015  # 0.1% fee + 0.05% slippage per side

def indicators(d, e1, e2, n=14):
    c,h,l=d.close,d.high,d.low
    out=d.copy()
    out["ema1"]=c.ewm(span=e1,adjust=False).mean(); out["ema2"]=c.ewm(span=e2,adjust=False).mean()
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    out["atr"]=tr.ewm(alpha=1/n,adjust=False).mean()
    out["bull"]=(c>out.ema1)&(out.ema1>out.ema2)
    out["stretch"]=(c-out.ema1)/out.atr
    return out

def run(d, e1=20, e2=50, k=2.0, smax=2.0, start="2013-01-01", end=None, veto=None):
    x=indicators(d,e1,e2).loc[start:end]
    o,h,l,c=x.open.values,x.high.values,x.low.values,x.close.values
    ema1,ema2,atr,bull,st=x.ema1.values,x.ema2.values,x.atr.values,x.bull.values,x.stretch.values
    v=np.zeros(len(x),bool) if veto is None else veto.reindex(x.index).fillna(False).values.astype(bool)
    pos=0; entry=stop=0; pend=None; eq=[1.0]; cash=1.0; units=0; trades=[]; expo=0
    for i in range(1,len(x)):
        # --- execute pending pullback order / stops on today's bar using yesterday's levels
        if pos==0 and pend is not None:
            lvl,stp=pend
            if l[i]<=lvl and bull[i-1]:
                fill=min(o[i],lvl)*(1+FEE); units=cash/fill; cash=0; pos=1; entry=fill; stop=stp; trades.append([x.index[i],fill,None,None,"pullback"])
            pend=None
        if pos==1:
            if l[i]<=stop:
                px=min(o[i],stop)*(1-FEE); cash=units*px; pos=0; trades[-1][2:4]=[x.index[i],px]; trades[-1].append("stop")
            elif c[i]<ema2[i]:
                px=c[i]*(1-FEE); cash=units*px; pos=0; trades[-1][2:4]=[x.index[i],px]; trades[-1].append("invalid")
            else:
                stop=max(stop,c[i]-k*atr[i]); expo+=1
        # --- signals at today's close
        if pos==0 and bull[i] and not v[i]:
            if st[i]<=smax:
                fill=c[i]*(1+FEE); units=cash/fill; cash=0; pos=1; entry=fill; stop=c[i]-k*atr[i]; trades.append([x.index[i],fill,None,None,"close"])
            else:
                pend=(ema1[i],ema1[i]-k*atr[i])
        eq.append(cash if pos==0 else units*c[i])
    eq=pd.Series(eq,index=x.index)
    if pos==1: trades[-1][2:4]=[x.index[-1],c[-1]]; trades[-1].append("open")
    t=pd.DataFrame(trades,columns=["in","pin","out","pout","how","exit"]); t["r"]=t.pout/t.pin-1
    yrs=(eq.index[-1]-eq.index[0]).days/365.25
    ret=eq.pct_change().dropna()
    bh=x.close/x.close.iloc[0]
    m=dict(cagr=eq.iloc[-1]**(1/yrs)-1, mdd=(eq/eq.cummax()-1).min(), sharpe=ret.mean()/ret.std()*np.sqrt(365) if ret.std()>0 else 0,
           expo=expo/len(x), n=len(t), win=(t.r>0).mean(), pf=t.r[t.r>0].sum()/-t.r[t.r<0].sum() if (t.r<0).any() else np.inf,
           avgw=t.r[t.r>0].mean(), avgl=t.r[t.r<0].mean(), bh_cagr=bh.iloc[-1]**(1/yrs)-1, bh_mdd=(bh/bh.cummax()-1).min(),
           bh_sharpe=x.close.pct_change().dropna().pipe(lambda r:r.mean()/r.std()*np.sqrt(365)))
    return m,t,eq
def show(m):
    return f"CAGR {m['cagr']*100:6.1f}% | MDD {m['mdd']*100:6.1f}% | Sharpe {m['sharpe']:.2f} | 持仓 {m['expo']*100:4.0f}% | 笔数 {m['n']:3d} | 胜率 {m['win']*100:4.0f}% | 盈亏比 {m['pf']:.2f} | 均盈 {m['avgw']*100:5.1f}% 均亏 {m['avgl']*100:5.1f}%"
if __name__=="__main__":
    print("== 基准 2013-01 → 2026-09 ==")
    m,t,eq=run(df); print("规则(回踩)  ",show(m)); print(f"买入持有    CAGR {m['bh_cagr']*100:6.1f}% | MDD {m['bh_mdd']*100:6.1f}% | Sharpe {m['bh_sharpe']:.2f}")
    m2,_,_=run(df,smax=99); print("规则(不等回踩)",show(m2))
    print("\n== 分时段 ==")
    for a,b in [("2013-01-01","2016-12-31"),("2017-01-01","2020-12-31"),("2021-01-01","2023-12-31"),("2024-01-01","2026-09-21")]:
        m,_,_=run(df,start=a,end=b); print(a[:4],"-",b[:4],show(m),f"| B&H CAGR {m['bh_cagr']*100:.0f}% MDD {m['bh_mdd']*100:.0f}% Sh {m['bh_sharpe']:.2f}")
    print("\n== 参数邻域（2013→2026）==")
    for e1,e2 in [(10,30),(15,40),(20,50),(25,60),(30,100),(50,200)]:
        m,_,_=run(df,e1=e1,e2=e2); print(f"EMA{e1}/{e2:<3}",show(m))
    for k in [1.0,1.5,2.0,2.5,3.0]:
        m,_,_=run(df,k=k); print(f"止损 {k}×ATR ",show(m))
    for s in [1.0,1.5,2.0,3.0]:
        m,_,_=run(df,smax=s); print(f"拉伸阈值 {s} ",show(m))
    t.to_csv("trades.csv"); eq.to_csv("equity.csv")
