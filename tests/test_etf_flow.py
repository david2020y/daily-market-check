"""etf_flow 收件箱 / 历史合并 / 样本不足 的单元测试。不联网。
运行：python3 -m unittest discover -s tests -v
"""
import json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import pandas as pd  # noqa: E402
import etf_flow as ef  # noqa: E402


TODAY = pd.Timestamp("2026-10-13")                       # 收件箱解析测试固定「今天」
LAST_BDAY = pd.Timestamp.today().normalize() - pd.offsets.BDay(1)  # compute() 用真实今天，测试日期跟着走


def trading_days(n, end=None):
    return list(pd.bdate_range(end=end or LAST_BDAY, periods=n))


class TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write_json(self, name, obj):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return p


class ParseInbox(TmpDir):
    def test_negative_numbers_and_order(self):
        p = self.write_json("etf.json", [["2026-10-09", 21.1], ["2026-10-07", -484.9],
                                         ["2026-10-08", "(244.1)"], ["2026-10-12", "-65.4"]])
        t = ef.parse_inbox(p, today=TODAY)
        self.assertEqual(t["Date"].dt.strftime("%Y-%m-%d").tolist(),
                         ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"])
        self.assertEqual(t["Total"].tolist(), [-484.9, -244.1, 21.1, -65.4])

    def test_missing_file_returns_none(self):
        self.assertIsNone(ef.parse_inbox(os.path.join(self.dir, "nope.json")))
        self.assertIsNone(ef.parse_inbox(None))
        self.assertIsNone(ef.parse_inbox(""))

    def test_skips_holiday_markers(self):
        p = self.write_json("etf.json", [["2026-10-06", "-"], ["2026-10-07", None], ["2026-10-08", 0],
                                         ["2026-10-09", "\u2014"], ["2026-10-12", 5.5]])
        t = ef.parse_inbox(p, today=TODAY)
        self.assertEqual(len(t), 1)
        self.assertEqual(t["Total"].iloc[0], 5.5)

    def test_rejects_malformed(self):
        bad = [
            {"2026-10-12": 1.0},                    # 不是数组
            [["2026-10-12"]],                       # 少一列
            [["12 Oct 2026", 1.0]],                 # 日期格式不对
            [["2026-10-12", "abc"]],                # 数值不对
            [["2026-10-12", True]],                 # 布尔值
            [["2099-01-01", 1.0]],                  # 未来日期
            [["2025-10-13", 1.0]],                  # 超过 120 天：多半读错年份
            [["2026-10-10", 1.0]],                  # 周六：Farside 不会有
            [["2026-10-12", 98765.0]],              # 超出合理范围
            [["2026-10-12", "-"]],                  # 一行有效数据都没有
            [],
        ]
        for i, obj in enumerate(bad):
            with self.subTest(case=obj):
                with self.assertRaises(ValueError):
                    ef.parse_inbox(self.write_json(f"bad{i}.json", obj), today=TODAY)

    def test_unicode_minus_and_bom(self):
        p = os.path.join(self.dir, "bom.json")
        with open(p, "w", encoding="utf-8-sig") as fh:
            json.dump([["2026-10-07", "\u2212484.9"], ["2026-10-08", "1,234.5"]], fh)
        t = ef.parse_inbox(p, today=TODAY)
        self.assertEqual(t["Total"].tolist(), [-484.9, 1234.5])

    def test_rejects_invalid_json(self):
        p = os.path.join(self.dir, "broken.json")
        with open(p, "w") as fh:
            fh.write("[[\"2026-10-12\", 1.0")
        with self.assertRaises(ValueError):
            ef.parse_inbox(p, today=TODAY)


class MergeHistory(TmpDir):
    def test_dedupe_new_overrides_old(self):
        old = pd.DataFrame({"Date": pd.to_datetime(["2026-10-07", "2026-10-08"]),
                            "Total": [-487.1, -244.0], "source": ["sosovalue", "sosovalue"]})
        new = pd.DataFrame({"Date": pd.to_datetime(["2026-10-08", "2026-10-09"]), "Total": [-244.1, 21.1]})
        m = ef.merge_history(old, new, "inbox")
        self.assertEqual(m["Date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-07", "2026-10-08", "2026-10-09"])
        self.assertEqual(m["Total"].tolist(), [-487.1, -244.1, 21.1])
        self.assertEqual(m["source"].tolist(), ["sosovalue", "inbox", "inbox"])

    def test_save_and_load_roundtrip(self):
        p = os.path.join(self.dir, "sub", "etf_history.csv")
        new = pd.DataFrame({"Date": pd.to_datetime(["2026-10-09", "2026-10-07"]), "Total": [21.1, -484.9]})
        ef.save_history(ef.merge_history(ef.load_history(p), new, "farside"), p)
        h = ef.load_history(p)
        self.assertEqual(list(h.columns), ["Date", "Total", "source"])
        self.assertEqual(h["Total"].tolist(), [-484.9, 21.1])
        with open(p) as fh:
            self.assertEqual(fh.readline().strip(), "Date,Total,source")


class Compute(TmpDir):
    def run_compute(self, inbox_rows=None, history_rows=None, fetch=None, write=True):
        hist = os.path.join(self.dir, "etf_history.csv")
        if history_rows is not None:
            df = pd.DataFrame(history_rows, columns=["Date", "Total"])
            df["Date"] = pd.to_datetime(df["Date"])
            ef.save_history(ef.merge_history(ef.load_history(None), df, "farside"), hist)
        env = {}
        if inbox_rows is not None:
            env["ETF_INBOX"] = self.write_json("etf.json", inbox_rows)
        fetch = fetch or mock.Mock(side_effect=RuntimeError("all channels down"))
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(ef, "fetch_all", fetch):
            if inbox_rows is None:
                os.environ.pop("ETF_INBOX", None)
            return ef.compute(history=hist, write=write), hist, fetch

    def test_inbox_used_first_and_merged_into_history(self):
        days = trading_days(40)
        hist_rows = [(d, 100.0) for d in days[:-1]]                # 历史缺最后一天
        inbox = [[d.strftime("%Y-%m-%d"), -50.0] for d in days[-3:]]  # 收件箱覆盖最后 3 天
        out, hist, fetch = self.run_compute(inbox, hist_rows)
        fetch.assert_not_called()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["source"], "inbox")
        self.assertEqual(out["data_date"], days[-1].strftime("%Y-%m-%d"))
        self.assertEqual((out["streak_days"], out["streak_dir"]), (3, "流出"))
        self.assertFalse(out["partial"])
        h = ef.load_history(hist)
        self.assertEqual(len(h), 40)
        self.assertEqual(h["Total"].tail(3).tolist(), [-50.0] * 3)  # 新的覆盖旧的

    def test_missing_inbox_falls_back_to_channels(self):
        days = trading_days(30)
        t = pd.DataFrame({"Date": days, "Total": [10.0] * 30})
        out, _, fetch = self.run_compute(inbox_rows=None, history_rows=None,
                                         fetch=mock.Mock(return_value=(t, "farside")))
        fetch.assert_called_once()
        self.assertEqual(out["source"], "farside")
        self.assertEqual(out["inflow_days_20"], 20)

    def test_bad_inbox_falls_back_to_channels(self):
        days = trading_days(30)
        t = pd.DataFrame({"Date": days, "Total": [10.0] * 30})
        out, _, fetch = self.run_compute(inbox_rows=[["oops", 1]], fetch=mock.Mock(return_value=(t, "sosovalue")))
        fetch.assert_called_once()
        self.assertEqual(out["source"], "sosovalue")

    def test_all_channels_fail_is_missing_even_with_history(self):
        days = trading_days(40)
        out, hist, _ = self.run_compute(inbox_rows=None, history_rows=[(d, 1.0) for d in days])
        self.assertEqual(out["status"], "missing")
        self.assertEqual(len(ef.load_history(hist)), 40)            # 历史未被改动

    def test_short_history_is_partial_and_conservative(self):
        days = trading_days(12)
        inbox = [[d.strftime("%Y-%m-%d"), -10.0] for d in days]    # 12 天全流出：若估算会触发熊市节奏
        out, _, _ = self.run_compute(inbox)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["partial"])
        self.assertIsNone(out["inflow_days_20"])
        self.assertNotIn("熊市节奏", out["brake"])
        self.assertNotIn("波段末端", out["brake"])

    def test_write_false_leaves_history_untouched(self):
        days = trading_days(30)
        inbox = [[d.strftime("%Y-%m-%d"), 5.0] for d in days]
        out, hist, _ = self.run_compute(inbox, write=False)
        self.assertEqual(out["status"], "ok")
        self.assertFalse(os.path.exists(hist))

    def test_corrupt_history_not_overwritten(self):
        hist = os.path.join(self.dir, "etf_history.csv")
        with open(hist, "w") as fh:
            fh.write("garbage\n1,2\n")
        days = trading_days(30)
        inbox = [[d.strftime("%Y-%m-%d"), 5.0] for d in days]
        out, _, _ = self.run_compute(inbox)
        self.assertEqual(out["status"], "ok")
        self.assertIn("history_error", out)
        with open(hist) as fh:
            self.assertEqual(fh.read(), "garbage\n1,2\n")


