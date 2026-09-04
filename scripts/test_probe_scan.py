#!/usr/bin/env python3
"""watch 局部 AT-SPI 扫描器的离线测试。"""

from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_probe():
    atspi = types.SimpleNamespace(
        StateType=types.SimpleNamespace(FOCUSED="focused", EDITABLE="editable"),
        init=lambda: None,
    )
    glib = types.SimpleNamespace()
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    gi.repository = types.SimpleNamespace(Atspi=atspi, GLib=glib)
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = gi.repository
    import atspi_probe as probe  # noqa: PLC0415

    return probe


P = load_probe()


class FakeStates:
    def contains(self, _state):
        return False


class FakeNode:
    def __init__(self, name, role, children=()):
        self.name = name
        self.role = role
        self.children = list(children)
        self.accessed = []

    def get_name(self):
        return self.name

    def get_role_name(self):
        return self.role

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        self.accessed.append(index)
        return self.children[index]

    def get_state_set(self):
        return FakeStates()

    def get_text_iface(self):
        return None


class TestWatchScan(unittest.TestCase):
    def test_messages_only_reads_tail_window(self):
        messages = FakeNode("Messages", "list", [FakeNode(str(i), "list item") for i in range(100)])
        chats = FakeNode("Chats", "list", [FakeNode("测试群 老冯: hi 12:00", "list item")])
        root = FakeNode("WeChat", "frame", [chats, messages])

        nodes = P.scan_watch_tree(root, P.Diagnostics(verbose=False), max_depth=10, message_window=10)

        self.assertEqual(messages.accessed, list(range(90, 100)))
        self.assertEqual([item[1].name for item in nodes if item[1].path == (1, 90)], ["90"])
        self.assertNotIn((1, 0), [item[1].path for item in nodes])

    def test_messages_shorter_than_window_reads_all_without_negative_index(self):
        messages = FakeNode("Messages", "list", [FakeNode(str(i), "list item") for i in range(3)])
        root = FakeNode("WeChat", "frame", [messages])

        P.scan_watch_tree(root, P.Diagnostics(verbose=False), max_depth=10, message_window=10)

        self.assertEqual(messages.accessed, [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
