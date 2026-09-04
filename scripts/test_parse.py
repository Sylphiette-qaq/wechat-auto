#!/usr/bin/env python3
"""群聊消息读取解析层的离线回归测试。

纯标准库、不依赖 gi/容器。用法：
    python scripts/test_parse.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import atspi_parse as P  # noqa: E402

TESTDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata")


def load_nodes(name: str) -> list[dict]:
    """读取测试 fixture，并将每行 JSON 转换为节点字典。"""
    with open(os.path.join(TESTDATA, name), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestParseChatRow(unittest.TestCase):
    def test_group_mention_latest(self):
        """验证包含未读和聚合提及标记的群聊行解析。"""
        info = P.parse_chat_row(
            "珠科内哥喜欢6 3 unread message(s) [You were mentioned] 半夏: @小半夏 15:46"
        )
        self.assertEqual(info.chat_name, "珠科内哥喜欢6")
        self.assertEqual(info.chat_type_hint, "group")
        self.assertEqual(info.unread_count, 3)
        self.assertTrue(info.mentioned)
        self.assertEqual(info.sender, "半夏")
        self.assertEqual(info.preview_text, "@小半夏")
        self.assertEqual(info.row_time, "15:46")

    def test_group_no_mention_long_text(self):
        """验证长正文不会被误判为聚合提及。"""
        raw = (
            "珠科内哥喜欢6 2 unread message(s) "
            "老冯: @小半夏 复活吧我的夹子，复活吧我的夹子，复活吧我的夹子，复活吧我的夹子 16:19"
        )
        info = P.parse_chat_row(raw)
        self.assertEqual(info.chat_name, "珠科内哥喜欢6")
        self.assertEqual(info.chat_type_hint, "group")
        self.assertFalse(info.mentioned)
        self.assertEqual(info.sender, "老冯")
        self.assertTrue(info.preview_text.startswith("@小半夏 复活吧我的夹子"))
        self.assertEqual(info.row_time, "16:19")

    def test_group_no_unread_token(self):
        """验证没有未读 token 时仍能识别群聊和预览。"""
        info = P.parse_chat_row("珠科内哥喜欢6 老冯: 你好 15:50")
        self.assertEqual(info.chat_name, "珠科内哥喜欢6")
        self.assertEqual(info.chat_type_hint, "group")
        self.assertIsNone(info.unread_count)
        self.assertEqual(info.sender, "老冯")
        self.assertEqual(info.preview_text, "你好")
        self.assertEqual(info.row_time, "15:50")

    def test_direct_with_unread(self):
        """验证带未读数量的私聊行解析。"""
        info = P.parse_chat_row("半夏 1 unread message(s) 666 16:17")
        self.assertEqual(info.chat_name, "半夏")
        self.assertEqual(info.chat_type_hint, "direct")
        self.assertEqual(info.unread_count, 1)
        self.assertEqual(info.preview_text, "666")
        self.assertEqual(info.row_time, "16:17")
        self.assertEqual(info.sender, "")

    def test_direct_without_unread(self):
        """验证普通私聊行解析及空 sender 行为。"""
        info = P.parse_chat_row("半夏 666 16:17")
        self.assertEqual(info.chat_name, "半夏")
        self.assertEqual(info.chat_type_hint, "direct")
        self.assertIsNone(info.unread_count)
        self.assertEqual(info.preview_text, "666")
        self.assertEqual(info.row_time, "16:17")

    def test_direct_empty_preview(self):
        """验证没有预览正文的私聊仍保留会话名。"""
        info = P.parse_chat_row("半夏  ")
        self.assertEqual(info.chat_name, "半夏")
        self.assertEqual(info.chat_type_hint, "direct")
        self.assertFalse(info.has_content)
        self.assertEqual(info.preview_text, "")

    def test_chat_name_containing_colon_split(self):
        """验证群名包含冒号时解析不会崩溃且仍保留时间。"""
        info = P.parse_chat_row("学习:打卡群 老冯: 明天见 15:00")
        # 首个冒号段属于群名内含冒号场景的误拆，chat_name 至少非空且不崩
        self.assertTrue(info.chat_name)
        self.assertEqual(info.row_time, "15:00")

    def test_preview_preserves_u2005_for_matching(self):
        """验证 U+2005 空格属于正文，不能被普通空白折叠。"""
        raw = "珠科内哥喜欢6 1 unread message(s) [You were mentioned] 半夏: @小半夏\u2005你好666 16:51"
        info = P.parse_chat_row(raw)
        row_clean = "@小半夏\u2005你好666\n ".strip()
        self.assertEqual(info.preview_text, row_clean)
        self.assertNotEqual(info.preview_text, "@小半夏 你好666")


class TestMentionAndIdentity(unittest.TestCase):
    def test_is_mention_of(self):
        """验证 @昵称的边界匹配规则，避免误匹配相似昵称。"""
        self.assertTrue(P.is_mention_of("@小半夏 6666", "小半夏"))
        self.assertTrue(P.is_mention_of("@小半夏\u20056666", "小半夏"))
        self.assertTrue(P.is_mention_of("看 @小半夏 说话", "小半夏"))
        self.assertTrue(P.is_mention_of("@小半夏", "小半夏"))
        self.assertFalse(P.is_mention_of("@小半夏6666", "小半夏"))
        self.assertFalse(P.is_mention_of("@小半夏酱", "小半夏"))
        self.assertFalse(P.is_mention_of("@半夏 6666", "小半夏"))
        self.assertFalse(P.is_mention_of("普通消息", "小半夏"))
        self.assertFalse(P.is_mention_of("@小半夏 6666", ""))

    def test_message_id_distinguishes_time(self):
        """验证探针侧派生的消息 ID 在不同时间下保持可区分。"""
        text = "@小半夏 6666"
        a = P.derive_message_id("群A", "半夏", text, "16:24")
        b = P.derive_message_id("群A", "半夏", text, "16:25")
        self.assertNotEqual(a, b)
        c = P.derive_message_id("群A", "半夏", text, "16:24")
        self.assertEqual(a, c)
        d = P.derive_message_id("群A", "半夏", text, "")
        self.assertEqual(d, P.derive_message_id("群A", "半夏", text, ""))


def extract(name: str, **kwargs):
    """加载 fixture 并使用测试默认配置执行群聊事件提取。"""
    nodes = load_nodes(name)
    defaults = dict(
        account_id="acc-test",
        bot_name="小半夏",
        chat_type_force="auto",
        seen=set(),
        first_scan=False,
        emit_existing=True,
    )
    defaults.update(kwargs)
    return P.extract_group_events(nodes, **defaults)


class TestScenarioMentionOlder(unittest.TestCase):
    """16:25 场景：chats 预览是 123321，但真正的 @机器人 消息是更早的 @小半夏 6666。"""

    def setUp(self):
        """为每个场景测试准备一次完整的事件和诊断报告。"""
        self.events, self.report = extract("scenario_mention_older.jsonl")

    def test_group_open(self):
        """验证场景被识别为当前打开的群聊。"""
        self.assertTrue(self.report["group_open"])
        self.assertEqual(self.report["context"]["row"]["chat_name"], "珠科内哥喜欢6")

    def test_two_events_in_order(self):
        """验证消息按控件树顺序输出，较早的提及不会丢失。"""
        self.assertEqual(len(self.events), 2)
        first, second = self.events
        # 内容行按视图顺序输出：先 @小半夏 6666，后 123321
        self.assertEqual(first["text"], "@小半夏 6666")
        self.assertEqual(second["text"], "123321")

    def test_mention_event_fields(self):
        """验证较早提及消息的正文、时间和类型字段。"""
        first, second = self.events
        self.assertTrue(first["is_mention"])
        self.assertEqual(first["sender_name"], "")  # 非最新消息 sender 不可得
        self.assertEqual(first["message_time"], "16:24")
        self.assertEqual(first["chat_name"], "珠科内哥喜欢6")
        self.assertEqual(first["chat_type"], "group")
        self.assertEqual(first["message_type"], "text")

    def test_latest_event_gets_sender_from_chats_preview(self):
        """验证最新预览消息可以绑定 Chats 行中的发送者。"""
        _, second = self.events
        self.assertFalse(second["is_mention"])
        self.assertEqual(second["sender_name"], "半夏")
        self.assertEqual(second["message_time"], "16:25")
        self.assertEqual(second["unread_count"], 2)
        self.assertTrue(second["mentioned"])

    def test_sender_bound_even_when_time_header_lags(self):
        """验证区段时间滞后时仍以 Chats 预览时间为准。"""
        """实测区段头可能滞后于 chats 预览时间：正文一致即可绑定，时间用 chats 权威值。"""
        nodes = load_nodes("scenario_mention_older.jsonl")
        for n in nodes:
            name = n.get("name", "")
            if name.startswith("珠科内哥喜欢6 2 unread"):
                n["name"] = name.replace("123321 16:25", "123321 16:52")
            elif name == "16:25":
                n["name"] = "16:51"
        events, report = P.extract_group_events(
            nodes,
            account_id="acc",
            bot_name="小半夏",
            chat_type_force="auto",
            seen=set(),
            first_scan=False,
            emit_existing=True,
        )
        latest = events[-1]
        self.assertEqual(latest["text"], "123321")
        self.assertEqual(latest["sender_name"], "半夏")
        self.assertEqual(latest["message_time"], "16:52")

    def test_message_ids_distinct(self):
        """验证同一场景中的两条消息不会共享消息 ID。"""
        self.assertNotEqual(self.events[0]["message_id"], self.events[1]["message_id"])


class TestScenarioSameTextTwoTimes(unittest.TestCase):
    """同文本在 16:10 / 16:19 各出现一次：两条都输出且 message_id 不同。"""

    def test_both_emitted(self):
        """验证相同文本在两个时间区段出现时都会被输出。"""
        events, report = extract("scenario_same_text_two_times.jsonl")
        self.assertTrue(report["group_open"])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["text"], events[1]["text"])
        self.assertNotEqual(events[0]["message_id"], events[1]["message_id"])
        self.assertNotEqual(events[0]["message_time"], events[1]["message_time"])
        self.assertTrue(events[0]["is_mention"])  # 正文含 @小半夏


class TestScenarioDirectOpen(unittest.TestCase):
    """打开的是私聊：不产事件。"""

    def test_no_events_when_direct_open(self):
        """验证当前打开私聊时不产生群聊事件。"""
        events, report = extract("scenario_direct_open.jsonl")
        self.assertEqual(events, [])
        self.assertFalse(report["group_open"])
        self.assertEqual(report["reason"], "open_chat_not_group")

    def test_direct_with_mention_text_still_skipped(self):
        """私聊正文出现 '@昵称' 字样不得被误判为群聊提及。"""
        nodes = load_nodes("scenario_direct_open.jsonl")
        for n in nodes:
            if n.get("role") == "list item" and n.get("name", "").startswith("123321"):
                n["name"] = "@小半夏 你好\n "
        events, report = P.extract_group_events(
            nodes,
            account_id="acc",
            bot_name="小半夏",
            chat_type_force="auto",
            seen=set(),
            first_scan=False,
            emit_existing=True,
        )
        self.assertEqual(events, [])
        self.assertEqual(report["reason"], "open_chat_not_group")


class TestPaneTitle(unittest.TestCase):
    """错误提示 toast 与真实标题并存时，必须选中与 chats 会话名一致的标题。"""

    def _nodes(self):
        """构造包含错误提示和真实会话标题的最小节点树。"""
        return [
            {
                "path": [0, 0, 0, 0, 2, 1, 0, 0, 0, 2, 0, 0, 2, 0, 1],
                "role": "label",
                "name": "Unable to send message in an exited group chat",
                "text": "",
                "child_count": 0,
                "focused": False,
                "editable": False,
            },
            {
                "path": [0, 0, 0, 0, 2, 1, 0, 0, 0, 2, 0, 0, 2, 1, 1],
                "role": "text",
                "name": "珠科内哥喜欢6",
                "text": "",
                "child_count": 3,
                "focused": False,
                "editable": True,
            },
            {
                "path": [0, 0, 0, 0, 2, 1, 0, 0, 0, 2, 0, 1, 0],
                "role": "list",
                "name": "Messages",
                "text": "",
                "child_count": 0,
                "focused": False,
                "editable": False,
            },
        ]

    def test_prefers_name_matching_a_chat_row(self):
        """验证标题匹配 Chats 会话名时优先选择该标题。"""
        msg_root = self._nodes()[-1]
        title = P.find_pane_title(self._nodes(), msg_root, ["珠科内哥喜欢6", "半夏"])
        self.assertEqual(title, "珠科内哥喜欢6")

    def test_no_match_returns_empty(self):
        """验证没有匹配会话名时不误选错误提示文本。"""
        msg_root = self._nodes()[-1]
        title = P.find_pane_title(self._nodes(), msg_root, ["半夏"])
        self.assertEqual(title, "")

    def test_without_chat_names_falls_back_to_deepest(self):
        """验证缺少会话名约束时回退到最深候选标题。"""
        msg_root = self._nodes()[-1]
        title = P.find_pane_title(self._nodes(), msg_root)
        self.assertTrue(title)


class TestIncremental(unittest.TestCase):
    def test_baseline_then_incremental(self):
        """验证首扫建立基线，后续只输出新增消息。"""
        nodes = load_nodes("scenario_mention_older.jsonl")
        seen: set = set()

        # 首扫（不 emit_existing）：只记基线，不输出
        events, _ = P.extract_group_events(
            nodes,
            account_id="acc",
            bot_name="小半夏",
            chat_type_force="auto",
            seen=seen,
            first_scan=True,
            emit_existing=False,
        )
        self.assertEqual(events, [])
        self.assertEqual(len(seen), 2)

        # 状态未变：无输出
        events, _ = P.extract_group_events(
            nodes,
            account_id="acc",
            bot_name="小半夏",
            chat_type_force="auto",
            seen=seen,
            first_scan=False,
            emit_existing=False,
        )
        self.assertEqual(events, [])

        # 新消息 12344567 到达（chats 预览同步更新）
        updated = [dict(n) for n in nodes]
        updated.append(
            {
                "path": [0, 0, 0, 0, 2, 1, 0, 0, 0, 2, 0, 1, 0, 4],
                "role": "list item",
                "name": "12344567\n ",
                "text": "",
                "child_count": 0,
                "focused": False,
                "editable": False,
            }
        )
        for n in updated:
            if n.get("name", "").startswith("珠科内哥喜欢6 2 unread"):
                n["name"] = (
                    "珠科内哥喜欢6 3 unread message(s) [You were mentioned] "
                    "半夏: 12344567 16:26"
                )
        events, _ = P.extract_group_events(
            updated,
            account_id="acc",
            bot_name="小半夏",
            chat_type_force="auto",
            seen=seen,
            first_scan=False,
            emit_existing=False,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], "12344567")
        self.assertEqual(events[0]["is_mention"], False)

    def test_mentioned_aggregate_does_not_replay_old_mention_after_window_shift(self):
        """时间头被尾部窗口截掉时，持续的聚合 @ 提示不得重放旧消息。"""
        seen: set = set()
        first = [
            {
                "path": [0],
                "role": "list",
                "name": "Chats",
                "text": "",
                "child_count": 1,
            },
            {
                "path": [0, 0],
                "role": "list item",
                "name": "测试群 1 unread message(s) [You were mentioned] 半夏: @小半夏 07:00",
                "text": "",
                "child_count": 0,
            },
            {
                "path": [0, 1],
                "role": "text",
                "name": "测试群",
                "text": "",
                "child_count": 0,
            },
            {
                "path": [1],
                "role": "list",
                "name": "Messages",
                "text": "",
                "child_count": 3,
            },
            {
                "path": [1, 1],
                "role": "list item",
                "name": "07:00",
                "text": "",
                "child_count": 0,
            },
            {
                "path": [1, 2],
                "role": "list item",
                "name": "@小半夏 你好",
                "text": "",
                "child_count": 0,
            },
        ]
        events, _ = P.extract_group_events(
            first,
            account_id="acc",
            bot_name="小半夏",
            chat_type_force="auto",
            seen=seen,
            first_scan=False,
            emit_existing=True,
        )
        self.assertEqual([event["text"] for event in events], ["@小半夏 你好"])

        second = [
            {
                "path": [0],
                "role": "list",
                "name": "Chats",
                "text": "",
                "child_count": 1,
            },
            {
                "path": [0, 0],
                "role": "list item",
                "name": "测试群 2 unread message(s) [You were mentioned] 半夏: 普通消息 07:01",
                "text": "",
                "child_count": 0,
            },
            {
                "path": [0, 1],
                "role": "text",
                "name": "测试群",
                "text": "",
                "child_count": 0,
            },
            {
                "path": [1],
                "role": "list",
                "name": "Messages",
                "text": "",
                "child_count": 12,
            },
            # 时间头在消息窗口外；旧消息路径仍保持不变，但 time_block 变为空。
            {
                "path": [1, 2],
                "role": "list item",
                "name": "@小半夏 你好",
                "text": "",
                "child_count": 0,
            },
            {
                "path": [1, 10],
                "role": "list item",
                "name": "机器人的回复",
                "text": "",
                "child_count": 0,
            },
            {
                "path": [1, 11],
                "role": "list item",
                "name": "普通消息",
                "text": "",
                "child_count": 0,
            },
        ]
        events, _ = P.extract_group_events(
            second,
            account_id="acc",
            bot_name="小半夏",
            chat_type_force="auto",
            seen=seen,
            first_scan=False,
            emit_existing=True,
        )
        self.assertEqual(
            [event["text"] for event in events], ["机器人的回复", "普通消息"]
        )
        self.assertTrue(all(not event["is_mention"] for event in events))


class TestGroupDetectionFallbacks(unittest.TestCase):
    def test_no_chats_root_but_mention_row(self):
        """验证缺少 Chats 容器时可根据群聊提及内容兜底识别。"""
        nodes = load_nodes("scenario_mention_older.jsonl")
        nodes = [n for n in nodes if n.get("role") != "list" or n.get("name") != "Chats"]
        events, report = P.extract_group_events(
            nodes,
            account_id="acc",
            bot_name="小半夏",
            chat_type_force="auto",
            seen=set(),
            first_scan=False,
            emit_existing=True,
        )
        self.assertTrue(report["group_open"])
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["chat_name"], "珠科内哥喜欢6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
