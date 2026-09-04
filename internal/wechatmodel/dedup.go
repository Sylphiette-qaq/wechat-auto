package wechatmodel

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"sync"
)

// DeriveIdentity 在无原生消息 ID 时，根据消息上下文生成确定性的派生 ID。
// 当前 CLI 实现始终将这类 ID 标记为 derived。
func DeriveIdentity(accountID, chatID, senderName, text string) string {
	// 使用不可见分隔符避免不同字段拼接后产生歧义。
	key := strings.Join([]string{accountID, chatID, senderName, text}, "\x00")
	// SHA-256 提供稳定且长度固定的身份摘要。
	sum := sha256.Sum256([]byte(key))
	return "derived-" + hex.EncodeToString(sum[:])
}

// DeriveChatID 在 AT-SPI 只有会话标题时生成稳定的会话 ID。
// ID 绑定账号，避免多账号场景下同名会话互相冲突。
func DeriveChatID(accountID, chatName string) string {
	// 账号和会话名共同构成派生输入。
	key := strings.Join([]string{accountID, chatName}, "\x00")
	sum := sha256.Sum256([]byte(key))
	return "derived-chat-" + hex.EncodeToString(sum[:])
}

// DedupKey 组合事件的账号、会话和消息 ID，作为进程内去重键。
func DedupKey(event Event) string {
	return strings.Join([]string{event.AccountID, event.ChatID, event.MessageID}, "\x00")
}

// Deduper 在当前进程生命周期内抑制重复观测。
type Deduper struct {
	mu   sync.Mutex
	seen map[string]struct{}
}

// NewDeduper 创建一个已初始化内部集合的去重器。
func NewDeduper() *Deduper { return &Deduper{seen: make(map[string]struct{})} }

// Add 记录已校验的去重键，并返回它是否是首次出现。
// 调用方应在进入去重器前完成事件身份校验。
func (d *Deduper) Add(id string) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	// 已见过的键不再重复输出。
	if _, ok := d.seen[id]; ok {
		return false
	}
	// 记录新键，后续相同消息会被过滤。
	d.seen[id] = struct{}{}
	return true
}
