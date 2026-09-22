#!/usr/bin/env python3
"""每日看盘一键脚本：趋势层(BTC/ETH 日线) + ETF 刹车层 → 五行结论 + 日志行。
用法:
  python3 daily_check.py                       # 空仓
  python3 daily_check.py --position long       # 有多仓（只看收盘是否破 EMA50）
  python3 daily_check.py --risk 2              # 单笔风险预算 2%（默认 1%）
  python3 daily_check.py --mode trend          # 关闭周期层，退回 v2 纯趋势规则
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


# ---------------- 周期层（v3）：减半日历决定「哪条规则处于武装状态」 ----------------
HALVINGS = [pd.Timestamp(d) for d in ("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20")]
NEXT_HALVING_EST = pd.Timestamp("2028-04-15")   # 估算，减半前一年按区块高度校正
CYCLE_BOTTOM = "2026-06-30"                      # 本轮实测底（信息用）
EXIT_ARM, EXIT_HARD, EXIT_END = 15, 19, 20       # 减半后 15 月武装出场；19 月硬出场；20 月窗口结束
REENTRY_OPEN, REENTRY_HARD = 26, 34              # 减半后 26–34 月再入场窗口，34 月硬买回
LEV_OPEN_PREV = 26                               # 合约窗口 = [上次减半+26 月, 本次减半+15 月] = [H−22, H+15]，与再入场窗口同步开启（回测 H+26～32 等价）


def cycle(today=None):
    today = pd.Timestamp(today or time.strftime("%Y-%m-%d"))
    hs = HALVINGS + ([NEXT_HALVING_EST] if NEXT_HALVING_EST > HALVINGS[-1] else [])
    past = [h for h in hs if h <= today]
    h = past[-1]
    est = h == NEXT_HALVING_EST
    m = (today - h).days / 30.44
    if EXIT_ARM <= m < EXIT_END:
        phase = "出场武装期"
    elif EXIT_END <= m < REENTRY_OPEN:
        phase = "熊市等待期"
    elif REENTRY_OPEN <= m < REENTRY_HARD:
        phase = "再入场窗口"
    else:
        phase = "持有累积期"
    lev_ok = (m >= LEV_OPEN_PREV) or (m < EXIT_ARM)
    nxt = NEXT_HALVING_EST if h < NEXT_HALVING_EST else h + pd.DateOffset(months=48)
    base = h if m < EXIT_END else nxt          # 出场武装/硬出场属于哪次减半
    ms = lambda d, k: (d + pd.DateOffset(months=k)).strftime("%Y-%m-%d")
    return {"halving": h.strftime("%Y-%m-%d"), "halving_est": est, "months": m, "phase": phase, "lev_ok": lev_ok,
            "lev_open": ms(h, LEV_OPEN_PREV) if not lev_ok else None,
            "exit_arm": ms(base, EXIT_ARM), "exit_hard": ms(base, EXIT_HARD),
            "reentry_open": ms(h, REENTRY_OPEN), "reentry_hard": ms(h, REENTRY_HARD),
            "next_halving": nxt.strftime("%Y-%m-%d"), "cycle_bottom": CYCLE_BOTTOM}


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
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
    close = float(c.iloc[-1])
    state = "顺势" if close > ema20 > ema50 else ("逆势" if close < ema20 < ema50 else "震荡")
    high50 = float(h.tail(50).max())
    return {"status": "ok", "source": src,
            "candle_date": pd.to_datetime(df["t"].iloc[-1], unit="ms").strftime("%Y-%m-%d"),
            "close": close, "ema20": float(ema20), "ema50": float(ema50), "atr14": float(atr),
            "state": state, "risk_pct": (close - ema50) / close * 100,
            "high50": high50, "dist_high50_pct": (high50 - close) / high50 * 100}


def funding(sym):
    for url, path in ((f"https://www.okx.com/api/v5/public/funding-rate?instId={sym}-USD-SWAP", ("data", 0, "fundingRate"), ),
                      (f"https://api.bybit.com/v5/market/tickers?category=inverse&symbol={sym}USD", ("result", "list", 0, "fundingRate"))):
        try:
            js = _get(url)
            for k in path:
                js = js[k]
            r = float(js)
            return {"status": "ok", "source": url.split("/")[2], "rate_8h": r, "annual_pct": r * 3 * 365 * 100}
        except Exception:
            continue
    return {"status": "missing"}


def brakes(etf, btc):
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


def spot_action(sym, t, cy, position, risk, veto, mode):
    """返回 (动作文本, 入场价, 失效/参考价, 仓位%)。mode=trend → v2 纯趋势；mode=cycle → v3 周期武装。"""
    if t.get("status") != "ok":
        return f"{sym} K线缺失", None, None, None
    c, e50, rp = t["close"], t["ema50"], t["risk_pct"]
    below = c < e50
    size = risk / rp * 100 if rp > 0 else None
    ph = cy["phase"] if mode == "cycle" else "趋势"
    if position == "long":
        if ph == "出场武装期":
            return (f"{sym} 跌破EMA50→卖出1/3（站回再破再卖，{cy['exit_hard']}硬清仓）" if below
                    else f"{sym} 持有（出场已武装，{cy['exit_hard']}硬清仓）"), None, e50, None
        if ph == "熊市等待期":
            return f"{sym} 清仓（已过硬出场日）", None, e50, None
        if ph in ("持有累积期", "再入场窗口"):
            return f"{sym} 持有（周期持有期，趋势出场未武装）", None, e50, None
        return (f"{sym} 已失效→清仓" if below else f"{sym} 持有"), None, e50, None
    # 空仓
    if ph in ("出场武装期", "熊市等待期"):
        return f"{sym} 无操作（{ph}，{cy['reentry_open']}起可再入场）", None, e50, None
    if t["state"] != "顺势":
        tail = f"，{cy['reentry_hard']}硬买回" if ph == "再入场窗口" else ""
        return f"{sym} 无操作（未顺势{tail}）", None, e50, None
    if veto:
        return f"{sym} 无操作（ETF 刹车：{'/'.join(veto)}）", None, e50, None
    return f"{sym} 可入场（{risk:g}%风险→仓位 {size:.1f}%）", c, e50, size


def overlay(t, cy, tags, fr, mode):
    if t.get("status") != "ok":
        return "合约 K线缺失"
    if mode == "cycle" and not cy["lev_ok"]:
        return f"合约 不开（周期窗口 {cy['lev_open']} 起）"
    if t["state"] != "顺势":
        return "合约 不持/平掉（未顺势）"
    veto = [x for x in tags if x in ("熊市节奏", "资金过热", "波段末端")]
    if veto:
        return f"合约 不开（ETF 刹车：{'/'.join(veto)}）"
    if fr.get("status") != "ok":
        return "合约 费率缺失→不开新单"
    if fr["annual_pct"] >= 20:
        return f"合约 不开（费率年化 {fr['annual_pct']:.0f}%≥20%）"
    return f"合约 可持1x（费率年化 {fr['annual_pct']:.0f}%）"


def render(r):
    btc, eth, etf, cy = r["btc"], r["eth"], r["etf"], r["cycle"]

    def st(sym, t):
        if t.get("status") != "ok":
            return f"{sym} K线缺失"
        return f"{sym} {t['state']}（{fmt(t['close'])}/{fmt(t['ema20'])}/{fmt(t['ema50'])}，距EMA50 {t['risk_pct']:.1f}%）"
    if etf.get("status") == "ok":
        d = pd.to_datetime(etf["data_date"]); in20 = etf["inflow_days_20"]
        etf_line = (f"连流 {etf['streak_days']}天{etf['streak_dir']}｜20日 {in20 if in20 is not None else '—'}/20｜本周 {etf['week_sum_100m_usd']}亿｜"
                    f"连续流入 {etf['consec_inflow_weeks']}周｜刹车：{'/'.join(r['brake']) or '无'}（截至 {d.month}月{d.day}日，{etf['source']}）")
    else:
        etf_line = "ETF 数据缺失（三渠道均失败）｜刹车：无（不估算）"
    if r["mode"] == "cycle":
        lev = "开放" if cy["lev_ok"] else f"{cy['lev_open']} 起"
        cyc_line = (f"减半后 {cy['months']:.1f} 月（{cy['halving']}{'估' if cy['halving_est'] else ''}）｜阶段：{cy['phase']}｜"
                    f"合约窗口 {lev}｜出场武装 {cy['exit_arm']} / 硬出场 {cy['exit_hard']}｜下一减半≈{cy['next_halving']}（估）")
    else:
        cyc_line = "周期层关闭（--mode trend）"

    def kp(sym, k):
        if k[2] is None:
            return f"{sym} —"
        return f"{sym} 入场 {fmt(k[1])} / 失效 {fmt(k[2])}" if k[1] else f"{sym} 失效/参考 {fmt(k[2])}"
    lines = [f"- 状态：{st('BTC', btc)}｜{st('ETH', eth)}",
             f"- 周期：{cyc_line}",
             f"- ETF：{etf_line}",
             f"- 动作：{r['act_btc'][0]}；{r['act_eth'][0]}｜{r['overlay']}",
             f"- 关键价：{kp('BTC', r['act_btc'])}｜{kp('ETH', r['act_eth'])}",
             f"- 理由：{r['reason']}"]
    etf_streak = f"{etf['streak_dir']}{etf['streak_days']}天" if etf.get("status") == "ok" else "缺失"
    in20 = f"{etf['inflow_days_20']}/20" if etf.get("status") == "ok" and etf.get("inflow_days_20") is not None else "缺失"
    log = (f"{r['date']} | BTC{btc.get('state','缺失')} | ETH{eth.get('state','缺失')} | {etf_streak} | {in20} | "
           f"{'/'.join(r['brake']) or '无'} | {cy['phase']} H+{cy['months']:.1f} | {r['act_btc'][0]}；{r['act_eth'][0]} | {r['overlay']}")
    return "\n".join(lines) + f"\n日志：{log}"


def reason(btc, eth, etf, tags, fr, cy, mode):
    ok = [t for t in (btc, eth) if t.get("status") == "ok"]
    if not ok:
        tr = "K线缺失"
    elif all(t["state"] == "顺势" for t in ok):
        tr = "双币价>EMA20>EMA50"
    elif all(t["state"] == "逆势" for t in ok):
        tr = "双币价<EMA20<EMA50"
    else:
        tr = "趋势不一致/震荡"
    ph = f"周期{cy['phase']}（{'趋势出场仅在武装期生效' if mode == 'cycle' else '纯趋势'}）"
    ef = "ETF缺失不影响趋势层" if etf.get("status") != "ok" else ("ETF刹车" + "/".join(tags) + "仅否决" if tags else "ETF无刹车")
    fund = f"费率年化{fr['annual_pct']:.0f}%" if fr.get("status") == "ok" else "费率缺失"
    return f"{tr}；{ph}；{ef}；{fund}。"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--position", choices=["none", "long"], default="none")
    ap.add_argument("--risk", type=float, default=1.0, help="单笔风险预算，占账户%%（默认 1）")
    ap.add_argument("--mode", choices=["cycle", "trend"], default="cycle", help="cycle=v3 周期武装（默认）；trend=v2 纯趋势")
    ap.add_argument("--date", default=None, help="覆盖今天日期（周期层测试用）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="写 latest.md / latest.json 并追加 log.csv 到该目录")
    a = ap.parse_args()
    btc, eth = trend("BTC"), trend("ETH")
    etf = etf_flow.compute()
    fr = funding("BTC")
    cy = cycle(a.date)
    tags, regime = brakes(etf, btc)
    veto = [x for x in tags if x in ("熊市节奏", "资金过热", "波段末端")]
    r = {"date": a.date or time.strftime("%Y-%m-%d"), "mode": a.mode, "position": a.position, "risk": a.risk,
         "btc": btc, "eth": eth, "etf": etf, "funding": fr, "cycle": cy, "brake": tags, "regime": regime}
    r["act_btc"] = spot_action("BTC", btc, cy, a.position, a.risk, veto, a.mode)
    r["act_eth"] = spot_action("ETH", eth, cy, a.position, a.risk, veto, a.mode)
    r["overlay"] = overlay(btc, cy, tags, fr, a.mode)
    r["reason"] = reason(btc, eth, etf, tags, fr, cy, a.mode)
    text = render(r)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        open(os.path.join(a.out, "latest.md"), "w").write(f"# 每日看盘 {r['date']}\n\n{text}\n")
        json.dump(r, open(os.path.join(a.out, "latest.json"), "w"), ensure_ascii=False, default=float)
        logp = os.path.join(a.out, "log.csv")
        row = {"date": r["date"], "btc_close": btc.get("close"), "btc_state": btc.get("state"), "btc_ema50": btc.get("ema50"),
               "eth_close": eth.get("close"), "eth_state": eth.get("state"),
               "etf_day": round(etf["latest_total_musd"] / 100, 2) if etf.get("status") == "ok" else None,
               "etf_in20": etf.get("inflow_days_20") if etf.get("status") == "ok" else None,
               "etf_week": etf.get("week_sum_100m_usd") if etf.get("status") == "ok" else None,
               "brake": "/".join(tags), "funding_annual": fr.get("annual_pct"), "cycle_month": round(cy["months"], 1),
               "phase": cy["phase"], "action_btc": r["act_btc"][0], "action_eth": r["act_eth"][0], "overlay": r["overlay"]}
        old = pd.read_csv(logp) if os.path.exists(logp) else pd.DataFrame()
        new = pd.concat([old[old["date"] != r["date"]] if len(old) else old, pd.DataFrame([row])], ignore_index=True)
        new.to_csv(logp, index=False)
    print(json.dumps(r, ensure_ascii=False, default=float) if a.json else text)


if __name__ == "__main__":
    main()
