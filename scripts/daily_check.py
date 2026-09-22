#!/usr/bin/env python3
"""每日看盘一键脚本：趋势层(BTC/ETH 日线) + ETF 刹车层 → 五行结论 + 日志行。
用法:
  python3 daily_check.py                       # 空仓
  python3 daily_check.py --position long       # 有多仓（只看收盘是否破 EMA50）
  python3 daily_check.py --risk 2              # 单笔风险预算 2%（默认 1%）
  python3 daily_check.py --out docs            # 另存 latest.md/latest.json（定时任务用）
  python3 daily_check.py --json                # 只输出 JSON
设计: 所有判断在脚本内完成，调用方只需照抄文本。ETF 缺失只影响 ETF 行，永不影响动作/关键价。
"""
import argparse, json, os, sys, time, subprocess
import pandas as pd, numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etf_flow  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
DAY_MS = 86_400_000


def _get(url, extra=()):
    cp = subprocess.run(["curl", "-sL", "--compressed", "-m", "30", "-A", UA, *extra, url],
                        capture_output=True, text=True)
    return json.loads(cp.stdout)


def fetch_klines(sym):
    """渠道链：Crypto.com 公共接口 → OKX → Bybit。返回 (DataFrame[t,o,h,l,c] 已收盘, 来源)。"""
    now = int(time.time() * 1000)
    errs = []
    # 1. Crypto.com（与 MCP 同源，公共接口无需 key）
    try:
        js = _get(f"https://api.crypto.com/exchange/v1/public/get-candlestick"
                  f"?instrument_name={sym}_USDT&timeframe=1D&count=300")
        rows = js["result"]["data"]
        df = pd.DataFrame(rows)[["t", "o", "h", "l", "c"]].astype(float)
        df = df[df["t"] + DAY_MS <= now]           # 剔除未收盘当日
        if len(df) >= 60:
            return df.sort_values("t").reset_index(drop=True), "crypto.com"
        errs.append(f"crypto.com rows={len(df)}")
    except Exception as e:
        errs.append(f"crypto.com {str(e)[:40]}")
    # 2. OKX（confirm=1 才是已收盘）
    try:
        js = _get(f"https://www.okx.com/api/v5/market/candles?instId={sym}-USDT&bar=1Dutc&limit=300")
        rows = [r for r in js["data"] if r[-1] == "1"]
        df = pd.DataFrame(rows).iloc[:, :5]
        df.columns = ["t", "o", "h", "l", "c"]
        df = df.astype(float)
        if len(df) >= 60:
            return df.sort_values("t").reset_index(drop=True), "okx"
        errs.append(f"okx rows={len(df)}")
    except Exception as e:
        errs.append(f"okx {str(e)[:40]}")
    # 3. Bybit
    try:
        js = _get(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={sym}USDT&interval=D&limit=300")
        df = pd.DataFrame(js["result"]["list"]).iloc[:, :5]
        df.columns = ["t", "o", "h", "l", "c"]
        df = df.astype(float)
        df = df[df["t"] + DAY_MS <= now]
        if len(df) >= 60:
            return df.sort_values("t").reset_index(drop=True), "bybit"
        errs.append(f"bybit rows={len(df)}")
    except Exception as e:
        errs.append(f"bybit {str(e)[:40]}")
    raise RuntimeError(" | ".join(errs))


def trend(sym):
    try:
        df, src = fetch_klines(sym)
    except Exception as e:
        return {"status": "missing", "reason": str(e)[:120]}
    c, h, l = df["c"], df["h"], df["l"]
    ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]   # Wilder ATR14（仅供参考，不参与规则）
    close = float(c.iloc[-1])
    if close > ema20 > ema50:
        state = "顺势"
    elif close < ema20 < ema50:
        state = "逆势"
    else:
        state = "震荡"
    high50 = float(h.tail(50).max())
    return {
        "status": "ok", "source": src,
        "candle_date": pd.to_datetime(df["t"].iloc[-1], unit="ms").strftime("%Y-%m-%d"),
        "close": close, "ema20": float(ema20), "ema50": float(ema50), "atr14": float(atr),
        "state": state, "risk_pct": (close - ema50) / close * 100,      # 入场到失效的距离 = 单笔风险%
        "high50": high50, "dist_high50_pct": (high50 - close) / high50 * 100,
    }


