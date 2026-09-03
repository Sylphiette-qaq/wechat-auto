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
import hashlib
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence

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
MESSAGE_CONTAINER_NAMES = frozenset({"messages", "message", "消息", "消息列表"})
INPUT_NAMES = frozenset({"message input", "input", "输入", "输入框", "发送消息"})
IGNORED_TEXT = frozenset({"chats", "contacts", "discover", "me", "微信", "通讯录", "发现", "我"})
EVENT_TYPES = (
    "object:children-changed",
    "object:text-changed",
    "object:state-changed:focused",
    "focus",
)
TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}$")
MESSAGE_NOISE = frozenset(
    {
        "live",
        "send(s)",
        "search",
        "clear",
        "messages",
        "chats",
        "network unavailable",
        "importing chat history...",
        "version updated. loading chat history...",
        "one-by-one",
        "combine and forward",
        "add to favorites",
        "save to computer",
        "delete",
        "cancel",
        "open",
        "close",
    }
)
IMAGE_MARKERS = frozenset({"image", "[image]", "图片", "[图片]"})
UNSUPPORTED_MARKERS = frozenset({"[audio]", "audio", "[video]", "video", "[file]", "file"})


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

    @property
    def identity(self) -> str:
        """根据路径和内容生成当前运行周期内的去重标识。"""
        material = json.dumps(
            [self.path, self.role, self.name, self.text], ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

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


def normalized(value: str) -> str:
    """统一大小写和空白，便于比较 UI 文案。"""
    return " ".join(value.casefold().split())


def is_message_container(node: NodeSnapshot) -> bool:
    """判断节点是否为微信消息列表容器。"""
    return normalized(node.name) in MESSAGE_CONTAINER_NAMES


def is_input_node(node: NodeSnapshot) -> bool:
    """判断节点是否为输入框或可编辑节点。"""
    return node.editable or normalized(node.name) in INPUT_NAMES


def find_message_roots(nodes: Sequence[tuple[Any, NodeSnapshot]]) -> list[tuple[Any, NodeSnapshot]]:
    """提取所有 Messages 列表，作为消息候选扫描边界。"""
    roots = [(raw, node) for raw, node in nodes if is_message_container(node)]
    return roots if roots else list(nodes)


def within_any_root(path: tuple[int, ...], roots: Sequence[NodeSnapshot]) -> bool:
    """判断节点路径是否位于任一消息根节点下。"""
    return any(path[: len(root.path)] == root.path for root in roots)


def message_candidates(nodes: Sequence[tuple[Any, NodeSnapshot]]) -> list[NodeSnapshot]:
    """仅筛选文本和图片消息，排除时间、按钮及状态节点。"""
    root_pairs = find_message_roots(nodes)
    roots = [node for _, node in root_pairs]
    scoped = [node for _, node in nodes if within_any_root(node.path, roots)] if roots != [n for _, n in nodes] else [n for _, n in nodes]
    candidates: list[NodeSnapshot] = []
    for node in scoped:
        content = node.content
        role = normalized(node.role)
        normalized_content = normalized(content)
        is_image = "image" in role or normalized_content in IMAGE_MARKERS
        if is_message_container(node) or is_input_node(node):
            continue
        # Only consume direct message rows, plus explicit image accessibles.
        # This excludes toolbar/status labels that happen to be under the same
        # virtualized pane.
        if not is_image and not any(
            len(node.path) == len(root.path) + 1 and node.path[:-1] == root.path
            for root in roots
        ):
            continue
        if not content and not is_image:
            continue
        if normalized_content in IGNORED_TEXT or normalized_content in MESSAGE_NOISE:
            continue
        if normalized_content in UNSUPPORTED_MARKERS:
            continue
        if TIMESTAMP_RE.fullmatch(content.strip()):
            continue
        # Text rows are leaves in the current WeChat build. Images may expose
        # an image role with no readable alt text; retain them as [image].
        if is_image or node.child_count == 0:
            candidates.append(node)
    return candidates


def message_kind(node: NodeSnapshot) -> str:
    """根据 AT-SPI role 或图片标记返回 text/image 类型。"""
    content = normalized(node.content)
    return "image" if "image" in normalized(node.role) or content in IMAGE_MARKERS else "text"


def message_content(node: NodeSnapshot) -> str:
    """返回业务层消息文本；图片没有描述时使用统一占位符。"""
    content = node.content.strip()
    return content if content else "[image]"


def infer_chat_name(nodes: Sequence[tuple[Any, NodeSnapshot]]) -> str:
    """保守识别当前会话标题，无法确认时返回空字符串。"""
    message_paths = [node.path for _, node in nodes if is_message_container(node)]
    editable_paths = [node.path for _, node in nodes if is_input_node(node)]
    boundary_paths = message_paths + editable_paths
    for _, node in nodes:
        content = node.content.strip()
        if not content or node.editable or normalized(content) in IGNORED_TEXT:
            continue
        role = normalized(node.role)
        if not ("heading" in role or "title" in role):
            continue
        if not boundary_paths or any(node.path < boundary for boundary in boundary_paths):
            return content
    # The Linux client commonly exposes the active conversation title as a
    # short text node immediately before the Messages list, without a heading
    # role. Prefer the nearest non-generic text/label node in that region.
    if message_paths:
        message_root = min(message_paths)
        generic = IGNORED_TEXT | {
            "search",
            "navigation",
            "messages",
            "chats",
            "weixin",
            "wechat",
            "live",
            "send(s)",
            "clear",
        }
        prior: list[NodeSnapshot] = []
        for _, node in nodes:
            content = node.content.strip()
            if not content or node.editable or node.path >= message_root:
                continue
            if normalized(content) in generic or normalized(content) in MESSAGE_NOISE or len(content) > 80:
                continue
            role = normalized(node.role)
            if role in {"text", "label", "heading", "title"}:
                prior.append(node)
        # Prefer a non-editable text widget: in WeChat this is the active
        # conversation title (for example, a contact or group name). Labels
        # such as the Send button may appear later in the accessibility order.
        text_titles = [node for node in prior if normalized(node.role) == "text"]
        cjk_titles = [node for node in text_titles if re.search(r"[\u3400-\u9fff]", node.content)]
        if cjk_titles:
            return cjk_titles[0].content.strip()
    # 无法确认标题时返回空字符串，由上层丢弃事件，避免误路由。
    return ""


def infer_sender(candidate: NodeSnapshot, candidates: Sequence[NodeSnapshot]) -> str:
    """发送者暂不从相邻节点猜测，避免把其他消息误当成发送者。"""
    return ""


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
) -> int:
    """监听 accessible tree，并输出基线之后新增的文本/图片消息。"""
    wakeup = EventWakeup(diagnostics)
    wakeup.register()
    known: set[str] = set()
    first_scan = True
    running = True

    def stop(_signum: int, _frame: Any) -> None:
        """响应终止信号，触发监听循环优雅退出。"""
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    diagnostics.write(
        "info",
        "watch_started",
        "watching the current WeChat accessible tree",
        poll_interval_seconds=poll_interval,
        max_depth=max_depth,
        emit_existing=emit_existing,
    )

    try:
        while running:
            started = time.monotonic()
            app = find_wechat_application(app_pattern, diagnostics)
            if app is not None:
                nodes = list(walk_tree(app, diagnostics, max_depth))
                candidates = message_candidates(nodes)
                chat_name = infer_chat_name(nodes)
                if not candidates:
                    diagnostics.write(
                        "warning",
                        "message_text_not_found",
                        "no readable message-like text nodes were found in the accessible tree",
                        node_count=len(nodes),
                        chat_name=chat_name,
                        hint="run dump mode and preserve its output for AT-SPI diagnosis",
                    )
                for candidate in candidates:
                    identity = candidate.identity
                    is_new = identity not in known
                    known.add(identity)
                    if not is_new or (first_scan and not emit_existing):
                        continue
                    sender = infer_sender(candidate, candidates)
                    emit_json(
                        sys.stdout,
                        {
                            "kind": "message",
                            "account_id": account_id,
                            "chat_name": chat_name,
                            "chat_type": chat_type,
                            "sender_name": sender,
                            "text": message_content(candidate),
                            "message_type": message_kind(candidate),
                            "observed_at": utc_now(),
                            "identity": identity,
                            "identity_source": "derived",
                            "raw": candidate.as_record(),
                        },
                    )
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
        choices=("direct", "group"),
        default=os.environ.get("WECHAT_CHAT_TYPE", "direct"),
        help="current session type; automatic group classification is deferred",
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
