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
"""

import ast
import pathlib
import unittest
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_functions(*names):
    module_path = ROOT / "modules" / "task_manager.py"
    source = module_path.read_text(encoding="utf-8")
    module_ast = ast.parse(source, filename=str(module_path))
    wanted = set(names)
    selected = [
        node for node in module_ast.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    isolated = ast.Module(body=selected, type_ignores=[])
    ns = {
        "datetime": datetime,
        "timedelta": timedelta,
        # 供 _as_int 使用的最小桩：只处理这里会遇到的默认值与下限
        "_as_int": lambda value, default, minimum=None: (
            default if value in (None, "") else int(value)
        ),
    }
    exec(compile(isolated, str(module_path), "exec"), ns)
    return [ns[n] for n in names]


class DailyQuotaBackoffTests(unittest.TestCase):
    def setUp(self):
        self.retry_after, self.next_reset, self.compute_retry = _load_functions(
            "_daily_quota_retry_after", "_compute_next_daily_reset", "_compute_retry_after")

    def _hours_until(self, value):
        return (
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S") - datetime.now()
        ).total_seconds() / 3600

    def test_probe_backoff_grows(self):
        """刚在重置点失败时应改递增探测，而不是再顺延一整天。"""
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

    def test_returns_wait_seconds_consistent_with_timestamp(self):
        for attempts in (0, 1, 2):
            retry_after, _, wait_s = self.retry_after(attempts, {})
            hours = self._hours_until(retry_after)
            self.assertAlmostEqual(hours * 3600, wait_s, delta=120)


class RateLimitPacePenaltyTests(unittest.TestCase):
    """日配额耗尽不应放大节流倍数（会拖累次日第一批任务）。"""

    def setUp(self):
        source = (ROOT / "modules" / "task_manager.py").read_text(encoding="utf-8")
        self.source = source

    def test_daily_quota_does_not_escalate_factor(self):
        idx = self.source.index("def note_upload_rate_limited")
        body = self.source[idx:idx + 2200]
        # 倍数放大必须位于 daily_quota 的提前 return 之后
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
        window_part = body[daily_idx:factor_idx]
        self.assertIn("_upload_penalty_until", window_part)


if __name__ == "__main__":
    unittest.main()
