package probe

import (
	"bytes"
	"encoding/json"
	"fmt"
	"time"

	"wechat-auto/internal/wechatmodel"
)

// Record 是 Go 消费 Python AT-SPI 探针时使用的完整 JSON 契约。
// 身份、类型和时间字段由探针在输出边界填充；未声明字段保留到 Raw。
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

// ParseRecord 只负责把一行探针 JSONL 解码为 Record。
// 业务字段的完整性和身份派生由 Python 探针在输出边界保证。
func ParseRecord(line []byte) (Record, error) {
	// 去掉首尾空白后交给 JSON 解码器，空行会作为协议错误返回。
	line = bytes.TrimSpace(line)
	var rec Record
	if err := json.Unmarshal(line, &rec); err != nil {
		return Record{}, fmt.Errorf("parse probe JSON: %w", err)
	}
	// 再解码一份原始 map，只为保留诊断字段，不在 Go 层重新校验业务值。
	var raw map[string]any
	if err := json.Unmarshal(line, &raw); err != nil {
		return Record{}, fmt.Errorf("parse probe raw JSON: %w", err)
	}
	rec.Raw = raw
	return rec, nil
}

// Event 将已完整的 Record 做字段映射，不承担校验、补默认值或 ID 派生。
func (r Record) Event() wechatmodel.Event {
	return wechatmodel.Event{
		AccountID:   r.AccountID,
		ChatID:      r.ChatID,
		ChatName:    r.ChatName,
		ChatType:    r.ChatType,
		SenderID:    r.SenderID,
		SenderName:  r.SenderName,
		MessageID:   r.MessageID,
		Text:        r.Text,
		MessageType: r.MessageType,
		IsMention:   r.IsMention,
		CreatedAt:   r.CreatedAt,
		Raw:         r.Raw,
	}
}
