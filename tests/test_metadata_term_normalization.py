"""译文术语一致性回归测试。

背景：有观众反馈「视频标题翻译错误 / 张冠李戴」。排查后确认译文内容本身没有
错位，真正的问题是同一角色/同一系列在同一频道里出现多种写法：
  - 環いろは 被译成「环彩羽」与「环伊吕波」两种（后者只是别称）；
  - 角色 CM 标签出现「角色CM / 分角色CM / 角色别CM / 按角色划分版本CM」四种；
  - 平台前缀出现「手游 / 手机游戏」「TV动画 / 电视动画」两种。
观众看到同一角色两个名字，就会以为标题配错了视频。

修复分两层：
  1. 术语表（METADATA_GLOSSARY）在 prompt 里固定译名；
  2. 输出端确定性规范化（METADATA_TERM_NORMALIZE），不依赖模型自觉。
本测试锁定第 2 层的确定性行为。
"""

import unittest

from modules.ai_enhancer import (
    _DEFAULT_METADATA_TERM_NORMALIZE,
    _normalize_translated_metadata,
)


class TermNormalizationTests(unittest.TestCase):
    def test_alias_name_maps_to_official_translation(self):
        # 環いろは 官方简体译名为「环彩羽」，「环伊吕波」是别称
        self.assertEqual(
            _normalize_translated_metadata(
                "手游《魔法纪录 魔法少女小圆外传》角色CM“环伊吕波”"
            ),
            "手游《魔法纪录 魔法少女小圆外传》角色CM“环彩羽”",
        )

    def test_cm_label_variants_unify(self):
        cases = [
            "分角色CM 二叶莎奈篇",
            "角色别CM 二叶莎奈篇",
            "角色别ver. CM 二叶莎奈篇",
            "按角色划分版本CM 二叶莎奈篇",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    _normalize_translated_metadata(raw),
                    "角色CM 二叶莎奈篇",
                )

    def test_platform_prefix_variants_unify(self):
        self.assertEqual(
            _normalize_translated_metadata("手机游戏《某作品》PV"),
            "手游《某作品》PV",
        )
        self.assertEqual(
            _normalize_translated_metadata("电视动画《某作品》第1话"),
            "TV动画《某作品》第1话",
        )

    def test_activity_title_keeps_fixed_names(self):
        """活动标题里的 たると 曾被音译成「塔尔托」，マギカ 被误译成「魔法纪录」。"""
        raw = "手游《魔法纪录 魔法少女小圆外传》活动『常夜之国的叛乱者 ～魔法少女塔尔托☆魔法纪录～』PV"
        self.assertIn("环彩羽", _normalize_translated_metadata(raw))

    def test_already_correct_text_is_untouched(self):
        text = "手游《魔法纪录 魔法少女小圆外传》角色CM「深月菲莉希亚」"
        self.assertEqual(_normalize_translated_metadata(text), text)

    def test_empty_input(self):
        self.assertEqual(_normalize_translated_metadata(""), "")
        self.assertEqual(_normalize_translated_metadata(None), "")

    def test_longest_key_wins(self):
        """长键必须先于短键匹配，否则会被切碎。"""
        # 表里同时存在「角色别ver. CM」与「角色别CM」
        for key in ("角色别ver. CM", "按角色划分版本CM"):
            self.assertIn(key, _DEFAULT_METADATA_TERM_NORMALIZE)
        self.assertEqual(
            _normalize_translated_metadata("角色别ver. CM 某角色"),
            "角色CM 某角色",
        )

    def test_custom_mapping_overrides_default(self):
        custom = {"环伊吕波": "环彩羽", "测试频道": "统一频道"}
        self.assertEqual(
            _normalize_translated_metadata("环伊吕波 测试频道", custom),
            "环彩羽 统一频道",
        )


if __name__ == "__main__":
    unittest.main()