def funding(sym):
    """币本位永续资金费率（年化%）。OKX → Bybit；失败返回 None。"""
    try:
        js = _get(f"https://www.okx.com/api/v5/public/funding-rate?instId={sym}-USD-SWAP")
        r = float(js["data"][0]["fundingRate"])
        return {"status": "ok", "source": "okx", "rate_8h": r, "annual_pct": r * 3 * 365 * 100}
    except Exception:
        pass
    try:
        js = _get(f"https://api.bybit.com/v5/market/tickers?category=inverse&symbol={sym}USD")
        r = float(js["result"]["list"][0]["fundingRate"])
        return {"status": "ok", "source": "bybit", "rate_8h": r, "annual_pct": r * 3 * 365 * 100}
    except Exception as e:
        return {"status": "missing", "reason": str(e)[:80]}


def brakes(etf, btc):
    """ETF 刹车（只否决新入场/开合约）。ETF 缺失 → []。"""
    if etf.get("status") != "ok":
        return [], None
    tags, regime = [], None
    in20, wk, weeks = etf.get("inflow_days_20"), etf["week_sum_100m_usd"], etf["consec_inflow_weeks"]
    if in20 is not None and in20 <= 10:
        tags.append("熊市节奏")
    if wk > 25 and btc.get("status") == "ok" and btc["dist_high50_pct"] < 10:
        tags.append("资金过热")
    if weeks >= 5 and not etf.get("partial"):
        tags.append("波段末端")
    if etf["streak_dir"] == "流入" and etf["streak_days"] >= 5 and in20 is not None and in20 >= 13:
        regime = "牛市节奏"
    return tags, regime


def fmt(x):
    return "—" if x is None else f"{x:,.0f}"


def key_prices(t, position, risk):
    """v2 规则（2013–2026 回测定版，不随行情改）：
    空仓·顺势 → 入场=收盘价（不等回踩），失效=EMA50；仓位% = 风险预算% ÷ 距失效%
    空仓·震荡/逆势 → 无入场；观察位 EMA20，失效 EMA50
    有仓 → 失效=EMA50；收盘<EMA50 → 「已失效→清仓」。无追踪止损
    """
    if t.get("status") != "ok":
        return {"entry": None, "invalid": None, "size_pct": None, "note": "K线缺失"}
    c, e20, e50, rp = t["close"], t["ema20"], t["ema50"], t["risk_pct"]
    if position == "long":
        return {"entry": None, "invalid": e50, "size_pct": None,
                "note": "已失效→清仓" if c < e50 else "持有"}
    if t["state"] == "顺势":
        return {"entry": c, "invalid": e50, "size_pct": risk / rp * 100 if rp > 0 else None, "note": "可入场"}
    return {"entry": None, "invalid": e50, "size_pct": None, "note": f"未顺势，观察位EMA20 {fmt(e20)}"}


def overlay(t, tags, fr):
    """1 倍币本位合约层：顺势 且 无 ETF 刹车 且 资金费年化<20% → 可持 1x；否则不持/平掉。"""
    if t.get("status") != "ok":
        return "合约 K线缺失"
    if t["state"] != "顺势":
        return "合约 不持（未顺势）"
    veto = [x for x in tags if x in ("熊市节奏", "资金过热", "波段末端")]
    if veto:
        return f"合约 不开（ETF 刹车：{'/'.join(veto)}）"
    if fr.get("status") != "ok":
        return "合约 费率缺失→不开新单"
    if fr["annual_pct"] >= 20:
        return f"合约 不开（费率年化 {fr['annual_pct']:.0f}%≥20%）"
    return f"合约 可持1x（费率年化 {fr['annual_pct']:.0f}%）"


def action(btc, eth, kb, ke, tags, position, risk):
    veto = [x for x in tags if x in ("熊市节奏", "资金过热", "波段末端")]
    parts = []
    for sym, t, k in (("BTC", btc, kb), ("ETH", eth, ke)):
        if t.get("status") != "ok":
            parts.append(f"{sym} K线缺失")
        elif position == "long":
            parts.append(f"{sym} {k['note']}")
        elif k["note"] == "可入场":
            if veto:
                parts.append(f"{sym} 无操作（ETF 刹车：{'/'.join(veto)}）")
            else:
                parts.append(f"{sym} 可入场（{risk:g}%风险→仓位 {k['size_pct']:.1f}%）")
        else:
            parts.append(f"{sym} 无操作")
    return "；".join(parts)