class HistoryGap(TmpDir):
    def test_gap_is_not_bridged(self):
        """历史在几周前断档，收件箱只有最近 13 天：不能把断档前的旧数据拼进 20 日窗口。"""
        recent = trading_days(13)
        old = trading_days(30, end=recent[0] - pd.Timedelta(days=40))   # 断档 > 4 周
        hist_rows = [(d, -10.0) for d in old]                           # 旧数据全是流出
        inbox = [[d.strftime("%Y-%m-%d"), 10.0 if i % 4 else -10.0] for i, d in enumerate(recent)]
        out, _, _ = Compute.run_compute(self, inbox, hist_rows)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["partial"])
        self.assertIsNone(out["inflow_days_20"])
        self.assertNotIn("熊市节奏", out["brake"])
        self.assertEqual(out["window_start"], recent[0].strftime("%Y-%m-%d"))

    def test_streak_stops_at_gap(self):
        old = trading_days(10, end="2026-08-14")
        new = trading_days(3, end="2026-10-09")
        t = pd.DataFrame({"Date": old + new, "Total": [5.0] * 13})
        m = ef.metrics(t, "farside")
        self.assertEqual(m["streak_days"], 3)

    def test_normal_holiday_gap_is_kept(self):
        days = [d for d in trading_days(31, end="2026-10-09") if d != pd.Timestamp("2026-09-07")]  # 周一休市：隔 4 天
        t = pd.DataFrame({"Date": days, "Total": [5.0] * len(days)})
        m = ef.metrics(t, "farside")
        self.assertNotIn("window_start", m)
        self.assertEqual(m["inflow_days_20"], 20)


