#!/usr/bin/env python3
"""Minimal AT-SPI probe for Linux WeChat.

The probe has two modes:

* ``dump`` prints the accessible tree as JSON Lines.
* ``watch`` establishes a baseline and prints newly observed message-like text
  nodes as JSON Lines. AT-SPI events wake the scanner early; polling remains the
  compatibility path when WeChat does not emit useful accessibility events.

Diagnostics are written to stderr as JSON Lines so stdout can be consumed by
the Go CLI without mixing records from the two streams.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Sequence

from atspi_parse import extract_group_events

try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
except (ImportError, ValueError) as exc:  # pragma: no cover - container dependency
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
EVENT_TYPES = (
    "object:children-changed",
    "object:text-changed",
    "object:state-changed:focused",
    "focus",
)


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
    except Exception as exc:  # noqa: BLE001 - remote accessibility boundary
        diagnostics.once("node_name_unavailable", exc)
        return ""


def safe_role(node: Any, diagnostics: Diagnostics) -> str:
    """安全读取节点 role 名称。"""
    try:
        return str(node.get_role_name() or "unknown")
    except Exception as exc:  # noqa: BLE001 - remote accessibility boundary
        diagnostics.once("node_role_unavailable", exc)
        return "unknown"


def safe_child_count(node: Any, diagnostics: Diagnostics) -> int:
    """安全读取子节点数量并规范为非负整数。"""
    try:
        return max(0, int(node.get_child_count()))
    except Exception as exc:  # noqa: BLE001 - remote accessibility boundary
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
    except Exception as exc:  # noqa: BLE001 - interface availability varies by node
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
    except Exception as exc:  # noqa: BLE001 - remote accessibility boundary
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
        node, path, depth = stack.pop()
        current = snapshot(node, path, diagnostics)
        yield node, current
        if depth >= max_depth:
            continue
        for index in range(current.child_count - 1, -1, -1):
            try:
                child = node.get_child_at_index(index)
                if child is not None:
                    stack.append((child, path + (index,), depth + 1))
            except Exception as exc:  # noqa: BLE001 - stale remote nodes are expected
                diagnostics.once("node_child_unavailable", exc, path=list(path), child_index=index)


def find_wechat_application(pattern: re.Pattern[str], diagnostics: Diagnostics) -> Any | None:
    """从 AT-SPI desktop 根节点中查找微信 application。"""
    try:
        desktop = Atspi.get_desktop(0)
    except Exception as exc:  # noqa: BLE001 - session bus/registry boundary
        diagnostics.write(
            "error",
            "desktop_unavailable",
            str(exc),
            display=os.environ.get("DISPLAY", ""),
            dbus_session_bus_address=os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        )
        return None

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
    for _, node in walk_tree(app, diagnostics, max_depth):
        emit_json(sys.stdout, {"kind": "node", "observed_at": utc_now(), **node.as_record()})
        count += 1
    diagnostics.write("info", "tree_dump_complete", "accessible tree dump completed", node_count=count)
    return 0


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
        for event_type in self.registered:
            try:
                self.listener.deregister(event_type)
            except Exception as exc:  # noqa: BLE001
                self.diagnostics.once("event_unsubscribe_failed", exc, event_type=event_type)


def drain_glib_events() -> None:
    """排空 GLib 主循环，处理待处理的 AT-SPI 回调。"""
    context = GLib.MainContext.default()
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
) -> int:
    """监听控件树，只对“当前打开且为群聊”的会话输出消息事件。

    每条输出都是一条完整消息（正文取自 Messages 内容行）；含
    ``@<bot_name>`` 的内容行标记为 is_mention=true。Go 侧据此即可拿到
    正确的 @机器人 消息体，不会被 chats 预览（可能指向更新的非 @ 消息）
    误导。
    """
    wakeup = EventWakeup(diagnostics)
    wakeup.register()
    # 跨轮状态：(chat_name, text, time_block)，用于增量输出与防抖。
    seen: set[tuple[str, str, str]] = set()
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
    )

    try:
        while running:
            started = time.monotonic()
            app = find_wechat_application(app_pattern, diagnostics)
            if app is not None:
                nodes = list(walk_tree(app, diagnostics, max_depth))
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
                    event["observed_at"] = utc_now()
                    emit_json(sys.stdout, event)
                first_scan = False

            wakeup.pending = False
            while running:
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


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """解析并校验探针命令行参数。"""
    parser = argparse.ArgumentParser(description="Dump or watch Linux WeChat's AT-SPI tree")
    parser.add_argument("mode", choices=("dump", "watch"), help="probe operation")
    parser.add_argument("--app-pattern", default=DEFAULT_APP_PATTERN, help="regular expression for app name")
    parser.add_argument("--max-depth", type=positive_int, default=40, help="maximum tree traversal depth")
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
        # Explicit initialization is required before querying the desktop
        # registry when the probe runs outside an existing AT-SPI client.
        Atspi.init()
    except Exception as exc:  # noqa: BLE001 - session bus/registry boundary
        diagnostics.write(
            "error",
            "atspi_init_failed",
            str(exc),
            display=os.environ.get("DISPLAY", ""),
            dbus_session_bus_address=os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        )
        return 1
    if args.mode == "dump":
        app = find_wechat_application(args.compiled_app_pattern, diagnostics)
        if app is None:
            return 1
        return dump_tree(app, diagnostics, args.max_depth)
    return watch(
        args.compiled_app_pattern,
        diagnostics,
        args.max_depth,
        args.poll_interval,
        args.emit_existing,
        args.account_id,
        args.chat_type,
        args.bot_name,
    )


if __name__ == "__main__":
    raise SystemExit(main())