def reason(btc, eth, etf, tags, fr):
    ok = [t for t in (btc, eth) if t.get("status") == "ok"]
    if not ok:
        tr = "K线缺失"
    elif all(t["state"] == "顺势" for t in ok):
        tr = "双币价>EMA20>EMA50，顺势即可入，出场只看收盘破EMA50"
    elif all(t["state"] == "逆势" for t in ok):
        tr = "双币价<EMA20<EMA50，无入场条件"
    else:
        tr = "趋势不一致/震荡，未触发入场"
    ef = ("ETF缺失不影响趋势层" if etf.get("status") != "ok"
          else ("ETF刹车" + "/".join(tags) + "仅否决" if tags else "ETF无刹车"))
    fund = f"费率年化{fr['annual_pct']:.0f}%" if fr.get("status") == "ok" else "费率缺失"
    return f"{tr}；{ef}；{fund}。"


def render(r):
    btc, eth, etf, kb, ke = r["btc"], r["eth"], r["etf"], r["key_btc"], r["key_eth"]

    def st(sym, t):
        if t.get("status") != "ok":
            return f"{sym} K线缺失"
        return f"{sym} {t['state']}（{fmt(t['close'])}/{fmt(t['ema20'])}/{fmt(t['ema50'])}，距失效 {t['risk_pct']:.1f}%）"

    if etf.get("status") == "ok":
        d = pd.to_datetime(etf["data_date"])
        in20 = etf["inflow_days_20"]
        in20s = f"{in20}/20" if in20 is not None else "样本不足"
        etf_line = (f"连流 {etf['streak_days']}天{etf['streak_dir']}｜20日 {in20s}｜本周 {etf['week_sum_100m_usd']}亿｜"
                    f"连续流入 {etf['consec_inflow_weeks']}周｜刹车：{'/'.join(r['brake']) or '无'}"
                    f"（数据截至 {d.month}月{d.day}日，{etf['source']}）")
    else:
        etf_line = "ETF 数据缺失（三渠道均失败）｜刹车：无（不估算）"

    def kp(sym, k):
        if k["invalid"] is None:
            return f"{sym} {k['note']}"
        if k["entry"] is None:
            return f"{sym} {k['note']} / 失效 {fmt(k['invalid'])}"
        return f"{sym} 入场 {fmt(k['entry'])} / 失效 {fmt(k['invalid'])}"

    lines = [
        f"- 状态：{st('BTC', btc)}｜{st('ETH', eth)}",
        f"- ETF：{etf_line}",
        f"- 动作：{r['action']}｜{r['overlay']}",
        f"- 关键价：{kp('BTC', kb)}｜{kp('ETH', ke)}",
        f"- 理由：{r['reason']}",
    ]
    etf_streak = f"{etf['streak_dir']}{etf['streak_days']}天" if etf.get("status") == "ok" else "缺失"
    in20 = (f"{etf['inflow_days_20']}/20" if etf.get("status") == "ok" and etf["inflow_days_20"] is not None
            else ("样本不足" if etf.get("status") == "ok" else "缺失"))
    keyp = f"BTC失效 {fmt(kb['invalid'])}｜ETH失效 {fmt(ke['invalid'])}"
    log = (f"{r['date']} | BTC{btc.get('state', '缺失')} | ETH{eth.get('state', '缺失')} | {etf_streak} | {in20} | "
           f"{'/'.join(r['brake']) or '无'} | {r['action']} | {r['overlay']} | {keyp}")
    return "\n".join(lines) + f"\n日志：{log}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--position", choices=["none", "long"], default="none")
    ap.add_argument("--risk", type=float, default=1.0, help="单笔风险预算，占账户%%（默认 1）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--out", default=None, help="同时写 latest.md / latest.json 到该目录（供定时任务与简报读取）")
    a = ap.parse_args()

    btc, eth = trend("BTC"), trend("ETH")
    etf = etf_flow.compute()
    fr = funding("BTC")
    tags, regime = brakes(etf, btc)
    kb, ke = key_prices(btc, a.position, a.risk), key_prices(eth, a.position, a.risk)
    r = {"date": time.strftime("%Y-%m-%d"), "position": a.position, "risk": a.risk,
         "btc": btc, "eth": eth, "etf": etf, "funding": fr, "brake": tags, "regime": regime,
         "key_btc": kb, "key_eth": ke}
    r["overlay"] = overlay(btc, tags, fr)
    r["action"] = action(btc, eth, kb, ke, tags, a.position, a.risk)
    r["reason"] = reason(btc, eth, etf, tags, fr)
    text = render(r)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "latest.md"), "w") as f:
            f.write(f"# 每日看盘 {r['date']}\n\n{text}\n")
        with open(os.path.join(a.out, "latest.json"), "w") as f:
            json.dump(r, f, ensure_ascii=False, default=float)
    print(json.dumps(r, ensure_ascii=False, default=float) if a.json else text)


if __name__ == "__main__":
    main()
