package wechatmodel

import (
	"context"
	"time"
)

// ChatType identifies whether an event belongs to a direct or group chat.
type ChatType string

const (
	ChatTypeDirect ChatType = "direct"
	ChatTypeGroup  ChatType = "group"
)

// Event is the transport-neutral message model emitted by the listener.
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

type ChatTarget struct {
	ChatID   string   `json:"chat_id"`
	ChatName string   `json:"chat_name"`
	ChatType ChatType `json:"chat_type"`
}

type MessageType string

const (
	MessageTypeText  MessageType = "text"
	MessageTypeImage MessageType = "image"
)

type MessageSender interface {
	SendText(ctx context.Context, target ChatTarget, text string) error
}

type ChatListener interface {
	Start(ctx context.Context) (<-chan Event, error)
	Health(ctx context.Context) error
	Close() error
}
