#!/usr/bin/env python3
"""BTC 现货 ETF 日度净流 → 4 个刹车指标。输出 JSON，一行。
用法: python3 etf_flow.py            （只读 docs/etf_history.csv，不写）
渠道：收件箱（环境变量 ETF_INBOX 指向的 JSON）→ Farside 全量 → SoSoValue → Farside 当月表。
任何渠道取到的数据都并入 docs/etf_history.csv（按日期去重，新的覆盖旧的），指标一律从这个历史文件算。
当天所有渠道都失败时输出 {"status": "missing", "reason": ...}，调用方按「ETF 数据缺失」处理，不估算。"""
import json, sys, io, os, math
import datetime as dt
import subprocess, pandas as pd, numpy as np

URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
URL_ALT = "https://farside.co.uk/?p=997"          # Farside 备用表（仅当月，约 15-22 行）
SOSO = "https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart"  # SoSoValue 开放接口，POST，无需 key
DEFAULT_HISTORY = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "etf_history.csv"))
HIST_COLS = ["Date", "Total", "source"]
MAX_GAP_DAYS = 4        # 相邻交易日最多隔 4 个自然日（周末 + 一天休市，历史实测上限）；更大视为断档
INBOX_MAX_AGE_DAYS = 120  # 收件箱只装最近约 40 个交易日；更早的日期多半是读错年份
INBOX_MAX_ABS = 5000      # 百万美元；历史最大单日 |净流| 约 1,374，超过视为读错
DASHES = "\u2212\u2013\u2014"  # 数学负号 / en dash / em dash（大模型抄表时常见）→ 当作 "-"
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


def parse_inbox(path, today=None):
    """收件箱：JSON 数组 [["YYYY-MM-DD", 当日 Total], ...]，单位百万美元，流出为负数。
    文件不存在 → None（不报错）；格式不对 → ValueError（调用方回落到网络渠道）。
    Total 为 null / 空白 / "-" / 0 的行视为休市或未更新，跳过（与 SoSoValue 渠道的 0 处理一致）。"""
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        rows = json.load(fh)
    if not isinstance(rows, list):
        raise ValueError("收件箱不是 JSON 数组")
    today = pd.Timestamp(today) if today is not None else pd.Timestamp(dt.datetime.now(dt.timezone.utc).date())
    latest_ok, earliest_ok = today + pd.Timedelta(days=1), today - pd.Timedelta(days=INBOX_MAX_AGE_DAYS)
    vals = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2 or not isinstance(row[0], str):
            raise ValueError(f"收件箱行格式不对: {row!r}"[:80])
        d = pd.to_datetime(row[0].strip(), format="%Y-%m-%d")
        if d > latest_ok or d < earliest_ok:
            raise ValueError(f"收件箱日期超出范围: {row[0]}")
        if d.weekday() >= 5:                          # Farside 只列交易日，周末日期说明读错了
            raise ValueError(f"收件箱有周末日期: {row[0]}")
        v = row[1]
        if isinstance(v, str):
            v = v.translate({ord(c): "-" for c in DASHES}).strip()
        if v is None or (isinstance(v, str) and v.strip("-") == ""):
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ValueError(f"收件箱数值不对: {row!r}"[:80])
        x = float(v) if not isinstance(v, str) else num(v)
        if not math.isfinite(x) or abs(x) > INBOX_MAX_ABS:
            raise ValueError(f"收件箱数值不对: {row!r}"[:80])
        if x == 0:
            continue
        vals[d] = x                                   # 同一日期重复时以后出现的为准
    if not vals:
        raise ValueError("收件箱没有有效行")
    return pd.DataFrame(sorted(vals.items()), columns=["Date", "Total"])


