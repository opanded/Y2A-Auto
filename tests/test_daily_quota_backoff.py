"""日配额耗尽后的退避策略：纯探测，不再假设「次日重置点」。

背景（2026-11-27/28 实测）：
  - 11-27 当天投出 51 条，第 52 次起持续返回 137022。
  - 11-28 00:30（原本假设的「自然日重置点」）仍然 137022；
    02:30、02:53、03:02、04:45 也全部被拒。
    → 说明配额既不在 00:00 也不在 00:30 重置，「顺延到重置点」这个假设是错的。

旧实现因此在凌晨把任务排到「次日 00:30」，界面上显示
「1182 分钟后重试」——让用户白等 19.5 小时，而那个时点毫无依据。

现在改为纯递增探测：4h → 8h → 16h（封顶），到点再试一次。

注意：断言用「可控时钟」，否则会随一天中的时刻翻转。
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


class DailyQuotaProbeBackoffTests(unittest.TestCase):
    """固定时刻：凌晨 04:58（旧实现会把任务推到次日 00:30 = 等 19.5 小时）。"""

    def setUp(self):
        _FrozenDatetime.set_now(datetime(2026, 11, 28, 4, 58, 0))
        self.retry_after, self.compute_retry = _load_functions(
            "_daily_quota_retry_after", "_compute_retry_after")

    def _hours_until(self, value):
        return (
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S") - _FrozenDatetime.now()
        ).total_seconds() / 3600

    def test_first_probe_is_four_hours(self):
        retry_after, note, wait_s = self.retry_after(0, {})
        self.assertEqual(wait_s, 4 * 3600)
        self.assertAlmostEqual(self._hours_until(retry_after), 4.0, delta=0.1)
        self.assertIn("4 小时", note)

    def test_never_waits_until_next_midnight(self):
        """回归：凌晨时不能把任务推到次日 00:30（那是 19.5 小时）。"""
        for attempts in (0, 1, 2, 3, 9):
            retry_after, _, wait_s = self.retry_after(attempts, {})
            self.assertLessEqual(wait_s, 16 * 3600,
                                 f'attempts={attempts} 等待过长（旧实现在这里有 19.5h）')
            self.assertLessEqual(self._hours_until(retry_after), 16.1)

    def test_backoff_grows_then_caps(self):
        waits = [self.retry_after(a, {})[2] for a in (0, 1, 2, 3)]
        self.assertEqual(waits, [4 * 3600, 8 * 3600, 16 * 3600, 16 * 3600])

    def test_wait_seconds_matches_timestamp(self):
        for attempts in (0, 1, 2):
            retry_after, _, wait_s = self.retry_after(attempts, {})
            self.assertAlmostEqual(self._hours_until(retry_after) * 3600, wait_s, delta=120)

    def test_note_mentions_hours(self):
        _, note, _ = self.retry_after(1, {})
        self.assertIn("8 小时", note)


class ManualUploadBypassesPaceTests(unittest.TestCase):
    """手动上传必须跳过节流闸门。

    节流是给自动流程防撞墙用的；用户点「立即上传」就是明确要求现在试一次。
    此前手动上传同样被 19 小时的静默窗口挡住，按钮点了等于没反应。
    """

    def setUp(self):
        self.source = MODULE.read_text(encoding="utf-8")

    def test_upload_to_bilibili_accepts_manual_flag(self):
        self.assertIn(
            "def _upload_to_bilibili(self, task_id, task_logger, "
            "subtitle_prepared=False, manual=False)",
            self.source,
        )

    def test_manual_skips_pace_gate(self):
        idx = self.source.index("if manual:")
        window = self.source[idx:idx + 700]
        self.assertIn("跳过节流闸门", window)
        # manual 分支必须直接落到上传，不经过 wait_for_upload_slot
        self.assertIn("_do_upload_to_bilibili", window)

    def test_force_upload_passes_manual(self):
        idx = self.source.index("def force_upload_task")
        body = self.source[idx:idx + 3000]
        self.assertIn("manual=True", body)

    def test_manual_flag_threaded_through_target(self):
        self.assertIn("def _upload_to_target(self, task_id, task_logger, "
                      "allow_missing_translations=False,", self.source)
        self.assertIn("self._upload_to_bilibili(task_id, task_logger, manual=manual)",
                      self.source)


if __name__ == "__main__":
    unittest.main()
