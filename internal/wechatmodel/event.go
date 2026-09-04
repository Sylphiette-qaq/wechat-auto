package wechatmodel

import (
	"context"
	"time"
)

// ChatType 标识事件所属的一对一会话或群聊。
type ChatType string

const (
	// ChatTypeDirect 表示一对一会话。
	ChatTypeDirect ChatType = "direct"
	// ChatTypeGroup 表示群聊会话。
	ChatTypeGroup ChatType = "group"
)

// Event 是监听层向下游输出的、与传输方式无关的统一消息模型。
type Event struct {
	AccountID   string         `json:"account_id"`
	ChatID      string         `json:"chat_id"`
	ChatName    string         `json:"chat_name"`
	ChatType    ChatType       `json:"chat_type"`
	SenderID    string         `json:"sender_id,omitempty"`
	SenderName  string         `json:"sender_name,omitempty"`
	MessageID   string         `json:"message_id"`
	Text        string         `json:"text"`
	MessageType MessageType    `json:"message_type"`
	IsMention   bool           `json:"is_mention"`
	CreatedAt   time.Time      `json:"created_at"`
	Raw         map[string]any `json:"raw,omitempty"`
}

// ChatTarget 描述发送消息时的目标会话。
type ChatTarget struct {
	ChatID   string   `json:"chat_id"`
	ChatName string   `json:"chat_name"`
	ChatType ChatType `json:"chat_type"`
}

// MessageType 区分当前支持的消息内容类型。
type MessageType string

const (
	// MessageTypeText 表示普通文本消息。
	MessageTypeText MessageType = "text"
	// MessageTypeImage 表示图片、表情或贴纸类消息。
	MessageTypeImage MessageType = "image"
)

// MessageSender 定义向指定会话发送文本的能力边界。
type MessageSender interface {
	SendText(ctx context.Context, target ChatTarget, text string) error
}

// ChatListener 定义监听器的启动、健康检查和关闭生命周期。
type ChatListener interface {
	Start(ctx context.Context) (<-chan Event, error)
	Health(ctx context.Context) error
	Close() error
}
