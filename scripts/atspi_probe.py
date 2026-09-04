#!/usr/bin/env python3
"""Linux 微信的最小 AT-SPI 探针。

探针提供两种模式：

* ``dump``：以 JSON Lines 输出无障碍控件树。
* ``watch``：建立首次扫描基线，持续输出新发现的消息候选节点；AT-SPI 事件
  会提前唤醒扫描器，当微信没有发出有用事件时仍使用轮询作为兼容路径。

诊断信息统一以 JSON Lines 写入 stderr，使 Go CLI 可以安全消费 stdout 中的
业务记录而不发生两条流混杂。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Sequence

from atspi_parse import (
    CHATS_CONTAINER_NAMES,
    INPUT_NAMES,
    MESSAGE_CONTAINER_NAMES,
    extract_group_events,
    find_chats_root,
    find_messages_root,
    find_pane_title,
    normalized,
    parse_chat_rows,
    split_message_rows,
)

try:
    # PyGObject 只在容器运行环境中可用；导入失败时将结构化错误写到 stderr。
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
except (ImportError, ValueError) as exc:  # pragma: no cover - 容器依赖，仅运行时覆盖
    print(
        json.dumps(
            {
                "level": "error",
                "code": "atspi_import_failed",
                "message": str(exc),
                "hint": "install python3-gi and gir1.2-atspi-2.0",
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


DEFAULT_APP_PATTERN = r"(?i)(wechat|weixin|微信)"
DEFAULT_MESSAGE_WINDOW = 10
# 订阅会唤醒扫描器的 AT-SPI 事件，轮询仍作为兼容兜底路径保留。
EVENT_TYPES = (
    "object:children-changed",
    "object:text-changed",
    "object:state-changed:focused",
    "focus",
)
SEND_KEY_VALUES = ("enter",)
SEND_LOCK_PATH = "/tmp/wechat-auto-send.lock"


def utc_now() -> str:
    """返回 UTC ISO-8601 时间，供诊断和事件记录使用。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def emit_json(stream: Any, record: dict[str, Any]) -> None:
    """将一条结构化记录写成单行 JSON，保证下游可流式解析。"""
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), file=stream, flush=True)


class Diagnostics:
    """负责输出结构化诊断，并抑制重复的远程控件访问错误。"""

    def __init__(self, verbose: bool) -> None:
        """初始化诊断器；verbose 模式下不抑制重复错误。"""
        self.verbose = verbose
        self._reported: set[tuple[str, str]] = set()

    def write(self, level: str, code: str, message: str, **fields: Any) -> None:
        """输出一条带上下文字段的诊断日志。"""
        emit_json(
            sys.stderr,
            {"timestamp": utc_now(), "level": level, "code": code, "message": message, **fields},
        )

    def once(self, code: str, error: BaseException, **fields: Any) -> None:
        """按错误类型去重输出一次远程访问异常。"""
        key = (code, type(error).__name__)
        if key in self._reported and not self.verbose:
            return
        self._reported.add(key)
        self.write("warning", code, str(error), error_type=type(error).__name__, **fields)


@dataclass(frozen=True)
class NodeSnapshot:
    """AT-SPI 节点的稳定快照，避免业务逻辑直接依赖远程对象。"""
    path: tuple[int, ...]
    role: str
    name: str
    text: str
    child_count: int
    focused: bool
    editable: bool

    @property
    def content(self) -> str:
        """返回节点正文；正文为空时回退到 accessible name。"""
        return self.text.strip() or self.name.strip()

    def as_record(self) -> dict[str, Any]:
        """转换成可写入 JSONL 的原始节点结构。"""
        return {
            "path": list(self.path),
            "role": self.role,
            "name": self.name,
            "text": self.text,
            "child_count": self.child_count,
            "focused": self.focused,
            "editable": self.editable,
        }


def safe_name(node: Any, diagnostics: Diagnostics) -> str:
    """安全读取节点名称，屏蔽 AT-SPI 远程调用异常。"""
    try:
        return str(node.get_name() or "")
    except Exception as exc:  # noqa: BLE001 - 远程无障碍对象边界
        diagnostics.once("node_name_unavailable", exc)
        return ""


