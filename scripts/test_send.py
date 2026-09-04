#!/usr/bin/env python3
"""发送流程的离线回归测试，不连接真实 AT-SPI/X11。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_probe():
    """注入最小 gi 假模块后加载探针模块。"""
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


class FakeProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):  # noqa: ARG002
        self.returncode = 0

    def kill(self):
        self.returncode = -9


class TestSendHelpers(unittest.TestCase):
    def test_duration_and_send_args(self):
        self.assertEqual(P.duration_seconds("10s"), 10)
        self.assertEqual(P.duration_seconds("0.5"), 0.5)
        args = P.parse_args(["send", "--send-key", "enter", "--send-timeout", "3s"])
        self.assertEqual(args.send_key, "enter")
        self.assertEqual(args.send_timeout, 3)

    def test_send_message_pastes_and_sends_once(self):
        fake_node = object()
        snapshots = [{"role": "list", "name": "Messages", "path": [1], "child_count": 1}]
        diagnostics = P.Diagnostics(verbose=False)
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(P, "SEND_LOCK_PATH", os.path.join(temp_dir, "send.lock")), \
                mock.patch.object(P, "_find_unique_wechat_window", return_value="42"), \
                mock.patch.object(P, "find_wechat_application", return_value=object()), \
                mock.patch.object(P, "walk_tree", return_value=[(fake_node, SimpleNamespace(as_record=lambda: snapshots[0]))]), \
                mock.patch.object(P, "find_messages_root", return_value=snapshots[0]), \
                mock.patch.object(P, "find_chats_root", return_value={"path": [0]}), \
                mock.patch.object(P, "parse_chat_rows", return_value=[SimpleNamespace(chat_name="测试群", chat_type_hint="group")]), \
                mock.patch.object(P, "find_pane_title", return_value="测试群"), \
                mock.patch.object(P, "_candidate_input_nodes", return_value=[(fake_node, None)]), \
                mock.patch.object(P, "_focus_input"), \
                mock.patch.object(P, "_read_clipboard", return_value=(False, "")), \
                mock.patch.object(P, "_start_clipboard_owner", return_value=FakeProcess()), \
                mock.patch.object(P, "_require_command", side_effect=lambda args, **_kwargs: calls.append(list(args))), \
                mock.patch.object(P, "_read_input_text", return_value="你好\n世界"), \
                mock.patch.object(P, "_poll_send_state", return_value=(True, True)):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = P.send_message(
                        app_pattern=P.re.compile("wechat", P.re.IGNORECASE),
                        diagnostics=diagnostics,
                        max_depth=40,
                        text="你好\n世界",
                        send_key="enter",
                        timeout_seconds=10,
                    )

        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["accepted"])
        self.assertEqual(result["verification"], "input_cleared_and_message_observed")
        self.assertEqual([call[3:] for call in calls], [["ctrl+v"], ["Return"]])

    def test_find_unique_wechat_window_uses_supported_title_patterns(self):
        calls = []

        def fake_command(args, *, timeout, input_bytes=None):  # noqa: ARG001
            calls.append(list(args))
            if args[-1] == "Weixin":
                return SimpleNamespace(returncode=0, stdout=b"42\n", stderr=b"")
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

        with mock.patch.object(P, "_command", side_effect=fake_command), mock.patch.object(P, "_require_command") as activate:
            self.assertEqual(P._find_unique_wechat_window(), "42")

        self.assertEqual([call[-1] for call in calls], ["Weixin", "WeChat", "wechat", "微信"])
        activate.assert_called_once_with(["xdotool", "windowfocus", "--sync", "42"], timeout=2.0)

    def test_candidate_input_prefers_current_chat_title_over_search_fields(self):
        nodes = [
            ("left-search", P.NodeSnapshot((0,), "text", "Search", "", 0, False, True)),
            ("message-input", P.NodeSnapshot((1,), "text", "测试群", "", 0, False, True)),
            ("right-search", P.NodeSnapshot((2,), "text", "Search", "", 0, False, True)),
        ]
        result = P._candidate_input_nodes(nodes, title="测试群", chat_names=["测试群"])
        self.assertEqual(result, [nodes[1]])


if __name__ == "__main__":
    unittest.main()
