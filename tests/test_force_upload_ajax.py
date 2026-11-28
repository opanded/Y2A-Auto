"""「立即上传」的 AJAX 契约：不跳页，返回 JSON，进度由 SSE 推进。

背景
----
任务列表点「立即上传」原先是一个普通表单 POST，路由处理后
`redirect(url_for('manual_review'))` —— 整页跳到人工审核页，
用户看不到正在上传什么进度。

改为：前端带 X-Requested-With 提交，路由返回 JSON 不跳页，
页面就地展开进度条；进度值由既有 SSE 的 task_progress 事件推送。

本测试锁住：
  A. AJAX 请求返回 JSON 且不含跳转；
  B. 缺分区时返回 400 + needs_partition + edit_url（前端据此提示后跳转）；
  C. 非 AJAX（无 JS 环境）仍保持原来的重定向行为，不能把老路径弄坏。
"""

import unittest
from unittest.mock import patch, MagicMock

import app as web_app


def _fake_task(status='pending', target='bilibili'):
    return {
        'id': '1',
        'task_id': '1',
        'status': status,
        'upload_target': target,
        'video_title_translated': '',
        'description_translated': '',
        'tags_generated': '[]',
        'selected_partition_id_bilibili': '152',
    }


class ForceUploadAjaxContractTests(unittest.TestCase):
    def setUp(self):
        web_app.app.config['TESTING'] = True
        self.client = web_app.app.test_client()

    def _patch(self, task=None, missing=None):
        return (
            patch.object(web_app, 'get_task', return_value=task or _fake_task()),
            patch.object(web_app, '_start_background_force_upload', lambda *a, **k: None),
            patch.object(web_app, 'load_config', return_value={}),
            patch.object(web_app, '_missing_upload_partition_labels',
                         lambda *a, **k: missing or []),
        )

    def test_ajax_returns_json_and_does_not_redirect(self):
        p1, p2, p3, p4 = self._patch()
        with p1, p2, p3, p4:
            resp = self.client.post(
                '/tasks/task-1/force_upload',
                headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'application/json')
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['task_id'], 'task-1')
        self.assertIn('bilibili', data['platform'])
        # 关键：不能是 3xx 跳转
        self.assertFalse(resp.status_code in (301, 302, 303, 307, 308))

    def test_ajax_missing_partition_reports_edit_url(self):
        p1, p2, p3, p4 = self._patch(missing=['bilibili'])
        with p1, p2, p3, p4:
            resp = self.client.post(
                '/tasks/task-1/force_upload',
                headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
            )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['ok'])
        self.assertTrue(data['needs_partition'])
        self.assertIn('/edit', data['edit_url'])
        self.assertIn('分区', data['error'])

    def test_ajax_task_not_found(self):
        with patch.object(web_app, 'get_task', return_value=None):
            resp = self.client.post(
                '/tasks/nope/force_upload',
                headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
            )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.get_json()['ok'])

    def test_plain_form_still_redirects(self):
        """没有 JS 的老路径必须保持重定向，不能返回 JSON。"""
        p1, p2, p3, p4 = self._patch()
        with p1, p2, p3, p4:
            resp = self.client.post('/tasks/task-1/force_upload')
        self.assertEqual(resp.status_code, 302)


