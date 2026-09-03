package probe

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"wechat-auto/internal/wechatmodel"
)

// Record is the intentionally small JSON contract consumed from the Python
// AT-SPI probe. Unknown fields are retained in Raw for diagnostics.
type Record struct {
	AccountID   string                  `json:"account_id"`
	ChatID      string                  `json:"chat_id"`
	ChatName    string                  `json:"chat_name"`
	ChatType    wechatmodel.ChatType    `json:"chat_type"`
	SenderID    string                  `json:"sender_id"`
	SenderName  string                  `json:"sender_name"`
	MessageID   string                  `json:"message_id"`
	Text        string                  `json:"text"`
	MessageType wechatmodel.MessageType `json:"message_type"`
	IsMention   bool                    `json:"is_mention"`
	CreatedAt   time.Time               `json:"created_at"`
	Raw         map[string]any          `json:"-"`
}

func ParseRecord(line []byte) (Record, error) {
	line = bytes.TrimSpace(line)
	if len(line) == 0 {
		return Record{}, fmt.Errorf("empty JSONL record")
	}
	var rec Record
	if err := json.Unmarshal(line, &rec); err != nil {
		return Record{}, fmt.Errorf("parse probe JSON: %w", err)
	}
	if rec.ChatType != wechatmodel.ChatTypeDirect && rec.ChatType != wechatmodel.ChatTypeGroup {
		return Record{}, fmt.Errorf("invalid chat_type %q", rec.ChatType)
	}
	if strings.TrimSpace(rec.ChatName) == "" || strings.TrimSpace(rec.Text) == "" {
		return Record{}, fmt.Errorf("chat_name and text are required")
	}
	if rec.CreatedAt.IsZero() {
		rec.CreatedAt = time.Now().UTC()
	}
	var raw map[string]any
	if err := json.Unmarshal(line, &raw); err == nil {
		rec.Raw = raw
	}
	return rec, nil
}

func (r Record) Event() wechatmodel.Event {
	if strings.TrimSpace(r.ChatID) == "" {
		r.ChatID = wechatmodel.DeriveChatID(r.AccountID, r.ChatName)
	}
	id := r.MessageID
	identitySource := "native"
	if strings.TrimSpace(id) == "" {
		id = wechatmodel.DeriveIdentity(r.AccountID, r.ChatID, r.SenderName, r.Text)
		identitySource = "derived"
	}
	if r.Raw == nil {
		r.Raw = map[string]any{}
	}
	r.Raw["identity_source"] = identitySource
	return wechatmodel.Event{AccountID: r.AccountID, ChatID: r.ChatID, ChatName: r.ChatName, ChatType: r.ChatType, SenderID: r.SenderID, SenderName: r.SenderName, MessageID: id, Text: r.Text, MessageType: r.MessageType, IsMention: r.IsMention, CreatedAt: r.CreatedAt, Raw: r.Raw}
}