class MetricsUnchanged(unittest.TestCase):
    def test_brake_rules_same_thresholds(self):
        days = trading_days(30)
        t = pd.DataFrame({"Date": days, "Total": [-10.0] * 20 + [10.0] * 10})
        m = ef.metrics(t, "farside")
        self.assertEqual(m["inflow_days_20"], 10)
        self.assertIn("熊市节奏", m["brake"])                      # in20 ≤ 10
        fri = trading_days(30, end="2026-10-09")                  # 以周五结束：本周 5 天 × 10 亿 = 50 亿
        t2 = pd.DataFrame({"Date": fri, "Total": [1000.0] * 30})
        m2 = ef.metrics(t2, "farside")
        self.assertIn("资金过热(需再核 BTC 距50日高<10%)", m2["brake"])  # 本周 > 25 亿
        self.assertIn("波段末端", m2["brake"])                      # 连续流入 ≥ 5 周
        self.assertEqual(m2.get("regime"), "牛市节奏")


# r.jina.ai 转出的两种排版（结构照抄真实页面，数字取自 Farside 2026-10）
PIPE_PAGE = """Title: Farside Investors

|  | IBIT | FBTC | GBTC |  |
| --- | --- | --- | --- | --- |
| Fee | 0.25% | 0.25% | 1.50% |  |
| 06 Oct 2026 | 122.0 | 0.0 | (3.2) | 118.8 |
| 07 Oct 2026 | (207.7) | (105.1) | (172.1) | (484.9) |
| 08 Oct 2026 | - | - | - | 0.0 |
| 09 Oct 2026 | 22.4 | (3.6) | 2.3 | 21.1 |
| Total | 65,668 | 10,525 | (27,944) | 57,112 |
| Average | 95.2 | 15.3 | (40.5) | 82.8 |
"""
CELL_PAGE = "Bitcoin ETF Flow \u2013 All Data (US$m)\nDate\t\nIBIT\n\t\nFBTC\n\t\nTotal\n\n\n" + "".join(
    f"{d}\t\n{a}\n\t\n{b}\n\t\n{t}\n\n" for d, a, b, t in [
        ("11 Jan 2024", "111.7", "227.0", "338.7"),
        ("15 Jan 2024", "-", "-", "0.0"),
        ("16 Jan 2024", "212.7", "(1,265.4)", "(1,052.7)"),
    ]) + "Total\t\n65,668\n\t\n10,525\n\t\n76,193\n"