class UploadProgressDomTests(unittest.TestCase):
    """进度条容器与 AJAX 标记必须出现在两套布局里。"""

    def _read(self, rel):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        return (root / 'templates' / rel).read_text(encoding='utf-8')

    def test_row_has_progress_bar_container(self):
        src = self._read('partials/task_row.html')
        self.assertIn('data-task-upload-progress', src)
        self.assertIn('data-task-upload-bar', src)

    def test_card_has_progress_bar_container(self):
        src = self._read('partials/task_card.html')
        self.assertIn('data-task-upload-progress', src)
        self.assertIn('data-task-upload-bar', src)

    def test_upload_forms_carry_ajax_marker(self):
        src = self._read('partials/task_actions.html')
        self.assertIn("a.endpoint == 'force_upload_task_route'", src)
        self.assertIn('data-ajax-upload="1"', src)

    def test_page_js_handles_progress_and_ajax(self):
        src = self._read('tasks.html')
        for marker in ('handleAjaxUploadSubmit', 'setUploadProgressState',
                       'parseProgressPercent', 'markUploadFailed',
                       'form[data-ajax-upload="1"]'):
            self.assertIn(marker, src, f'tasks.html 缺少 {marker}')

    def test_progress_boxes_addressed_as_list_not_single(self):
        """同一任务在 DOM 里有表格行和卡片两份，必须全部更新。

        用 querySelector 只会命中排在前面的那份（窄屏下正好是隐藏的表格行），
        进度条会更新到看不见的节点上——实测点击后进度条不出现就是这么来的。
        """
        src = self._read('tasks.html')
        self.assertIn('findTaskUploadProgressBoxes', src)
        self.assertIn('querySelectorAll(selector)', src)
        self.assertNotIn('function findTaskUploadProgressBox(', src,
                         '不应保留只取单个节点的旧函数')

    def test_progress_state_survives_fragment_refresh(self):
        """SSE 的 task_updated 会整行替换 DOM，进度状态必须能贴回去。"""
        src = self._read('tasks.html')
        self.assertIn('manualUploadState', src)
        self.assertIn('restoreManualUploadIndicators', src)
        # 三个会替换 DOM 的地方都要调用恢复
        self.assertIn('restoreManualUploadIndicators();\n    }', src.replace('\r\n', '\n'))
        occurrences = src.count('restoreManualUploadIndicators()')
        self.assertGreaterEqual(occurrences, 3,
                                '行替换 / 整块替换 / 定义处都应出现，实际只有 %d 处' % occurrences)

    def test_upload_state_not_cleared_while_pending(self):
        """上传启动瞬间状态仍是 pending，此时清状态会让进度条立刻消失。"""
        src = self._read('tasks.html')
        idx = src.index("payload.type === 'task_updated'")
        window = src[idx:idx + 900]
        self.assertIn("newStatus === 'completed'", window)
        self.assertIn("newStatus === 'failed'", window)
        self.assertNotIn("newStatus !== 'uploading'", window,
                         '不应按「非 uploading 就清」判断：pending 是上传启动中的正常状态')

    def test_pending_with_error_marks_failure(self):
        """上传失败（137022 等）会退回 pending + error_message。

        只按终态判断会让「上传中…」永远转下去——实测就是这个卡死。
        必须把 pending + error_message 识别为「本次尝试失败」并显示原因。
        """
        src = self._read('tasks.html')
        idx = src.index("payload.type === 'task_updated'")
        window = src[idx:idx + 900]
        self.assertIn("newStatus === 'pending' && errMsg", window)
        self.assertIn('markUploadFailed', window)

    def test_failure_restores_button(self):
        """失败后按钮必须恢复可点，否则用户没法重试。"""
        src = self._read('tasks.html')
        idx = src.index('function markUploadFailed')
        body = src[idx:idx + 1600]
        self.assertIn('btn.disabled = false', body)
        self.assertIn('originalHtml', body)

    def test_failed_state_not_overwritten_by_late_progress(self):
        """失败后可能还有一条滞后的 task_progress，不能把红色态刷回绿色。"""
        src = self._read('tasks.html')
        idx = src.index('function setUploadProgressState')
        body = src[idx:idx + 700]
        self.assertIn('state.failed', body)
        self.assertIn('return', body)

    def test_retry_clears_failed_flag(self):
        """用户重新点上传是新的一次尝试，要显式清 failed。"""
        src = self._read('tasks.html')
        idx = src.index('async function handleAjaxUploadSubmit')
        body = src[idx:idx + 1800]
        self.assertIn('failed: false', body)

    def test_event_payload_carries_error_message(self):
        """后端 task_updated 事件必须带上 error_message，前端才能判断失败。"""
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        src = (root / 'modules' / 'task_manager.py').read_text(encoding='utf-8')
        idx = src.index("event_type = 'task_updated'")
        window = src[idx:idx + 700]
        self.assertIn("event_payload['error_message']", window)


if __name__ == '__main__':
    unittest.main()


