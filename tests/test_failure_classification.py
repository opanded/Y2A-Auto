"""失败分类回归测试：不要把可重试的瞬时故障当成永久失败。

两个真实故障促成了这些用例：

1. bilibili 上传报 `'NoneType' object is not subscriptable`
   根因是 _probe() 把全部线路测速超时后返回 None，_preupload 再取
   line["os"] 就崩了。耗时 30 秒、错误信息完全看不出是线路问题。

2. 两条视频被标记「视频不可用或无法访问」，但它们实际仍是 public
   预检用非零退出码判断不可用，而 n-challenge 解不出、TLS 被中间设备
   打断同样返回非零，这些错误把可用视频永久判死。
"""

import pathlib
import unittest


def _load_function(module_path, name):
    import ast
    source = module_path.read_text(encoding="utf-8")
    module_ast = ast.parse(source, filename=str(module_path))
    selected = [
        node for node in module_ast.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    isolated = ast.Module(body=selected, type_ignores=[])
    namespace = {}
    exec(compile(isolated, str(module_path), "exec"), namespace)
    return namespace[name]


ROOT = pathlib.Path(__file__).resolve().parents[1]


class UploadErrorRetryClassificationTests(unittest.TestCase):
    def setUp(self):
        self.fn = _load_function(ROOT / "modules" / "task_manager.py", "_is_retryable_upload_error")

    def test_chunk_upload_500_is_retryable(self):
        self.assertTrue(
            self.fn("bilibili上传异常: 网络错误，状态码：500 - 上传视频分块失败。")
        )

    def test_line_probe_none_type_is_retryable(self):
        # 旧版本把线路探测失败暴露成这句；它属环境问题，不是业务拒绝
        self.assertTrue(self.fn("bilibili上传异常: 'NoneType' object is not subscriptable"))

    def test_explicit_line_failure_is_retryable(self):
        self.assertTrue(
            self.fn("bilibili上传异常: 无法确定 bilibili 上传线路（线路表为空或探测失败）")
        )

    def test_account_risk_still_not_retryable(self):
        # 账号级风控不应因为放宽分类而变成重试
        self.assertFalse(self.fn("bilibili上传异常: 风控校验失败，代码 -352"))
        self.assertFalse(self.fn("bilibili上传异常: 603 账号异常"))


class DownloadFailureClassificationTests(unittest.TestCase):
    def setUp(self):
        self.permanent = _load_function(
            ROOT / "modules" / "task_manager.py", "_is_permanent_download_failure")
        self.transient = _load_function(
            ROOT / "modules" / "youtube_handler.py", "_looks_like_transient_network_error")

    def test_geo_block_is_permanent(self):
        self.assertTrue(
            self.permanent("The uploader has not made this video available in your country"))

    def test_deleted_video_is_permanent(self):
        self.assertTrue(self.permanent("This video has been removed by the uploader"))

    def test_generic_unavailable_message_is_not_permanent(self):
        """这条中文提示来自预检非零退出码，不能单独作为永久失败判据。"""
        self.assertFalse(self.permanent("视频不可用或无法访问"))

    def test_ssl_eof_detected_as_transient(self):
        self.assertTrue(
            self.transient(
                "Failed to download m3u8 information: [SSL: UNEXPECTED_EOF_WHILE_READING] "
                "EOF occurred in violation of protocol (_ssl.c:1010)"
            )
        )

    def test_n_challenge_failure_detected_as_transient(self):
        self.assertTrue(self.transient("n challenge solving failed: Some formats may be missing."))

    def test_real_geo_block_not_transient(self):
        self.assertFalse(
            self.transient("The uploader has not made this video available in your country")
        )


if __name__ == "__main__":
    unittest.main()


class ThrottleDoesNotSleepHoldingLockTests(unittest.TestCase):
    """preupload 406 不能在持有上传锁时 sleep。

    上传锁是全局的（MAX_CONCURRENT_UPLOADS=1）。旧实现在锁内
    sleep(600) 重试 5 次，一条被限流的任务会把后面所有任务饿死在
    「等待获取上传锁」上（实测 3 条任务卡死 8 分钟以上）。
    """

    def _source(self):
        return (ROOT / "modules" / "task_manager.py").read_text(encoding="utf-8")

    def test_no_long_sleep_for_throttle_in_retry_wrapper(self):
        import ast
        source = self._source()
        tree = ast.parse(source)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_upload_bilibili_with_retry":
                target = node
                break
        self.assertIsNotNone(target, "未找到 _upload_bilibili_with_retry")

        # 收集该函数里出现的 time.sleep 实参
        sleep_args = []
        for node in ast.walk(target):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "sleep"):
                sleep_args.append(ast.dump(node))

        # 允许按 retry_delay（短）睡眠，但不允许用 throttle_delay 长睡
        joined = " ".join(sleep_args)
        self.assertNotIn("throttle_delay", joined,
                         "限流冷却不应在持锁状态下长睡，应交给延迟队列")

    def test_preupload_406_goes_to_deferred_queue(self):
        source = self._source()
        # 406 分支必须写 retry_after 并置回 pending
        self.assertIn("preupload 406 限流，已排队等待重试", source)
        idx = source.index("preupload 406 限流，已排队等待重试")
        window = source[max(0, idx - 500):idx + 500]
        self.assertIn("retry_after", window)
        self.assertIn("PENDING", window)