def safe_role(node: Any, diagnostics: Diagnostics) -> str:
    """安全读取节点 role 名称。"""
    try:
        return str(node.get_role_name() or "unknown")
    except Exception as exc:  # noqa: BLE001 - 远程无障碍对象边界
        diagnostics.once("node_role_unavailable", exc)
        return "unknown"


def safe_child_count(node: Any, diagnostics: Diagnostics) -> int:
    """安全读取子节点数量并规范为非负整数。"""
    try:
        return max(0, int(node.get_child_count()))
    except Exception as exc:  # noqa: BLE001 - 远程无障碍对象边界
        diagnostics.once("node_children_unavailable", exc)
        return 0


def safe_text(node: Any, diagnostics: Diagnostics) -> str:
    """通过 Atspi.Text 接口读取节点正文。"""
    try:
        text_iface = node.get_text_iface()
        if text_iface is None:
            return ""
        count = int(text_iface.get_character_count())
        if count <= 0:
            return ""
        return str(text_iface.get_text(0, count) or "")
    except Exception as exc:  # noqa: BLE001 - 不同节点的接口可用性不同
        diagnostics.once("node_text_unavailable", exc)
        return ""


def safe_states(node: Any, diagnostics: Diagnostics) -> tuple[bool, bool]:
    """读取 focused/editable 状态，用于识别输入框和当前节点。"""
    try:
        states = node.get_state_set()
        return (
            bool(states.contains(Atspi.StateType.FOCUSED)),
            bool(states.contains(Atspi.StateType.EDITABLE)),
        )
    except Exception as exc:  # noqa: BLE001 - 远程无障碍对象边界
        diagnostics.once("node_state_unavailable", exc)
        return False, False


def snapshot(node: Any, path: tuple[int, ...], diagnostics: Diagnostics) -> NodeSnapshot:
    """将远程 accessible 对象转换为不可变快照。"""
    focused, editable = safe_states(node, diagnostics)
    return NodeSnapshot(
        path=path,
        role=safe_role(node, diagnostics),
        name=safe_name(node, diagnostics),
        text=safe_text(node, diagnostics),
        child_count=safe_child_count(node, diagnostics),
        focused=focused,
        editable=editable,
    )


def walk_tree(
    root: Any,
    diagnostics: Diagnostics,
    max_depth: int,
) -> Iterator[tuple[Any, NodeSnapshot]]:
    """深度优先遍历 accessible tree，并限制最大深度。"""
    stack: list[tuple[Any, tuple[int, ...], int]] = [(root, (), 0)]
    while stack:
        # 栈中保存远程节点、路径和深度，避免递归过深影响探针稳定性。
        node, path, depth = stack.pop()
        current = snapshot(node, path, diagnostics)
        yield node, current
        if depth >= max_depth:
            continue
        for index in range(current.child_count - 1, -1, -1):
            # 逆序入栈，使弹出顺序仍与控件树的自然顺序一致。
            try:
                child = node.get_child_at_index(index)
                if child is not None:
                    stack.append((child, path + (index,), depth + 1))
            except Exception as exc:  # noqa: BLE001 - 远程节点失效属于预期情况
                diagnostics.once("node_child_unavailable", exc, path=list(path), child_index=index)


def scan_watch_tree(
    root: Any,
    diagnostics: Diagnostics,
    max_depth: int,
    message_window: int,
) -> list[tuple[Any, NodeSnapshot]]:
    """读取 watch 所需的局部控件树，避免访问 Messages 历史子树。

    普通上层节点仍按 ``max_depth`` 遍历；Chats 只取直接会话行，Messages
    只取末尾 ``message_window`` 个直接子节点。窗口外节点不会被远程访问。
    """
    snapshots: list[tuple[Any, NodeSnapshot]] = []
    stack: list[tuple[Any, tuple[int, ...], int]] = [(root, (), 0)]
    while stack:
        node, path, depth = stack.pop()
        current = snapshot(node, path, diagnostics)
        snapshots.append((node, current))

        role = normalized(current.role)
        name = normalized(current.name)
        if role == "list" and name in MESSAGE_CONTAINER_NAMES:
            start = max(0, current.child_count - message_window)
            for index in range(start, current.child_count):
                try:
                    child = node.get_child_at_index(index)
                    if child is not None:
                        child_path = path + (index,)
                        snapshots.append((child, snapshot(child, child_path, diagnostics)))
                except Exception as exc:  # noqa: BLE001 - 远程节点可能在滚动时失效
                    diagnostics.once("message_child_unavailable", exc, path=list(path), child_index=index)
            continue

        if role == "list" and name in CHATS_CONTAINER_NAMES:
            for index in range(current.child_count):
                try:
                    child = node.get_child_at_index(index)
                    if child is not None:
                        child_path = path + (index,)
                        snapshots.append((child, snapshot(child, child_path, diagnostics)))
                except Exception as exc:  # noqa: BLE001 - 远程节点可能在刷新时失效
                    diagnostics.once("chat_child_unavailable", exc, path=list(path), child_index=index)
            continue

        if depth >= max_depth:
            continue
        for index in range(current.child_count - 1, -1, -1):
            try:
                child = node.get_child_at_index(index)
                if child is not None:
                    stack.append((child, path + (index,), depth + 1))
            except Exception as exc:  # noqa: BLE001 - 远程节点失效属于预期情况
                diagnostics.once("node_child_unavailable", exc, path=list(path), child_index=index)
    return snapshots