def load_history(path):
    """读 etf_history.csv（Date, Total, source）。文件不存在 → 空表；文件损坏 → 抛异常（调用方不覆盖它）。"""
    if not path or not os.path.isfile(path):
        return pd.DataFrame({"Date": pd.Series(dtype="datetime64[ns]"), "Total": pd.Series(dtype=float),
                             "source": pd.Series(dtype=str)})
    h = pd.read_csv(path, dtype={"source": str})
    if set(HIST_COLS) - set(h.columns):
        raise ValueError(f"{os.path.basename(path)} 缺列，应为 {HIST_COLS}")
    h["Date"] = pd.to_datetime(h["Date"], format="%Y-%m-%d")
    h["Total"] = h["Total"].astype(float)
    h["source"] = h["source"].fillna("")
    return h[HIST_COLS].dropna(subset=["Date", "Total"]).sort_values("Date").reset_index(drop=True)


def merge_history(hist, new, source):
    """新数据并入历史：按日期去重，新的覆盖旧的，按日期从旧到新。"""
    n = new[["Date", "Total"]].copy()
    n["source"] = source
    m = n if hist.empty else pd.concat([hist[HIST_COLS], n], ignore_index=True)
    return m.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)


def save_history(h, path):
    """原子写入，避免半截文件。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    out = h.assign(Date=h["Date"].dt.strftime("%Y-%m-%d"), Total=h["Total"].round(2))[HIST_COLS]
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)


def contiguous_tail(t):
    """只取最后一段没有断档的历史（相邻两行间隔 ≤ MAX_GAP_DAYS 个自然日）。
    断档之前的数据不参与指标，避免把几周前的旧数据和新数据拼成「20 日」窗口。"""
    gaps = t["Date"].diff().dt.days
    cut = gaps.index[gaps > MAX_GAP_DAYS]
    return t.loc[cut[-1]:].reset_index(drop=True) if len(cut) else t.reset_index(drop=True)


def metrics(t, src):
    """从历史（Date, Total[, source]，按日期从旧到新）算 4 个刹车指标。规则与阈值同 v3，未改。
    只用最后一段连续的历史；样本不足 25 行标 partial；不足 20 行时 20 日占比为 None（不刹车、不估算）。"""
    full_start = t["Date"].iloc[0]
    t = contiguous_tail(t)
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
        "source": t["source"].iloc[-1] if "source" in t else src,
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
    if t["Date"].iloc[0] != full_start:              # 历史有断档：注明指标从哪天起算
        out["window_start"] = t["Date"].iloc[0].strftime("%Y-%m-%d")
    if in20 is not None and in20 <= 10:
        out["brake"].append("熊市节奏")
    if week_sum > 2500:
        out["brake"].append("资金过热(需再核 BTC 距50日高<10%)")
    if weeks >= 5 and not short:
        out["brake"].append("波段末端")
    if streak >= 5 and sign > 0 and in20 is not None and in20 >= 13:
        out["regime"] = "牛市节奏"
    return out


def compute(history=None, write=False):
    """返回 dict；失败返回 {"status":"missing",...}，不抛异常。
    history：etf_history.csv 路径（默认 docs/etf_history.csv）；write=True 时把当天取到的数据并入并写回。"""
    history = history or DEFAULT_HISTORY
    errs = []
    try:
        new, src = None, None
        try:
            t = parse_inbox(os.environ.get("ETF_INBOX"))
            if t is not None:
                new, src = t, "inbox"
        except Exception as e:
            errs.append(f"inbox {str(e)[:60]}")
        if new is None:
            try:
                new, src = fetch_all()
            except Exception as e:
                errs.append(str(e)[:120])
        if new is None:                               # 当天没有任何渠道取到数据 → 缺失，不用旧历史顶替
            raise RuntimeError(" | ".join(errs))
        try:
            hist, hist_ok = load_history(history), True
        except Exception as e:                        # 历史文件损坏：只用当天数据算，且不覆盖它
            hist, hist_ok = load_history(None), False
        t = merge_history(hist, new, src)
        if write and hist_ok:
            save_history(t, history)
        out = metrics(t, src)
        out["history_rows"] = len(t)
        if not hist_ok:
            out["history_error"] = "etf_history.csv 读取失败，未写回"
        return out
    except Exception as e:  # noqa
        return {"status": "missing", "reason": str(e)[:120]}


def main():
    print(json.dumps(compute(), ensure_ascii=False))


if __name__ == "__main__":
    main()
