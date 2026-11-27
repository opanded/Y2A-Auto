"""日配额耗尽后的退避策略：不要让任务整日空转。

背景（2026-11-27/28 实测）：
  - 11-27 当天投出 51 条，第 52 次起持续返回 137022。
  - 任务被顺延到 11-28 00:30（「次日重置点」），结果仍然 137022。
    说明配额不是按自然日 00:00 清零，或账号走滚动窗口。
  - 旧逻辑此时会再顺延到 11-29 00:30，等于跳过一整天。

因此：
  - 距离下一个重置点 >20h（说明刚在重置点上失败）→ 改用递增探测 4h / 8h / 16h；
  - 否则按重置点顺延；
  - 日配额耗尽不放大节流惩罚倍数，只压静默窗口，让整条队列一起等，
    而不是逐条去撞同一堵墙。

注意：这些断言必须用「可控时钟」，否则会随一天中的时刻翻转
（例如 04:33 时距下一个 00:30 只有 19.9h，就走不到探测分支）。
"""

import ast
import pathlib
import unittest
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = ROOT / "modules" / "task_manager.py"


class _FrozenDatetime(datetime):
    """datetime 替身：now() 返回固定时刻，其余行为不变。"""

    _frozen = None

    @classmethod
    def now(cls, tz=None):
        return cls._frozen

    @classmethod
    def utcnow(cls):
        return cls._frozen

    @classmethod
    def set_now(cls, value):
        cls._frozen = value


def _load_functions(*names):
    """把指定函数摘出来，注入可控 datetime 与 _as_int 桩。"""
    source = MODULE.read_text(encoding="utf-8")
    module_ast = ast.parse(source, filename=str(MODULE))
    wanted = set(names)
    selected = [
        node for node in module_ast.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    isolated = ast.Module(body=selected, type_ignores=[])
    ns = {
        "datetime": _FrozenDatetime,
        "timedelta": timedelta,
        "_as_int": lambda value, default, minimum=None: (
            default if value in (None, "") else int(value)
        ),
    }
    exec(compile(isolated, str(MODULE), "exec"), ns)
    return [ns[n] for n in names]


class DailyQuotaBackoffTests(unittest.TestCase):
    """固定时刻：凌晨 00:40（刚过 00:30 重置点）。"""

    def setUp(self):
        _FrozenDatetime.set_now(datetime(2026, 11, 28, 0, 40, 0))
        self.retry_after, self.next_reset, self.compute_retry = _load_functions(
            "_daily_quota_retry_after", "_compute_next_daily_reset", "_compute_retry_after")

    def _hours_until(self, value):
        return (
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S") - _FrozenDatetime.now()
        ).total_seconds() / 3600

    def test_just_after_reset_uses_increasing_probe(self):
        """刚在重置点上失败时改递增探测，而不是再顺延一整天。"""
        waits = []
        for attempts in (0, 1, 2):
            retry_after, note, wait_s = self.retry_after(attempts, {})
            hours = self._hours_until(retry_after)
            self.assertLess(hours, 20, f"attempts={attempts} 不该顺延到次日")
            self.assertIn("探测", note)
            waits.append(wait_s)
        # 4h → 8h → 16h
        self.assertEqual(waits, [4 * 3600, 8 * 3600, 16 * 3600])

    def test_probe_backoff_capped(self):
        _, _, wait_s = self.retry_after(9, {})
        self.assertEqual(wait_s, 16 * 3600, "递增探测应封顶 16 小时")

    def test_wait_seconds_matches_timestamp(self):
        for attempts in (0, 1, 2):
            retry_after, _, wait_s = self.retry_after(attempts, {})
            hours = self._hours_until(retry_after)
            self.assertAlmostEqual(hours * 3600, wait_s, delta=120)


class DailyQuotaDefersToResetTests(unittest.TestCase):
    """固定时刻：下午 15:00（距下一个 00:30 还有 9.5h，属正常顺延）。"""

    def setUp(self):
        _FrozenDatetime.set_now(datetime(2026, 11, 28, 15, 0, 0))
        self.retry_after, self.next_reset, self.compute_retry = _load_functions(
            "_daily_quota_retry_after", "_compute_next_daily_reset", "_compute_retry_after")

    def test_midday_defers_to_next_reset(self):
        retry_after, note, wait_s = self.retry_after(0, {})
        self.assertEqual(retry_after, "2026-11-29 00:30:00")
        self.assertIn("顺延", note)
        # 15:00 -> 次日 00:30 = 9.5h
        self.assertEqual(wait_s, int(9.5 * 3600))

    def test_wait_seconds_positive(self):
        _, _, wait_s = self.retry_after(3, {})
        self.assertGreater(wait_s, 0)


class RateLimitPacePenaltyTests(unittest.TestCase):
    """日配额耗尽不应放大节流倍数（会拖累次日第一批任务）。"""

    def setUp(self):
        self.source = MODULE.read_text(encoding="utf-8")

    def test_daily_quota_does_not_escalate_factor(self):
        idx = self.source.index("def note_upload_rate_limited")
        body = self.source[idx:idx + 2200]
        daily_idx = body.index("if daily_quota:")
        factor_idx = body.index("_upload_penalty_factor = min(8")
        self.assertLess(daily_idx, factor_idx,
                        "daily_quota 分支必须先返回，不再放大惩罚倍数")
        self.assertIn("return", body[daily_idx:factor_idx])

    def test_daily_quota_still_sets_silence_window(self):
        """静默窗口必须保留，否则队列会逐条撞墙。"""
        idx = self.source.index("def note_upload_rate_limited")
        body = self.source[idx:idx + 2200]
        daily_idx = body.index("if daily_quota:")
        factor_idx = body.index("_upload_penalty_factor = min(8")
        self.assertIn("_upload_penalty_until", body[daily_idx:factor_idx])


if __name__ == "__main__":
    unittest.main()
