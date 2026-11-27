"""日配额耗尽与分钟级限流的退避区分。

两者都用 137022 之类的「投稿过于频繁」提示，但恢复窗口差一个数量级：
  - 分钟级频率限制：等几十分钟即可（preupload 406 一类）
  - 日配额耗尽：必须等自然日重置，每 2 小时撞一次只是整夜空转

因此日配额耗尽要顺延到次日重置时点。这里锁定这两个判定。
"""

import ast
import pathlib
import unittest

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
    namespace = {"datetime": __import__("datetime").datetime, "timedelta": __import__("datetime").timedelta}
    exec(compile(isolated, str(module_path), "exec"), namespace)
    return [namespace[n] for n in names]


class DailyQuotaDetectionTests(unittest.TestCase):
    def setUp(self):
        self.is_daily, self.next_reset = _load_functions(
            "_is_daily_quota_exhausted", "_compute_next_daily_reset")

    def test_137022_is_daily_quota(self):
        self.assertTrue(self.is_daily("上传失败: 状态码 137022 - 投稿过于频繁"))

    def test_chinese_message_is_daily_quota(self):
        self.assertTrue(self.is_daily("投稿过于频繁，请稍后再试"))

    def test_preupload_406_is_not_daily_quota(self):
        """频率限制不是日配额，仍按分钟级冷却处理。"""
        self.assertFalse(self.is_daily("preupload 获取失败，状态码 406"))

    def test_empty_is_not_daily_quota(self):
        self.assertFalse(self.is_daily(""))
        self.assertFalse(self.is_daily(None))

    def test_next_reset_is_in_the_future(self):
        from datetime import datetime
        value = self.next_reset(0, 30)
        target = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        self.assertGreater(target, datetime.now())
        self.assertEqual((target.hour, target.minute), (0, 30))


if __name__ == "__main__":
    unittest.main()
