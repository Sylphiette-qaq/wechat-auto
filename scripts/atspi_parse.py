"""纯解析逻辑：把微信 AT-SPI 控件树解析成群聊消息事件。

本模块不依赖 gi（不 import PyGObject/Atspi），只处理结构化的节点快照
（``NodeSnapshot.as_record()`` 形状的 dict），因此可以在宿主机上直接对
抓取的 JSONL fixture 做离线回归测试。

设计要点（对应 README/openspec 中“群聊消息正确读取”的算法）：

* ``Chats`` 列表行 = 每个会话的上下文：会话名 / 群聊或私聊 / 未读数 /
  聚合“提及”标记 / 最新一条的发送者、正文、时间。行内容全部拼在
  accessible name 里，各段（未读、提及、sender 冒号段、时间）均可选。
* ``Messages`` 列表 = 当前打开会话的消息正文行；时间行（可选）作为区段头，
  其后的内容行属于该区段。内容行是消息的**权威正文**——当提及消息不是
  最新一条时（chats 预览指向更新的非 @ 消息），仍以内容行为准。
* 提及判定：消息正文里出现 ``@<WECHAT_BOT_NAME>``（后随空白/U+2005/标点
  或结尾）即为 ``@机器人`` 消息。
* 只处理**当前打开且为群聊**的会话；私聊/无打开群聊不产事件。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

# --------------------------------------------------------------------------
# 常量表（英文文案为实测值，中文文案为预置可扩展项，命中不了的部分会进
# residue/诊断，便于后续校准）。
# --------------------------------------------------------------------------

CHATS_CONTAINER_NAMES = frozenset(
    {
        "chats",
        "chat",
        "conversations",
        "conversation",
        "会话",
        "会话列表",
        "聊天",
        "聊天列表",
    }
)

MESSAGE_CONTAINER_NAMES = frozenset({"messages", "message", "消息", "消息列表"})

INPUT_NAMES = frozenset({"message input", "input", "输入", "输入框", "发送消息"})

IGNORED_TEXT = frozenset({"chats", "contacts", "discover", "me", "微信", "通讯录", "发现", "我"})

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

# 图片/表情/贴纸行标记：内容含这些即按 image 输出（消息模型只有 text|image）。
IMAGE_MARKERS = frozenset(
    {
        "image",
        "[image]",
        "图片",
        "[图片]",
        "photo",
        "[photo]",
        "[sticker]",
        "[贴纸]",
        "[表情]",
        "animated stickers",
    }
)

# 未读计数 token（会话行内会出现，可 IGNORECASE 匹配并剥除）。
UNREAD_PATTERNS = (
    re.compile(r"(\d+)\s*unread\s*message\(s\)", re.IGNORECASE),
    re.compile(r"(\d+)\s*条未读消息"),
    re.compile(r"(\d+)\s*条新消息"),
)

# “你被提及”聚合标记（WeChat 显示在会话行，但不区分具体哪条未读提及）。
MENTION_AGGREGATE_MARKERS = (
    "[you were mentioned]",
    "[有人提到我]",
    "[有人@我]",
    "[有人@了我]",
    "你被提及",
)

# 会话行尾部时间 token。目前实测为 24h HH:MM；其余为预置可扩展项。
TIME_TAIL_PATTERNS = (
    re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s*$"),
    re.compile(r"(昨天|前天|今天|明天|星期[一二三四五六日天]|上午|下午)\s*$"),
    re.compile(r"\d{1,2}月\d{1,2}日\s*$"),
)

# Messages 列表内的时间行（区段头）。
TIME_ROW_PATTERNS = (
    re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$"),
    re.compile(r"^(昨天|前天|今天|明天|星期[一二三四五六日天]|上午|下午)$"),
    re.compile(r"^\d{1,2}月\d{1,2}日$"),
)

_TITLE_TEXT_ROLES = frozenset({"text", "label", "heading", "title", "paragraph"})
_TITLE_GENERIC = IGNORED_TEXT | {
    "search",
    "navigation",
    "messages",
    "chats",
    "weixin",
    "wechat",
    "live",
    "send(s)",
    "clear",
    "official accounts",
    "card view",
}

# @昵称 出现在正文任意位置（前导或句中）都算提及；后随名字字符
# （字母/数字/下划线/CJK，含 U+2005 视为名字内联时的延续）则不算边界。
_MENTION_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _mention_pattern(bot_name: str) -> re.Pattern[str]:
    """构造匹配 ``@昵称``（后随非名字字符或结尾）的正则，带缓存。"""
    pat = _MENTION_RE_CACHE.get(bot_name)
    if pat is None:
        # 负向断言：@昵称 之后不能再接名字字符（否则是 @别人名字的一部分）。
        pat = re.compile(re.escape("@" + bot_name) + r"(?![0-9A-Za-z_\u3400-\u9fff])")
        _MENTION_RE_CACHE[bot_name] = pat
    return pat


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def normalized(value: str) -> str:
    """统一大小写与空白，用于 UI 文案比较。"""
    return " ".join(value.casefold().split())


def clean_text(value: str) -> str:
    """去掉首尾空白（含 \n/\\u2005 等），正文内部原样保留。"""
    return value.strip()


def derive_message_id(chat_name: str, sender: str, text: str, time_block: str) -> str:
    """按 (会话, 发送者, 正文, 区段时间) 派生消息 ID。

    时间参与哈希，使“同文本同发送者但不同时间”的消息可区分；
    时间缺失时退化为不含时间的旧式 ID。
    """
    material = "|".join([chat_name, sender, text, time_block or ""])
    return "derived-time-" + hashlib.sha256(material.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class ChatRowInfo:
    """一条 Chats 列表行解析出的会话信息。"""

    raw_name: str = ""
    chat_name: str = ""
    chat_type_hint: str = "unknown"  # group | direct | unknown
    unread_count: Optional[int] = None
    mentioned: bool = False
    sender: str = ""
    preview_text: str = ""
    row_time: str = ""
    residue: str = ""

    @property
    def has_content(self) -> bool:
        return bool(self.preview_text)

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_name": self.raw_name,
            "chat_name": self.chat_name,
            "chat_type_hint": self.chat_type_hint,
            "unread_count": self.unread_count,
            "mentioned": self.mentioned,
            "sender": self.sender,
            "preview_text": self.preview_text,
            "row_time": self.row_time,
            "residue": self.residue,
        }


@dataclass
class ParsedMessage:
    """Messages 列表中的一条内容行（时间行只作为区段头，不产出）。"""

    text: str
    time_block: str = ""
    message_type: str = "text"
    path: tuple[int, ...] = ()
    raw_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "time_block": self.time_block,
            "message_type": self.message_type,
            "raw_name": self.raw_name,
        }


@dataclass
class OpenChatContext:
    """当前打开会话的上下文（可能为空对象，代表匹配失败）。"""

    row: Optional[ChatRowInfo] = None
    title: str = ""
    matched: bool = False
    reason: str = ""
    messages_root_path: tuple[int, ...] = ()
    chats_root_path: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "row": self.row.as_dict() if self.row else None,
            "title": self.title,
            "matched": self.matched,
            "reason": self.reason,
            "messages_root_path": list(self.messages_root_path),
            "chats_root_path": list(self.chats_root_path),
        }


# --------------------------------------------------------------------------
# 容器定位
# --------------------------------------------------------------------------


def find_list_container(
    nodes: Sequence[dict[str, Any]],
    names: frozenset[str],
    *,
    require_items: bool = False,
) -> Optional[dict[str, Any]]:
    """按名字找第一个匹配的 role=list 容器。"""
    for node in nodes:
        if node.get("role") != "list":
            continue
        if normalized(node.get("name") or "") not in names:
            continue
        if require_items and (node.get("child_count") or 0) <= 0:
            continue
        return node
    return None


def find_messages_root(nodes: Sequence[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """当前打开会话的 Messages 列表容器。"""
    return find_list_container(nodes, MESSAGE_CONTAINER_NAMES)


def find_chats_root(nodes: Sequence[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """侧边栏会话列表容器。"""
    return find_list_container(nodes, CHATS_CONTAINER_NAMES, require_items=True)


def direct_children(
    nodes: Sequence[dict[str, Any]], parent_path: Sequence[int]
) -> list[dict[str, Any]]:
    """返回某容器的一级子节点。"""
    parent = tuple(parent_path)
    return [
        node
        for node in nodes
        if tuple(node.get("path") or ())[: len(parent)] == parent
        and len(node.get("path") or ()) == len(parent) + 1
    ]


def node_content(node: dict[str, Any]) -> str:
    """节点正文：text 优先，其次 accessible name。"""
    text = (node.get("text") or "").strip()
    if text:
        return text
    return (node.get("name") or "").strip()


# --------------------------------------------------------------------------
# Chats 行解析
# --------------------------------------------------------------------------


def _remove_case_insensitive(rest: str, markers: Sequence[str]) -> str:
    for marker in markers:
        rest = re.sub(re.escape(marker), "", rest, flags=re.IGNORECASE)
    return rest


def _collapse_spaces(value: str) -> str:
    """只折叠 ASCII 空格为单空格并去首尾空白。

    不能按 str.split 处理：提及正文里的 U+2005（如 ``@小半夏\u2005你好``）
    属于正文的一部分，必须原样保留，否则无法与 Messages 内容行匹配。
    """
    return re.sub(r" {2,}", " ", value).strip()


def _strip_unread(rest: str) -> tuple[str, Optional[int]]:
    for pat in UNREAD_PATTERNS:
        m = pat.search(rest)
        if m:
            count = int(m.group(1))
            rest = rest[: m.start()] + rest[m.end() :]
            return _collapse_spaces(rest), count
    return rest, None


def _strip_time_tail(rest: str) -> tuple[str, str]:
    """剥掉行尾时间 token，返回 (余部, 时间)。"""
    stripped = rest.strip()
    for pat in TIME_TAIL_PATTERNS:
        m = pat.search(stripped)
        if m:
            time_value = m.group(0).strip()
            before = stripped[: m.start()].strip()
            return before, time_value
    return stripped, ""


def _split_direct_name_content(rest: str) -> tuple[str, str]:
    """私聊行（无 sender 冒号段）：名字=首个空白段，余部=正文。"""
    parts = rest.split(maxsplit=1)
    if not parts:
        return "", ""
    name = parts[0]
    content = parts[1].strip() if len(parts) > 1 else ""
    return name, content


def parse_chat_row(raw_name: str, force_type: str = "auto") -> ChatRowInfo:
    """解析一条 Chats 列表行。

    规则：
    1. IGNORECASE 剥“提及”聚合标记与“未读计数”token，记下布尔/数值；
    2. 剥行尾时间 token；
    3. 余部出现首个 ``: ``（或全角冒号+空白）且该冒号段前有独立 sender
       token → 群聊：chat_name=冒号前去掉 sender 的余部，sender=冒号前
       最后一个 token，正文=冒号后；
    4. 否则视为私聊：chat_name=首个空白段，正文=其余部分；
    5. 残余无法归类的文本进 residue，供诊断校准。
    """
    info = ChatRowInfo(raw_name=raw_name)
    if not raw_name:
        return info

    rest = _remove_case_insensitive(raw_name, MENTION_AGGREGATE_MARKERS)
    if normalized(rest) != normalized(raw_name):
        info.mentioned = True
    rest, unread = _strip_unread(rest)
    info.unread_count = unread
    rest, time_value = _strip_time_tail(rest)
    info.row_time = time_value
    rest = _collapse_spaces(rest)

    if not rest:
        info.residue = raw_name
        return info

    colon = re.search(r"[:：]\s", rest)
    if colon is not None:
        prefix = rest[: colon.start()].strip()
        body = rest[colon.end():].strip()
        # prefix = 可能的会话名 + sender；用最后一个空白段当 sender。
        pieces = prefix.rsplit(maxsplit=1)
        if len(pieces) == 2 and len(pieces[1]) <= 32 and not re.search(r"[:：]", pieces[1]):
            info.chat_name = pieces[0]
            info.sender = pieces[1]
        else:
            info.chat_name = prefix
            info.sender = ""
        info.preview_text = body
        if info.mentioned or (info.sender and body):
            info.chat_type_hint = "group"
        else:
            info.chat_type_hint = "unknown"
    else:
        # 无 sender 冒号段：按私聊解析
        name, content = _split_direct_name_content(rest)
        info.chat_name = name
        info.preview_text = content
        info.chat_type_hint = "direct"

    if force_type in ("group", "direct"):
        info.chat_type_hint = force_type
    if not info.chat_name:
        info.residue = raw_name
    return info


def parse_chat_rows(
    nodes: Sequence[dict[str, Any]],
    chats_root_path: Sequence[int],
    force_type: str = "auto",
) -> list[ChatRowInfo]:
    rows: list[ChatRowInfo] = []
    for item in direct_children(nodes, chats_root_path):
        raw = item.get("name") or ""
        info = parse_chat_row(raw, force_type=force_type)
        if info.chat_name:
            rows.append(info)
    return rows


# --------------------------------------------------------------------------
# 会话窗标题 / Messages 行切分
# --------------------------------------------------------------------------


def find_pane_title(
    nodes: Sequence[dict[str, Any]],
    messages_root: dict[str, Any],
    chat_names: Optional[Sequence[str]] = None,
) -> str:
    """在 Messages 容器之前的短文本里找打开会话的标题。

    实测标题节点被标为 editable=True，因此这里允许 editable；通过
    “标题文本必须与某个 Chats 行会话名一致（chat_names 已知时）+
    路径最深且非通用词”的启发式挑选，避免把错误提示/状态文案当成标题。
    chat_names 为空时退回最深的非通用候选。
    """
    msg_path = tuple(messages_root.get("path") or ())
    name_set = {n for n in chat_names or () if n}
    candidates: list[tuple[int, dict[str, Any]]] = []
    for node in nodes:
        path = tuple(node.get("path") or ())
        if not path or path >= msg_path:
            continue
        if tuple(path)[: len(msg_path)] == msg_path:
            continue  # 不要 Messages 容器内部的节点
        role = normalized(node.get("role") or "")
        if role not in _TITLE_TEXT_ROLES:
            continue
        content = node_content(node)
        if not content:
            continue
        norm = normalized(content)
        if norm in _TITLE_GENERIC or norm in MESSAGE_NOISE or len(content) > 80:
            continue
        if name_set and content not in name_set:
            continue
        candidates.append((len(path), node))

    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return node_content(candidates[0][1])


def split_message_rows(
    nodes: Sequence[dict[str, Any]], messages_root: dict[str, Any]
) -> list[ParsedMessage]:
    """Messages 列表内容行切分。

    时间行视为区段头，切换当前 time_block；内容行（含图片/表情标记行）
    产出 ParsedMessage。过滤系统噪音行。
    """
    results: list[ParsedMessage] = []
    current_time = ""
    for item in direct_children(nodes, messages_root.get("path") or ()):
        raw_name = item.get("name") or ""
        content = clean_text(raw_name) or clean_text(item.get("text") or "")
        role = normalized(item.get("role") or "")
        norm = normalized(content)

        if not content:
            continue
        if any(pat.fullmatch(content) for pat in TIME_ROW_PATTERNS):
            current_time = content
            continue
        if role in ("list", "list item"):
            pass  # 正常消息行
        if norm in IGNORED_TEXT or norm in MESSAGE_NOISE:
            continue
        if "image" in role or any(marker in norm for marker in IMAGE_MARKERS):
            message_type = "image"
        else:
            message_type = "text"
        results.append(
            ParsedMessage(
                text=content,
                time_block=current_time,
                message_type=message_type,
                path=tuple(item.get("path") or ()),
                raw_name=raw_name,
            )
        )
    return results


# --------------------------------------------------------------------------
# 打开会话匹配 / 提及判定 / 事件组装
# --------------------------------------------------------------------------


def match_open_chat(
    chats: Sequence[ChatRowInfo],
    title: str,
    messages: Sequence[ParsedMessage],
) -> Optional[ChatRowInfo]:
    """把“当前打开会话”绑定到一条 Chats 行。

    评分：标题==chat_name 权重最高；最新内容行正文/区段时间匹配预览次之。
    匹配不到返回 None（调用方降级，不阻塞 @昵称 逐行判定）。
    """
    if not chats:
        return None
    last_text = messages[-1].text if messages else ""
    last_time = messages[-1].time_block if messages else ""

    best: Optional[ChatRowInfo] = None
    best_score = -1
    for chat in chats:
        score = 0
        if title and chat.chat_name and chat.chat_name == title:
            score += 10
        if last_text and chat.preview_text:
            if chat.preview_text == last_text:
                score += 3
            elif chat.preview_text.startswith(last_text) or last_text.startswith(chat.preview_text):
                score += 1
        if last_time and chat.row_time == last_time:
            score += 2
        if score > best_score:
            best_score = score
            best = chat
    if best_score <= 0:
        return None
    return best


def is_mention_of(text: str, bot_name: str) -> bool:
    """正文是否包含 ``@昵称``（任意位置，后随非名字字符或结尾）。"""
    if not bot_name or not text:
        return False
    return bool(_mention_pattern(bot_name).search(text))


def build_message_event(
    *,
    account_id: str,
    chat: ChatRowInfo,
    parsed: ParsedMessage,
    sender: str,
    is_mention: bool,
    message_time: str,
    observed_at: str,
    unread_count: Optional[int],
    mentioned_aggregate: bool,
    row_path: tuple[int, ...],
) -> dict[str, Any]:
    """组装探针 message JSONL 记录（schema 与 Go Record 兼容）。"""
    message_id = derive_message_id(chat.chat_name, sender, parsed.text, parsed.time_block)
    return {
        "kind": "message",
        "account_id": account_id,
        "chat_name": chat.chat_name,
        "chat_type": "group",
        "sender_name": sender,
        "message_id": message_id,
        "text": parsed.text,
        "message_type": parsed.message_type,
        "is_mention": is_mention,
        "message_time": message_time,
        "unread_count": unread_count if unread_count is not None else 0,
        "mentioned": mentioned_aggregate,
        "observed_at": observed_at,
        "identity": message_id,
        "identity_source": "derived",
        "raw": {
            "message_row": {
                "path": list(parsed.path) or list(row_path),
                "name": parsed.raw_name,
                "role": "list item",
            },
            "chat_row": chat.as_dict(),
        },
    }


def extract_group_events(
    nodes: Sequence[dict[str, Any]],
    *,
    account_id: str,
    bot_name: str,
    chat_type_force: str,
    seen: set[tuple[str, str, str]],
    first_scan: bool,
    emit_existing: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """主流程：定位群聊并产出 message 事件（纯函数，供 watch 调用与单测）。

    返回 (events, report)。report 含诊断信息；seen 为跨轮状态
    （键 = (chat_name, text, time_block)）。
    """
    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {"group_open": False, "reason": ""}

    chats_root = find_chats_root(nodes)
    messages_root = find_messages_root(nodes)
    report["chats_root_path"] = list(chats_root["path"]) if chats_root else []
    report["messages_root_path"] = list(messages_root["path"]) if messages_root else []

    chats = (
        parse_chat_rows(nodes, chats_root["path"], force_type=chat_type_force)
        if chats_root
        else []
    )
    report["chat_rows"] = [c.as_dict() for c in chats]

    if messages_root is None:
        report["reason"] = "no_messages_list"
        return events, report

    title = find_pane_title(nodes, messages_root, [c.chat_name for c in chats])
    report["title"] = title
    messages = split_message_rows(nodes, messages_root)
    report["messages"] = [m.as_dict() for m in messages]

    # 1) 判定群聊：chats 行 hint 优先，其次 @ 提及行，其次显式 force。
    ctx = OpenChatContext(title=title, messages_root_path=tuple(messages_root["path"] or ()))
    ctx.chats_root_path = tuple(chats_root["path"]) if chats_root else ()
    chat = match_open_chat(chats, title, messages)
    if chat is None and len(chats) == 1:
        chat = chats[0]
    ctx.row = chat

    def _with_group_hint(base: Optional[ChatRowInfo], name: str) -> ChatRowInfo:
        if base is None:
            return ChatRowInfo(
                chat_name=name or "(unknown)",
                chat_type_hint="group",
                unread_count=None,
                mentioned=False,
            )
        if base.chat_type_hint != "group":
            base = ChatRowInfo(
                raw_name=base.raw_name,
                chat_name=base.chat_name,
                chat_type_hint="group",
                unread_count=base.unread_count,
                mentioned=base.mentioned,
                sender=base.sender,
                preview_text=base.preview_text,
                row_time=base.row_time,
                residue=base.residue,
            )
        return base

    is_group = False
    if chat is not None and chat.chat_type_hint == "group":
        is_group = True
    elif chat_type_force == "group":
        is_group = True
        chat = _with_group_hint(chat, title)
    elif bot_name and any(is_mention_of(m.text, bot_name) for m in messages):
        # 找不到群聊行（标题对不上/行滚出可见区），但内容里出现了 @机器人：
        # 仍按群聊读，避免漏。注意：chat hint 已判定为 direct 的行不适用
        # 此兜底——私聊正文里出现 "@昵称" 字样不代表是群聊提及。
        if chat is None or chat.chat_type_hint == "unknown":
            if not title:
                report["reason"] = "chat_context_unavailable"
                report["context"] = ctx.as_dict()
                return events, report
            is_group = True
            chat = _with_group_hint(chat, title)

    if not is_group:
        report["reason"] = "open_chat_not_group"
        report["group_open"] = False
        report["context"] = ctx.as_dict()
        return events, report

    if chat is None:
        report["reason"] = "chat_context_unavailable"
        report["group_open"] = False
        report["context"] = ctx.as_dict()
        return events, report

    ctx.row = chat
    report["group_open"] = True
    report["context"] = ctx.as_dict()

    # 2) 发送者绑定：最新一条内容行若与 chats 预览正文一致（或预览被截断
    #    时以其为前缀），归属预览里的 sender；更早消息 sender 不可得，留空。
    #    注意：不要求区段时间与预览时间一致——实测 Messages 区段头可能滞后
    #    （新消息仍挂在旧区段下），正文一致才是可靠信号。
    # 2) 发送者绑定：仅当“最新一条内容行”与 chats 预览正文一致（或预览被
    #    截断时以其为前缀）时，归属预览里的 sender；更早消息 sender 不可得，
    #    留空。绑定按行位置（最新一条），不按文本——同一文本可能多次出现，
    #    只有最新那条才是预览所指。
    #    注意：不要求区段时间与预览时间一致——实测 Messages 区段头可能滞后
    #    （新消息仍挂在旧区段下），正文一致才是可靠信号。
    binding_index: Optional[int] = None
    if chat.sender and messages:
        last = messages[-1]
        bound = False
        if chat.preview_text:
            if last.text == chat.preview_text:
                bound = True
            elif chat.preview_text.endswith("…") and last.text.startswith(
                chat.preview_text.rstrip("…")
            ):
                bound = True
        if bound:
            binding_index = len(messages) - 1

    # 3) 增量输出
    for index, parsed in enumerate(messages):
        key = (chat.chat_name, parsed.text, parsed.time_block)
        is_new = key not in seen
        seen.add(key)
        if not is_new:
            continue
        if first_scan and not emit_existing:
            continue
        is_latest = index == binding_index
        sender = chat.sender if is_latest else ""
        is_mention = is_mention_of(parsed.text, bot_name)
        message_time = parsed.time_block
        # 最新且已绑定发送者的行：以 chats 行的权威时间为准（区段头可能滞后）。
        if is_latest and chat.row_time:
            message_time = chat.row_time
        events.append(
            build_message_event(
                account_id=account_id,
                chat=chat,
                parsed=parsed,
                sender=sender,
                is_mention=is_mention,
                message_time=message_time,
                observed_at="",
                unread_count=chat.unread_count,
                mentioned_aggregate=chat.mentioned,
                row_path=parsed.path,
            )
        )
    return events, report
