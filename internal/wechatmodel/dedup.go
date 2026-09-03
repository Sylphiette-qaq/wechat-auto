package wechatmodel

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"sync"
)

// DeriveIdentity returns a deterministic ID for an observed message when the
// accessibility tree does not expose native IDs. The source is always
// "derived" for this first CLI implementation.
func DeriveIdentity(accountID, chatID, senderName, text string) string {
	key := strings.Join([]string{accountID, chatID, senderName, text}, "\x00")
	sum := sha256.Sum256([]byte(key))
	return "derived-" + hex.EncodeToString(sum[:])
}

// DeriveChatID provides a stable fallback when AT-SPI exposes only the chat
// title. It is deliberately scoped by account to avoid cross-account clashes.
func DeriveChatID(accountID, chatName string) string {
	key := strings.Join([]string{accountID, chatName}, "\x00")
	sum := sha256.Sum256([]byte(key))
	return "derived-chat-" + hex.EncodeToString(sum[:])
}

func DedupKey(event Event) string {
	return strings.Join([]string{event.AccountID, event.ChatID, event.MessageID}, "\x00")
}

// Deduper suppresses duplicate observations while the process is running.
type Deduper struct {
	mu   sync.Mutex
	seen map[string]struct{}
}

func NewDeduper() *Deduper { return &Deduper{seen: make(map[string]struct{})} }

// Add reports whether id was new. Empty IDs are rejected so malformed input
// cannot accidentally suppress all subsequent events.
func (d *Deduper) Add(id string) bool {
	if id == "" {
		return false
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, ok := d.seen[id]; ok {
		return false
	}
	d.seen[id] = struct{}{}
	return true
}
