"""任务列表按钮矩阵：唯一事实来源 + 两套模板必须一致。

背景
----
任务列表有桌面表格（task_row.html）和窄屏卡片（task_card.html）两套模板。
按钮的「显示什么」原先在两套里各手写一遍 if/elif，实测漏过两次：

  1. pending 冷却中只显示灰掉的「等待中」，没有手动上传入口；
  2. downloaded 只有「中止」，没有「上传」。
  两次都只在表格版修好，窄屏卡片仍是旧的。

现在判断集中在 modules/task_view.py:build_task_actions()，模板只渲染。
本测试锁住三件事：
  A. 每种状态的按钮组合符合预期；
  B. 两套模板都不再自己写状态判断（防止有人又加回一份）；
  C. 渲染出的按钮在同一页面里只由宏产出。
"""

import pathlib
import re
import unittest

from modules.task_view import (
    ACTIVE_PROCESSING_STATES,
    build_task_actions,
    build_subtitle_warnings,
    prepare_task_view,
    task_status_icon,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROW_TPL = ROOT / "templates" / "partials" / "task_row.html"
CARD_TPL = ROOT / "templates" / "partials" / "task_card.html"
MACRO_TPL = ROOT / "templates" / "partials" / "task_actions.html"


def _labels(actions):
    return [a["label"] for a in actions]


class ActionMatrixTests(unittest.TestCase):
    def test_pending_with_cooldown_offers_manual_upload(self):
        """冷却中也要能手动投——自动重试只是兜底。"""
        acts = build_task_actions({
            "id": "x", "status": "pending",
            "retry_after": "2026-11-28 19:30:00", "upload_target": "bilibili",
        })
        self.assertEqual(_labels(acts), ["立即上传"])
        self.assertEqual(acts[0]["endpoint"], "force_upload_task_route")
        # 同时要标注自动重试时间，避免用户以为关掉了自动重试
        self.assertIn("19:30", acts[0]["note"])

    def test_pending_without_cooldown_offers_start(self):
        acts = build_task_actions({"id": "x", "status": "pending", "upload_target": "bilibili"})
        self.assertEqual(_labels(acts), ["开始"])
        self.assertEqual(acts[0]["endpoint"], "start_task_route")

    def test_downloaded_offers_upload_and_abort(self):
        acts = build_task_actions({"id": "x", "status": "downloaded", "upload_target": "bilibili"})
        self.assertEqual(_labels(acts), ["上传", "中止"])
        self.assertEqual(acts[0]["endpoint"], "force_upload_task_route")

    def test_ready_for_upload_names_target(self):
        acts = build_task_actions({"id": "x", "status": "ready_for_upload", "upload_target": "bilibili"})
        self.assertEqual(_labels(acts), ["上传到bilibili"])
        acts = build_task_actions({"id": "x", "status": "ready_for_upload", "upload_target": "both"})
        self.assertEqual(_labels(acts), ["上传到双平台"])

    def test_active_states_only_abort(self):
        for status in ACTIVE_PROCESSING_STATES:
            with self.subTest(status=status):
                acts = build_task_actions({"id": "x", "status": status})
                self.assertEqual(_labels(acts), ["中止"])

    def test_downloaded_is_not_in_active_states(self):
        """downloaded 必须单独处理，否则又会退化成只有「中止」。"""
        self.assertNotIn("downloaded", ACTIVE_PROCESSING_STATES)

    def test_awaiting_review_is_link(self):
        acts = build_task_actions({"id": "x", "status": "awaiting_manual_review"})
        self.assertEqual(acts[0]["kind"], "link")
        self.assertEqual(acts[0]["endpoint"], "review_task")

    def test_failed_retryable(self):
        acts = build_task_actions({
            "id": "x", "status": "failed", "error_message": "下载视频失败",
        })
        self.assertEqual(_labels(acts), ["重试"])
        self.assertEqual(acts[0]["endpoint"], "start_task_route")

    def test_failed_permanent_is_disabled(self):
        acts = build_task_actions({
            "id": "x", "status": "failed", "error_message": "this video is private",
        })
        self.assertEqual(_labels(acts), ["不可重试"])
        self.assertEqual(acts[0]["kind"], "disabled")

    def test_completed_offers_reupload_with_confirm(self):
        acts = build_task_actions({"id": "x", "status": "completed"})
        self.assertEqual(_labels(acts), ["重新上传"])
        self.assertIn("重复稿件", acts[0]["confirm"])

    def test_unknown_status_has_no_actions(self):
        self.assertEqual(build_task_actions({"id": "x", "status": "weird"}), [])


class StatusIconTests(unittest.TestCase):
    def test_known_statuses_have_specific_icon(self):
        self.assertEqual(task_status_icon("downloading"), "download")
        self.assertEqual(task_status_icon("completed"), "check-circle")
        self.assertEqual(task_status_icon("failed"), "x-circle")

    def test_unknown_falls_back(self):
        self.assertEqual(task_status_icon("whatever"), "circle")
        self.assertEqual(task_status_icon(None), "circle")


class SubtitleWarningTests(unittest.TestCase):
    def test_empty_task_has_no_badges(self):
        self.assertEqual(build_subtitle_warnings({}), [])

    def test_qc_failure_badge_contains_reason_and_threshold(self):
        badges = build_subtitle_warnings(
            {"subtitle_qc_failed": 1, "subtitle_qc_reason": "ai_fail:hallucination_meta",
             "subtitle_qc_score": 0.2},
            {"SUBTITLE_QC_THRESHOLD": 0.35},
        )
        self.assertEqual(len(badges), 1)
        self.assertEqual(badges[0]["label"], "字幕异常")
        self.assertIn("疑似幻觉", badges[0]["tooltip"])
        self.assertIn("0.350", badges[0]["tooltip"])

    def test_asr_warning_only(self):
        badges = build_subtitle_warnings({"asr_warning_message": "asr_failed"})
        self.assertEqual(len(badges), 1)
        self.assertIn("ASR语音识别全部失败", badges[0]["tooltip"])

    def test_embed_failure_is_separate_badge(self):
        badges = build_subtitle_warnings({"subtitle_warning_message": "subtitle_embed_failed"})
        self.assertEqual(len(badges), 1)
        self.assertEqual(badges[0]["label"], "字幕烧录异常")


class PrepareViewTests(unittest.TestCase):
    def test_display_title_fallback(self):
        view = prepare_task_view({"video_title_original": "orig"})
        self.assertEqual(view["_display_title"], "orig")
        view = prepare_task_view({})
        self.assertEqual(view["_display_title"], "未获取标题")

    def test_actions_and_warnings_attached(self):
        view = prepare_task_view(
            {"id": "x", "status": "downloaded", "subtitle_qc_failed": 1,
             "subtitle_qc_reason": "rule_fail:ultra_short_repeat"},
            {},
            "bilibili",
        )
        self.assertEqual(_labels(view["_actions"]), ["上传", "中止"])
        self.assertEqual(len(view["_subtitle_warnings"]), 1)
        self.assertEqual(view["_status_icon"], "download")


class TemplateSingleSourceTests(unittest.TestCase):
    """防止有人又把按钮矩阵手写回模板里。"""

    def _strip_comments(self, text):
        return re.sub(r"\{#.*?#\}", "", text, flags=re.S)

    def test_row_template_delegates_to_macro(self):
        src = self._strip_comments(ROW_TPL.read_text(encoding="utf-8"))
        self.assertIn("task_actions(", src, "表格模板必须调用共享宏")
        self.assertIn("subtitle_warning_badges(", src)

    def test_card_template_delegates_to_macro(self):
        src = self._strip_comments(CARD_TPL.read_text(encoding="utf-8"))
        self.assertIn("task_actions(", src, "卡片模板必须调用共享宏")
        self.assertIn("subtitle_warning_badges(", src)

    def test_no_inline_status_branching_for_buttons(self):
        """模板里不应再出现按状态决定按钮的 if/elif（宏之外）。"""
        button_markers = (
            "url_for('force_upload_task_route'",
            "url_for('start_task_route'",
            "url_for('abandon_task_route'",
        )
        for tpl in (ROW_TPL, CARD_TPL):
            src = self._strip_comments(tpl.read_text(encoding="utf-8"))
            for marker in button_markers:
                self.assertNotIn(
                    marker, src,
                    f"{tpl.name} 里仍在手写按钮表单；应改用 task_actions 宏",
                )

    def test_macro_file_exists_and_exports(self):
        src = MACRO_TPL.read_text(encoding="utf-8")
        self.assertIn("{% macro task_actions(", src)
        self.assertIn("{% macro subtitle_warning_badges(", src)


if __name__ == "__main__":
    unittest.main()
