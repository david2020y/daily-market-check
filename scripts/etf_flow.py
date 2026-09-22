#!/usr/bin/env python3
"""Farside BTC spot ETF 日度净流 → 4 个刹车指标。输出 JSON，一行。
用法: python3 etf_flow.py
失败时输出 {"status": "missing", "reason": ...}，调用方按「ETF 数据缺失」处理，不估算。"""
import json, sys, io
import subprocess, pandas as pd, numpy as np

URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
URL_ALT = "https://farside.co.uk/?p=997"          # Farside 备用表（仅当月，约 15-22 行）
SOSO = "https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart"  # SoSoValue 开放接口，POST，无需 key
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")  # 短 UA 会被 Cloudflare 403


def num(x):
    s = str(x).replace(",", "").strip()
    if s in ("-", "nan", ""):
        return np.nan
    return -float(s.strip("()")) if s.startswith("(") else float(s)


def _curl(url, extra=()):
    cp = subprocess.run(["curl", "-sL", "--compressed", "-m", "40", "-A", UA,
                         "-H", "Accept: text/html,application/json", "-H", "Accept-Language: en-US,en;q=0.9",
                         *extra, "-w", "\n%{http_code}", url], capture_output=True, text=True)
    body, _, code = cp.stdout.rpartition("\n")
    return body, code.strip()


def _parse_farside(body):
    t = pd.read_html(io.StringIO(body))[0]
    if isinstance(t.columns, pd.MultiIndex):
        t.columns = [c[-1] if "Total" not in str(c) else "Total" for c in t.columns]
        t = t.rename(columns={t.columns[0]: "Date"})
    t = t[t["Date"].astype(str).str.match(r"\d{2} \w{3} \d{4}")].copy()
    funds = [c for c in t.columns if c not in ("Date", "Total")]
    holiday = t[funds].apply(lambda row: all(str(v).strip() == "-" for v in row), axis=1)
    t = t[~holiday]
    t["Date"] = pd.to_datetime(t["Date"], format="%d %b %Y")
    t["Total"] = t["Total"].map(num)
    return t[["Date", "Total"]].dropna().sort_values("Date").reset_index(drop=True)


def fetch_all():
    """渠道链：Farside 全量 → SoSoValue API → Farside 当月备用表。返回 (DataFrame[Date,Total], 来源名)。"""
    errs = []
    # 1. Farside 全量表（Cloudflare 按 UA/TLS 指纹拦截，必须 curl+完整 UA）
    try:
        body, code = _curl(URL)
        if code == "200" and len(body) > 100_000:
            return _parse_farside(body), "farside"
        errs.append(f"farside {code}/{len(body)}")
    except Exception as e:
        errs.append(f"farside {e}")
    # 2. SoSoValue 开放接口（JSON，单位美元）
    try:
        body, code = _curl(SOSO, ("-X", "POST", "-H", "Content-Type: application/json",
                                  "-d", '{"type":"us-btc-spot"}'))
        js = json.loads(body)
        rows = js.get("data") or []
        if code == "200" and len(rows) >= 25:
            t = pd.DataFrame(rows)[["date", "totalNetInflow"]]
            t = t.rename(columns={"date": "Date", "totalNetInflow": "Total"})
            t["Date"] = pd.to_datetime(t["Date"])
            t["Total"] = t["Total"].astype(float) / 1e6
            t = t[t["Total"] != 0]  # 0 视为休市/未更新
            return t.sort_values("Date").reset_index(drop=True), "sosovalue"
        errs.append(f"sosovalue {code}/{len(rows)}")
    except Exception as e:
        errs.append(f"sosovalue {str(e)[:40]}")
    # 3. Farside 当月备用表（行数少，20 日/周数可能算不全）
    try:
        body, code = _curl(URL_ALT)
        if code == "200" and "Total" in body:
            return _parse_farside(body), "farside-monthly"
        errs.append(f"farside-alt {code}")
    except Exception as e:
        errs.append(f"farside-alt {str(e)[:40]}")
    raise RuntimeError(" | ".join(errs))


def compute():
    """返回 dict；失败返回 {"status":"missing",...}，不抛异常。"""
    try:
        t, src = fetch_all()
        f = t["Total"]
        short = len(f) < 25

        # a. 连流
        sign = np.sign(f.iloc[-1])
        streak = 0
        for v in f[::-1]:
            if np.sign(v) == sign and v != 0:
                streak += 1
            else:
                break
        # b. 20 日流入占比
        in20 = int((f.tail(20) > 0).sum()) if len(f) >= 20 else None  # None=样本不足
        # c. 本周累计（最新一行所在周，周一起）
        last = t["Date"].iloc[-1]
        monday = last - pd.Timedelta(days=last.weekday())
        week_sum = float(t.loc[t["Date"] >= monday, "Total"].sum())
        # d. 连续流入周数（含本周）
        w = t.set_index("Date")["Total"].resample("W-FRI").sum()
        w = w[w.index <= last + pd.Timedelta(days=4)]
        weeks = 0
        for v in w[::-1]:
            if v > 0:
                weeks += 1
            else:
                break

        out = {
            "status": "ok",
            "source": src,
            "partial": short,
            "data_date": last.strftime("%Y-%m-%d"),
            "latest_total_musd": round(float(f.iloc[-1]), 1),
            "streak_days": streak,
            "streak_dir": "流入" if sign > 0 else "流出",
            "inflow_days_20": in20,
            "week_sum_100m_usd": round(week_sum / 100, 2),   # 亿美元
            "consec_inflow_weeks": weeks,
            "brake": [],
        }
        if in20 is not None and in20 <= 10:
            out["brake"].append("熊市节奏")
        if week_sum > 2500:
            out["brake"].append("资金过热(需再核 BTC 距50日高<10%)")
        if weeks >= 5 and not short:
            out["brake"].append("波段末端")
        if streak >= 5 and sign > 0 and in20 is not None and in20 >= 13:
            out["regime"] = "牛市节奏"
        return out
    except Exception as e:  # noqa
        return {"status": "missing", "reason": str(e)[:120]}


def main():
    print(json.dumps(compute(), ensure_ascii=False))


if __name__ == "__main__":
    main()
