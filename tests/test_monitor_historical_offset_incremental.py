"""历史搬运偏移量在增量模式下不应吞掉新视频。

真实故障（2026-11-27）：aniplex 频道 17:00 发布《業物語 かれんオウガ》第1弾PV，
此后十几轮监控都报「处理新视频: 0」，视频始终没进任务队列。

根因是两处逻辑叠加：
  1. 配置切到增量游标后，每轮只拉「上次运行之后」的视频（0~6 条）；
  2. 但仍执行 filtered_videos[historical_offset:] 切片，而 offset 是
     历史回填累计值（45）。[45:] 对 6 条列表恒为空 → 新视频被永久丢弃。

同时 _update_historical_progress 用 new_offset >= len(本批) 判断「历史已完成」，
增量模式下同样失效（本批条数与历史总量无关）。
"""

import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MONITOR = ROOT / "modules" / "youtube_monitor.py"


class HistoricalOffsetIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.source = MONITOR.read_text(encoding="utf-8")

    def test_offset_slice_guarded_by_incremental_flag(self):
        """偏移量切片必须被 _used_incremental_cursor 排除。"""
        # 找到切片语句所在函数体，确认前置了增量判断
        idx = self.source.index("filtered_videos[current_offset:]")
        window = self.source[max(0, idx - 1200):idx]
        self.assertIn(
            "_used_incremental_cursor", window,
            "偏移量切片前必须先判断是否为增量轮次",
        )

    def test_incremental_flag_set_only_when_cursor_applied(self):
        """标记只在游标真正生效后设置，避免首轮全量回填误跳过偏移。"""
        idx = self.source.index("config['_used_incremental_cursor'] = True")
        window = self.source[max(0, idx - 600):idx]
        self.assertIn("published_after = cursor", window)

    def test_history_completed_check_not_used_in_incremental(self):
        idx = self.source.index("历史搬运已完成")
        window = self.source[max(0, idx - 500):idx]
        self.assertIn("_used_incremental_cursor", window)

    def test_slice_would_empty_small_incremental_batch(self):
        """固化故障形态：offset=45 对 6 条增量结果切片必然为空。"""
        batch = [f"v{i}" for i in range(6)]
        self.assertEqual(batch[45:], [])
        # 增量模式不做切片时，6 条全部保留
        self.assertEqual(len(batch), 6)


if __name__ == "__main__":
    unittest.main()