class FormNestingTests(unittest.TestCase):
    """任务列表不能出现嵌套 <form>。

    原先把整张表格包在 bulkActionForm 里，而行内操作按钮各自也是 <form>。
    HTML 不允许表单嵌套——浏览器会丢掉内层 form，于是点行内「重试」实际
    提交了外层批量表单，flash「请先勾选要操作的任务」。
    实测 DOM：bulkActionForm 内曾嵌着 38 个 form。
    """

    def _read(self, rel):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        return (root / 'templates' / rel).read_text(encoding='utf-8')

    def test_row_checkbox_uses_form_attribute(self):
        src = self._read('partials/task_row.html')
        idx = src.index('task-select-checkbox')
        window = src[max(0, idx - 400):idx + 300]
        self.assertIn('form="bulkActionForm"', window,
                      '行内复选框必须用 form 属性关联批量表单，而不是被它包裹')

    def test_bulk_form_does_not_wrap_task_list(self):
        src = self._read('tasks.html')
        idx = src.index('id="bulkActionForm"')
        # 从开标签往后找，列表容器必须出现在 </form> 之后
        rest = src[idx:]
        form_close = rest.index('</form>')
        list_start = rest.index('class="modern-list"')
        self.assertLess(form_close, list_start,
                        'bulkActionForm 必须在任务列表开始前闭合，否则产生嵌套表单')

    def test_form_tags_balanced(self):
        src = self._read('tasks.html')
        # 注释里提到 <form> 字样，先剥离 Jinja 注释
        import re
        body = re.sub(r'\{#.*?#\}', '', src, flags=re.S)
        opens = body.count('<form')
        closes = body.count('</form>')
        self.assertEqual(opens, closes, f'form 标签不配对：{opens} 开 / {closes} 闭')


class DailyQuotaNotCappedByMaxRetriesTests(unittest.TestCase):
    """日配额耗尽不受 UPLOAD_RATE_LIMIT_MAX_RETRIES 限制。

    实测：11-27 撞 137022 后，第 5 次探测把任务打成 failed，
    但配额只是「暂时没额度」，第二天会自己回来。
    判成永久失败只会让任务堆在失败列表里等人手点。
    """

    def setUp(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        self.src = (root / 'modules' / 'task_manager.py').read_text(encoding='utf-8')

    def test_max_retries_excludes_daily_quota(self):
        idx = self.src.index('def _schedule_rate_limit_retry')
        body = self.src[idx:idx + 1600]
        self.assertIn('is_daily_quota', body)
        # 上限判断必须排除日配额：条件里出现 not is_daily_quota
        cap_idx = body.index('if max_retries')
        window = body[cap_idx:cap_idx + 200]
        self.assertIn('not is_daily_quota', window,
                      '日配额耗尽不能吃掉 max_retries 上限')

    def test_manual_attempts_skip_retry_cap(self):
        idx = self.src.index('def _schedule_rate_limit_retry')
        body = self.src[idx:idx + 1600]
        cap_idx = body.index('if max_retries')
        window = body[cap_idx:cap_idx + 200]
        self.assertIn('not manual', window,
                      '手动上传不消耗自动重试预算')

    def test_manual_does_not_bump_counter(self):
        idx = self.src.index('def _schedule_rate_limit_retry')
        body = self.src[idx:idx + 2400]
        self.assertIn('if not manual:\n            _bump_rate_limit_retry_count', body,
                      '手动尝试不应累加自动退避计数')


class ManualUploadKeepsPlatformErrorTests(unittest.TestCase):
    """手动上传失败时必须保留平台原始报错。

    用户点「立即上传」，想看到的是 B站返回了什么（137022 投稿过于频繁），
    而不是我们自己的「日投稿配额耗尽，N 小时后重试」。
    后者是我们内部的排程说明，覆盖掉原始报错等于把平台的回答藏起来。
    """

    def setUp(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        self.src = (root / 'modules' / 'task_manager.py').read_text(encoding='utf-8')

    def test_manual_branch_keeps_result_text(self):
        idx = self.src.index('def _schedule_rate_limit_retry')
        body = self.src[idx:idx + 2600]
        self.assertIn('if manual:', body)
        manual_idx = body.index('if manual:')
        window = body[manual_idx:manual_idx + 900]
        # 必须把原始 result 写进 error_message，而不是内部 note
        self.assertIn('manual_note = f"{result}', window,
                      '手动分支必须以平台原始报错为主体')
        self.assertIn('error_message=manual_note', window)

    def test_auto_branch_still_uses_friendly_note(self):
        idx = self.src.index('def _schedule_rate_limit_retry')
        body = self.src[idx:idx + 2600]
        manual_idx = body.index('if manual:')
        after = body[manual_idx:]
        # 手动分支 return 之后，自动路径仍然写入 note
        self.assertIn('error_message=note', after)

    def test_manual_still_schedules_retry(self):
        """保留原始报错不等于放弃排程，retry_after 必须照常写入。"""
        idx = self.src.index('def _schedule_rate_limit_retry')
        body = self.src[idx:idx + 2600]
        manual_idx = body.index('if manual:')
        window = body[manual_idx:manual_idx + 900]
        self.assertIn('retry_after=retry_after', window)