def find_wechat_application(pattern: re.Pattern[str], diagnostics: Diagnostics) -> Any | None:
    """从 AT-SPI desktop 根节点中查找微信 application。"""
    try:
        desktop = Atspi.get_desktop(0)
    except Exception as exc:  # noqa: BLE001 - session bus/注册表边界
        diagnostics.write(
            "error",
            "desktop_unavailable",
            str(exc),
            display=os.environ.get("DISPLAY", ""),
            dbus_session_bus_address=os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        )
        return None

    # desktop 的一级子节点通常对应各个已注册的应用程序。
    count = safe_child_count(desktop, diagnostics)
    discovered: list[str] = []
    for index in range(count):
        try:
            app = desktop.get_child_at_index(index)
        except Exception as exc:  # noqa: BLE001
            diagnostics.once("desktop_child_unavailable", exc, child_index=index)
            continue
        if app is None:
            continue
        name = safe_name(app, diagnostics)
        discovered.append(name)
        # 只返回名称匹配的应用，后续遍历始终限制在该应用树内。
        if pattern.search(name):
            return app

    diagnostics.write(
        "error",
        "wechat_application_not_found",
        "AT-SPI desktop tree does not contain a matching WeChat application",
        app_pattern=pattern.pattern,
        discovered_applications=discovered,
        display=os.environ.get("DISPLAY", ""),
        dbus_session_bus_address=os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        hint="confirm WeChat is running in the same DISPLAY and D-Bus session",
    )
    return None


def dump_tree(app: Any, diagnostics: Diagnostics, max_depth: int) -> int:
    """输出完整控件树快照，供人工诊断节点结构。"""
    count = 0
    # dump 模式每个节点一行输出，方便离线保存和分析。
    for _, node in walk_tree(app, diagnostics, max_depth):
        emit_json(sys.stdout, {"kind": "node", "observed_at": utc_now(), **node.as_record()})
        count += 1
    diagnostics.write("info", "tree_dump_complete", "accessible tree dump completed", node_count=count)
    return 0


class SendFailure(RuntimeError):
    """可安全输出给 CLI 的发送失败。"""

    def __init__(self, code: str, message: str, **fields: Any) -> None:
        super().__init__(message)
        self.code = code
        self.fields = fields


