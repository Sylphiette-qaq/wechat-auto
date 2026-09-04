package probe

import (
	"testing"
	"wechat-auto/internal/wechatmodel"
)

func TestParseRecordPreservesCompleteRecord(t *testing.T) {
	// Go 解析层只解码完整记录，不负责再派生或补全身份字段。
	r, err := ParseRecord([]byte(`{"account_id":"acc","chat_id":"chat-1","chat_name":"群","chat_type":"group","sender_name":"半夏","message_id":"msg-1","text":"你好","message_type":"text","created_at":"2026-09-04T00:00:00Z"}`))
	if err != nil {
		t.Fatal(err)
	}
	e := r.Event()
	if e.ChatType != wechatmodel.ChatTypeGroup || e.MessageID != "msg-1" || e.ChatID != "chat-1" {
		t.Fatalf("unexpected event: %+v", e)
	}
}

func TestParseRecordRejectsMalformedJSON(t *testing.T) {
	// Go 层只拒绝无法解码的传输内容，业务字段合法性由探针保证。
	if _, err := ParseRecord([]byte(`{"account_id":"acc"`)); err == nil {
		t.Fatal("expected error")
	}
}