class FarsideText(unittest.TestCase):
    def test_pipe_table(self):
        t = ef._parse_farside_text(PIPE_PAGE)
        self.assertEqual(t["Date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-06", "2026-10-07", "2026-10-09"])  # 休市行跳过
        self.assertEqual(t["Total"].tolist(), [118.8, -484.9, 21.1])

    def test_one_cell_per_line(self):
        t = ef._parse_farside_text(CELL_PAGE)
        self.assertEqual(t["Date"].dt.strftime("%Y-%m-%d").tolist(), ["2024-01-11", "2024-01-16"])
        self.assertEqual(t["Total"].tolist(), [338.7, -1052.7])

    def test_sum_mismatch_rejects_whole_page(self):
        with self.assertRaises(ValueError):
            ef._parse_farside_text(PIPE_PAGE.replace("| 21.1 |", "| 99.9 |"))

    def test_truncated_page_rejected(self):
        cut = PIPE_PAGE[:PIPE_PAGE.index("| 09 Oct 2026")] + "| 09 Oct 2026 | 22.4 | (3.6) |"   # 断在行中
        with self.assertRaises(ValueError):
            ef._parse_farside_text(cut)
        no_footer = PIPE_PAGE[:PIPE_PAGE.index("| Total |")]                                  # 断在行尾
        with self.assertRaises(ValueError):
            ef._parse_farside_text(no_footer)
        with self.assertRaises(ValueError):
            ef._parse_farside_text(PIPE_PAGE.replace("| (484.9) |", "| (484 |"))              # 半截数字

    def test_pending_latest_row_skipped(self):
        page = PIPE_PAGE.replace("| 09 Oct 2026 | 22.4 | (3.6) | 2.3 | 21.1 |", "| 09 Oct 2026 | - | (3.6) | 2.3 | (1.3) |")
        t = ef._parse_farside_text(page)
        self.assertEqual(t["Date"].max().strftime("%Y-%m-%d"), "2026-10-07")               # IBIT 未报的当天不收

    def test_curl_nonzero_exit_is_failure(self):
        cp = mock.Mock(returncode=18, stdout="partial body\n200")
        with mock.patch.object(ef.subprocess, "run", return_value=cp):
            body, code = ef._curl("https://example.com/")
        self.assertNotEqual(code, "200")

    def test_challenge_page_gives_no_rows(self):
        self.assertTrue(ef._parse_farside_text("Title: Just a moment...\nEnable JavaScript").empty)


class ChannelOrder(unittest.TestCase):
    def test_direct_403_then_jina_used_and_labelled_farside(self):
        days = trading_days(30)
        page = "| | IBIT | |\n" + "".join(f"| {d.strftime('%d %b %Y')} | 10.0 | 10.0 |\n" for d in days) + "| Total | 300 | 300 |\n"
        calls = []

        def fake_curl(url, extra=(), timeout=40, accept="text/html,application/json"):
            calls.append(url)
            if url == ef.URL:
                return "<title>Just a moment...</title>", "403"
            if url == ef.JINA + ef.URL:
                return page, "200"
            raise AssertionError(f"不该走到 {url}")

        with mock.patch.object(ef, "_curl", fake_curl):
            t, src = ef.fetch_all()
        self.assertEqual(src, "farside")
        self.assertEqual(len(t), 30)
        self.assertEqual(calls, [ef.URL, ef.JINA + ef.URL])


if __name__ == "__main__":
    unittest.main()