def _command(
    args: Sequence[str],
    *,
    timeout: float,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """运行 UI 辅助命令并保留可定位的错误信息。"""
    try:
        return subprocess.run(
            list(args),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SendFailure("ui_tool_missing", f"missing UI tool: {args[0]}", tool=args[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise SendFailure("ui_tool_timeout", f"UI tool timed out: {args[0]}", tool=args[0]) from exc
    except OSError as exc:
        raise SendFailure("ui_tool_failed", str(exc), tool=args[0]) from exc


def _require_command(
    args: Sequence[str],
    *,
    timeout: float,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = _command(args, timeout=timeout, input_bytes=input_bytes)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SendFailure(
            "ui_tool_failed",
            detail or f"command exited with status {result.returncode}",
            tool=args[0],
            exit_code=result.returncode,
        )
    return result


def _find_unique_wechat_window() -> str:
    """返回唯一可见微信窗口 ID；不确定时拒绝发送。"""
    # xdotool 使用的正则实现不支持 Python/PCRE 的 ``(?i)`` 内联标记，
    # 因此分别查询常见标题并合并窗口 ID，避免依赖扩展语法。
    ids: set[str] = set()
    for pattern in ("Weixin", "WeChat", "wechat", "微信"):
        result = _command(
            ["xdotool", "search", "--onlyvisible", "--name", pattern],
            timeout=2.0,
        )
        if result.returncode == 1 and not result.stderr.decode("utf-8", errors="replace").strip():
            continue
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise SendFailure(
                "ui_tool_failed",
                detail or "xdotool window search failed",
                tool="xdotool",
                exit_code=result.returncode,
            )
        ids.update(line.strip() for line in result.stdout.decode().splitlines() if line.strip())

    if not ids:
        raise SendFailure("wechat_window_not_found", "no visible WeChat window found")
    if len(ids) != 1:
        raise SendFailure("wechat_window_ambiguous", "multiple visible WeChat windows found", count=len(ids))
    window_id = next(iter(ids))
    # Xvfb 中常见的轻量窗口管理器可能不实现 _NET_ACTIVE_WINDOW；
    # windowfocus 仍能把键盘焦点交给唯一可见的微信窗口。
    _require_command(["xdotool", "windowfocus", "--sync", window_id], timeout=2.0)
    return window_id


def _candidate_input_nodes(
    nodes: Sequence[tuple[Any, NodeSnapshot]],
    *,
    title: str,
    chat_names: Sequence[str],
) -> list[tuple[Any, NodeSnapshot]]:
    """按输入框名称/角色筛选唯一可编辑消息输入节点。"""
    candidates: list[tuple[Any, NodeSnapshot]] = []
    del chat_names  # 当前版本只需用会话标题优先选择输入框。
    for node, current in nodes:
        if not current.editable:
            continue
        name = normalized(current.name)
        role = normalized(current.role)
        if name in {"search", "搜索"}:
            continue
        named_input = name in {normalized(value) for value in INPUT_NAMES}
        editable_text = role in {"text", "entry", "editable text", "text input"}
        if not named_input and not editable_text:
            continue
        candidates.append((node, current))

    # 微信 4.1.1.8 的消息输入框通常复用当前会话标题作为 accessible name；
    # 搜索框同样是可编辑 text，因此先用标题优先消除这类固定歧义。
    preferred = [item for item in candidates if normalized(item[1].name) == normalized(title)]
    if len(preferred) == 1:
        return preferred
    focused = [item for item in candidates if item[1].focused]
    if len(focused) == 1:
        return focused
    return candidates


def _focus_input(node: Any, diagnostics: Diagnostics, timeout: float) -> None:
    """通过 AT-SPI 聚焦输入节点，并轮询确认 focused。"""
    focus_error: Exception | None = None
    try:
        grab_focus = getattr(node, "grab_focus", None)
        if callable(grab_focus):
            result = grab_focus()
            if result is False:
                raise RuntimeError("AT-SPI node refused focus")
        else:
            component = node.get_component_iface()
            if component is None or not component.grab_focus():
                raise RuntimeError("AT-SPI component refused focus")
    except Exception as exc:  # noqa: BLE001 - remote accessibility boundary
        focus_error = exc
    if focus_error is not None:
        raise SendFailure("focus_failed", str(focus_error)) from focus_error

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        focused, _ = safe_states(node, diagnostics)
        if focused:
            return
        time.sleep(0.05)
    raise SendFailure("focus_failed", "input node did not report focused")


def _read_input_text(node: Any, diagnostics: Diagnostics) -> str:
    return safe_text(node, diagnostics)


def _read_clipboard() -> tuple[bool, str]:
    result = _command(["xclip", "-selection", "clipboard", "-o"], timeout=1.0)
    if result.returncode != 0:
        return False, ""
    return True, result.stdout.decode("utf-8", errors="replace")


def _start_clipboard_owner(text: str) -> subprocess.Popen[bytes]:
    """启动一次性 X11 CLIPBOARD owner，等待 Ctrl+V 请求后退出。"""
    try:
        process = subprocess.Popen(
            ["xclip", "-selection", "clipboard", "-in", "-loops", "1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SendFailure("ui_tool_missing", "missing UI tool: xclip", tool="xclip") from exc
    except OSError as exc:
        raise SendFailure("ui_tool_failed", str(exc), tool="xclip") from exc
    try:
        assert process.stdin is not None
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()
    except OSError as exc:
        process.kill()
        raise SendFailure("clipboard_write_failed", str(exc)) from exc
    time.sleep(0.05)
    if process.poll() is not None and process.returncode != 0:
        detail = (process.stderr.read() if process.stderr else b"").decode("utf-8", errors="replace").strip()
        raise SendFailure("clipboard_write_failed", detail or "xclip exited before owning clipboard")
    return process


def _finish_clipboard_owner(process: subprocess.Popen[bytes], *, keep_alive: bool) -> None:
    if keep_alive:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.5)


def _message_echo_observed(app: Any, text: str, diagnostics: Diagnostics, max_depth: int) -> bool:
    snapshots = [snapshot_node.as_record() for _, snapshot_node in walk_tree(app, diagnostics, max_depth)]
    messages_root = find_messages_root(snapshots)
    if messages_root is None:
        return False
    return any(message.text == text for message in split_message_rows(snapshots, messages_root))


def _poll_send_state(
    *,
    app: Any,
    input_node: Any,
    text: str,
    diagnostics: Diagnostics,
    max_depth: int,
    timeout: float,
) -> tuple[bool, bool]:
    """等待输入框清空，并尽力观察 Messages 回显。"""
    deadline = time.monotonic() + timeout
    input_cleared = False
    echo_observed = False
    while time.monotonic() < deadline:
        if not input_cleared:
            input_cleared = _read_input_text(input_node, diagnostics) == ""
        if input_cleared and not echo_observed:
            echo_observed = _message_echo_observed(app, text, diagnostics, max_depth)
        if input_cleared and echo_observed:
            return True, True
        if input_cleared and time.monotonic() + 0.2 >= deadline:
            return True, False
        time.sleep(0.1)
    return input_cleared, echo_observed


def send_message(
    *,
    app_pattern: re.Pattern[str],
    diagnostics: Diagnostics,
    max_depth: int,
    text: str,
    send_key: str,
    timeout_seconds: float,
) -> int:
    """向当前已打开会话发送一条文本消息。"""
    lock_file: Any | None = None
    clipboard_owner: subprocess.Popen[bytes] | None = None
    restore_owner: subprocess.Popen[bytes] | None = None
    original_clipboard_available = False
    original_clipboard = ""
    started_at = utc_now()
    try:
        try:
            lock_file = open(SEND_LOCK_PATH, "a+", encoding="utf-8")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SendFailure("send_busy", "another send operation is in progress") from exc
        except OSError as exc:
            raise SendFailure("send_lock_failed", str(exc)) from exc

        _find_unique_wechat_window()
        app = find_wechat_application(app_pattern, diagnostics)
        if app is None:
            raise SendFailure("wechat_application_not_found", "AT-SPI WeChat application not found")
        nodes = list(walk_tree(app, diagnostics, max_depth))
        snapshots = [current.as_record() for _, current in nodes]
        messages_root = find_messages_root(snapshots)
        if messages_root is None:
            raise SendFailure("chat_context_unavailable", "Messages list is unavailable")
        title = ""
        chats_root = find_chats_root(snapshots)
        chat_names: list[str] = []
        if chats_root is not None:
            chats = parse_chat_rows(snapshots, chats_root.get("path") or ())
            chat_names = [chat.chat_name for chat in chats]
            title = find_pane_title(snapshots, messages_root, chat_names)
        if not title:
            raise SendFailure("chat_context_unavailable", "current chat title is unavailable")

        candidates = _candidate_input_nodes(nodes, title=title, chat_names=chat_names)
        if not candidates:
            raise SendFailure("input_not_found", "message input was not found")
        if len(candidates) != 1:
            raise SendFailure("input_ambiguous", "multiple editable message input candidates found", count=len(candidates))
        input_node, _ = candidates[0]
        _focus_input(input_node, diagnostics, timeout=min(3.0, timeout_seconds))

        try:
            original_clipboard_available, original_clipboard = _read_clipboard()
        except SendFailure as exc:
            diagnostics.write("warning", "clipboard_snapshot_failed", str(exc))
            original_clipboard_available = False
            original_clipboard = ""
        clipboard_owner = _start_clipboard_owner(text)
        _require_command(["xdotool", "key", "--clearmodifiers", "ctrl+v"], timeout=2.0)
        paste_deadline = time.monotonic() + min(3.0, timeout_seconds)
        while time.monotonic() < paste_deadline:
            if _read_input_text(input_node, diagnostics) == text:
                break
            time.sleep(0.1)
        else:
            raise SendFailure("paste_not_verified", "input content did not match stdin text")

        _require_command(["xdotool", "key", "--clearmodifiers", "Return"], timeout=2.0)
        input_cleared, echo_observed = _poll_send_state(
            app=app,
            input_node=input_node,
            text=text,
            diagnostics=diagnostics,
            max_depth=max_depth,
            timeout=max(0.1, timeout_seconds - min(3.0, timeout_seconds)),
        )
        if not input_cleared:
            raise SendFailure("send_timeout", "message input did not clear after send")
        chat_type = "direct"
        if chats_root is not None:
            for chat in parse_chat_rows(snapshots, chats_root.get("path") or ()):
                if chat.chat_name == title:
                    chat_type = "group" if chat.chat_type_hint == "group" else "direct"
                    break
        result = {
            "kind": "send_result",
            "accepted": True,
            "verified": True,
            "chat_name": title,
            "chat_type": chat_type,
            "text_length": len(text),
            "verification": "input_cleared_and_message_observed" if echo_observed else "input_cleared",
            "sent_at": started_at,
        }
        emit_json(sys.stdout, result)
        return 0
    except SendFailure as exc:
        emit_json(
            sys.stdout,
            {
                "kind": "send_result",
                "accepted": False,
                "verified": False,
                "error_code": exc.code,
                "error": str(exc),
                **exc.fields,
            },
        )
        diagnostics.write("error", exc.code, str(exc), **exc.fields)
        return 1
    finally:
        if clipboard_owner is not None:
            _finish_clipboard_owner(clipboard_owner, keep_alive=False)
        if original_clipboard_available:
            try:
                restore_owner = _start_clipboard_owner(original_clipboard)
            except SendFailure as exc:
                diagnostics.write("warning", "clipboard_restore_failed", str(exc))
        if lock_file is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()


class EventWakeup:
    """将 AT-SPI 回调转换为轮询循环可消费的唤醒标记。"""

    def __init__(self, diagnostics: Diagnostics) -> None:
        """初始化事件唤醒器和订阅状态。"""
        self.diagnostics = diagnostics
        self.pending = False
        self.listener: Any | None = None
        self.registered: list[str] = []

    def callback(self, event: Any) -> None:
        """收到 AT-SPI 事件后记录诊断并唤醒下一次扫描。"""
        self.pending = True
        # 回调只设置唤醒标志并记录诊断，不直接扫描树，避免阻塞 GLib 线程。
        source = getattr(event, "source", None)
        self.diagnostics.write(
            "debug",
            "atspi_event",
            "received AT-SPI event",
            event_type=str(getattr(event, "type", "unknown")),
            source_name=safe_name(source, self.diagnostics) if source is not None else "",
            source_role=safe_role(source, self.diagnostics) if source is not None else "",
            detail1=int(getattr(event, "detail1", 0)),
            detail2=int(getattr(event, "detail2", 0)),
        )

    def register(self) -> None:
        """尝试注册消息、焦点和子节点变化事件。"""
        try:
            # 监听器创建失败时仍允许 watch 依靠低频轮询运行。
            self.listener = Atspi.EventListener.new(self.callback)
        except Exception as exc:  # noqa: BLE001
            self.diagnostics.once("event_listener_create_failed", exc)
            return
        for event_type in EVENT_TYPES:
            try:
                self.listener.register(event_type)
                self.registered.append(event_type)
            except Exception as exc:  # noqa: BLE001
                self.diagnostics.once("event_subscription_failed", exc, event_type=event_type)
        self.diagnostics.write(
            "info",
            "event_subscription_complete",
            "AT-SPI event subscriptions attempted",
            registered=self.registered,
        )

    def close(self) -> None:
        """注销已成功注册的 AT-SPI 事件。"""
        if self.listener is None:
            return
        # 只注销实际注册成功的事件类型，避免重复注销产生噪音。
        for event_type in self.registered:
            try:
                self.listener.deregister(event_type)
            except Exception as exc:  # noqa: BLE001
                self.diagnostics.once("event_unsubscribe_failed", exc, event_type=event_type)


def drain_glib_events() -> None:
    """排空 GLib 主循环，处理待处理的 AT-SPI 回调。"""
    context = GLib.MainContext.default()
    # 一次调用可能积累多个回调，因此持续处理直到队列为空。
    while context.pending():
        context.iteration(False)


def watch(
    app_pattern: re.Pattern[str],
    diagnostics: Diagnostics,
    max_depth: int,
    poll_interval: float,
    emit_existing: bool,
    account_id: str,
    chat_type: str,
    bot_name: str,
    message_window: int,
) -> int:
    """监听控件树，只对“当前打开且为群聊”的会话输出消息事件。

    每条输出都是一条完整消息（正文取自 Messages 内容行）；含
    ``@<bot_name>`` 的内容行标记为 is_mention=true。Go 侧据此即可拿到
    正确的 @机器人 消息体，不会被 chats 预览（可能指向更新的非 @ 消息）
    误导。
    """
    wakeup = EventWakeup(diagnostics)
    # 事件唤醒与轮询共享同一份 seen 状态，确保两条路径不会重复输出。
    wakeup.register()
    # 跨轮状态优先使用 Messages 行路径，避免尾部窗口截掉时间头后重放旧消息。
    seen: set[tuple[str, tuple[int, ...], str, str]] = set()
    first_scan = True
    running = True
    last_state: tuple[Any, ...] = ()

    def stop(_signum: int, _frame: Any) -> None:
        """响应终止信号，触发监听循环优雅退出。"""
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    diagnostics.write(
        "info",
        "watch_started",
        "watching the current WeChat group conversation",
        poll_interval_seconds=poll_interval,
        max_depth=max_depth,
        emit_existing=emit_existing,
        chat_type=chat_type,
        bot_name=bot_name or "",
        message_window=message_window,
    )

    try:
        while running:
            # 每轮重新获取应用和控件树，适应窗口切换及远程对象失效。
            started = time.monotonic()
            app = find_wechat_application(app_pattern, diagnostics)
            if app is not None:
                nodes = scan_watch_tree(app, diagnostics, max_depth, message_window)
                snapshots = [sn.as_record() for _, sn in nodes]
                events, report = extract_group_events(
                    snapshots,
                    account_id=account_id,
                    bot_name=bot_name,
                    chat_type_force=chat_type,
                    seen=seen,
                    first_scan=first_scan,
                    emit_existing=emit_existing,
                )
                # 仅在扫描状态发生变化时记录详细诊断，减少 stderr 噪音。
                state = (report.get("group_open"), report.get("reason"), report.get("title"))
                if state != last_state:
                    last_state = state
                    diagnostics.write(
                        "info",
                        "scan_state",
                        "group reading scan state",
                        group_open=report.get("group_open"),
                        reason=report.get("reason") or "",
                        title=report.get("title") or "",
                        chat_rows=len(report.get("chat_rows", [])),
                        message_rows=len(report.get("messages", [])),
                        node_count=len(nodes),
                    )
                for event in events:
                    # 事件正文由解析层生成，observed_at 由实际输出时刻补齐。
                    event["observed_at"] = utc_now()
                    # created_at 是 Go 统一事件的必填时间，使用本次输出观测时刻。
                    event["created_at"] = event["observed_at"]
                    emit_json(sys.stdout, event)
                first_scan = False

            wakeup.pending = False
            while running:
                # 先排空已到达的 AT-SPI 事件，再按剩余时间休眠。
                drain_glib_events()
                elapsed = time.monotonic() - started
                if wakeup.pending or elapsed >= poll_interval:
                    break
                time.sleep(min(0.1, poll_interval - elapsed))
    finally:
        wakeup.close()
        diagnostics.write("info", "watch_stopped", "AT-SPI watch stopped")
    return 0


def positive_int(value: str) -> int:
    """解析大于零的整数 CLI 参数。"""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def positive_float(value: str) -> float:
    """解析大于零的浮点 CLI 参数。"""
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def duration_seconds(value: str) -> float:
    """解析秒或带 s 后缀的正时长。"""
    raw = value.strip().lower()
    if raw.endswith("s"):
        raw = raw[:-1].strip()
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("duration must be a positive number of seconds") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("duration must be greater than 0")
    return parsed


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """解析并校验探针命令行参数。"""
    parser = argparse.ArgumentParser(description="Dump, watch, or send through Linux WeChat's AT-SPI/UI bridge")
    # 所有 CLI 参数在入口统一解析和校验，业务函数只接收可信值。
    parser.add_argument("mode", choices=("dump", "watch", "send"), help="probe operation")
    parser.add_argument("--app-pattern", default=DEFAULT_APP_PATTERN, help="regular expression for app name")
    parser.add_argument("--max-depth", type=positive_int, default=40, help="maximum tree traversal depth")
    parser.add_argument(
        "--message-window",
        type=positive_int,
        default=os.environ.get("MESSAGE_WINDOW", str(DEFAULT_MESSAGE_WINDOW)),
        help="watch 只扫描 Messages 末尾的消息条数（默认 10）",
    )
    parser.add_argument("--poll-interval", type=positive_float, default=1.0, help="watch poll interval in seconds")
    parser.add_argument("--emit-existing", action="store_true", help="emit candidates found in the initial scan")
    parser.add_argument("--verbose", action="store_true", help="do not rate-limit repeated diagnostics")
    parser.add_argument("--account-id", default=os.environ.get("WECHAT_ACCOUNT_ID", "default"))
    parser.add_argument(
        "--chat-type",
        choices=("auto", "direct", "group"),
        default=os.environ.get("WECHAT_CHAT_TYPE", "auto"),
        help="会话类型：auto=按 chats 行自动判定（默认）；direct/group=强制",
    )
    parser.add_argument(
        "--bot-name",
        default=os.environ.get("WECHAT_BOT_NAME", ""),
        help="机器人显示昵称（用于识别 @昵称 提及消息），也可用 WECHAT_BOT_NAME",
    )
    parser.add_argument(
        "--send-key",
        choices=SEND_KEY_VALUES,
        default=os.environ.get("SEND_KEY", "enter"),
        help="发送快捷键：仅支持 enter",
    )
    parser.add_argument(
        "--send-timeout",
        type=duration_seconds,
        default=duration_seconds(os.environ.get("SEND_TIMEOUT", "10s")),
        help="发送总超时，单位秒（默认 10s）",
    )
    args = parser.parse_args(argv)
    try:
        args.compiled_app_pattern = re.compile(args.app_pattern)
    except re.error as exc:
        parser.error(f"invalid --app-pattern: {exc}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """初始化 AT-SPI 并根据参数执行 dump 或 watch。"""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    diagnostics = Diagnostics(verbose=args.verbose)
    try:
        # 在查询 desktop 注册表前显式初始化 AT-SPI，兼容独立运行的探针进程。
        Atspi.init()
    except Exception as exc:  # noqa: BLE001 - session bus/注册表边界
        diagnostics.write(
            "error",
            "atspi_init_failed",
            str(exc),
            display=os.environ.get("DISPLAY", ""),
            dbus_session_bus_address=os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        )
        return 1
    if args.mode == "dump":
        # dump 只执行一次树遍历；watch 则进入持续监听循环。
        app = find_wechat_application(args.compiled_app_pattern, diagnostics)
        if app is None:
            return 1
        return dump_tree(app, diagnostics, args.max_depth)
    if args.mode == "send":
        text = sys.stdin.read()
        if not text or not text.strip():
            emit_json(
                sys.stdout,
                {
                    "kind": "send_result",
                    "accepted": False,
                    "verified": False,
                    "error_code": "invalid_input",
                    "error": "stdin text must contain a non-whitespace character",
                },
            )
            diagnostics.write("error", "invalid_input", "stdin text must contain a non-whitespace character")
            return 1
        return send_message(
            app_pattern=args.compiled_app_pattern,
            diagnostics=diagnostics,
            max_depth=args.max_depth,
            text=text,
            send_key=args.send_key,
            timeout_seconds=args.send_timeout,
        )
    return watch(
        args.compiled_app_pattern,
        diagnostics,
        args.max_depth,
        args.poll_interval,
        args.emit_existing,
        args.account_id,
        args.chat_type,
        args.bot_name,
        args.message_window,
    )


if __name__ == "__main__":
    raise SystemExit(main())
