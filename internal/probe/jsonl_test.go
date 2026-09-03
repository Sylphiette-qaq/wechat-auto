package probe

import (
	"testing"
	"wechatAuto/internal/wechatmodel"
)

func TestParseRecordDerivesIdentity(t *testing.T) {
	r, err := ParseRecord([]byte(`{"chat_name":"群","chat_type":"group","text":"你好"}`))
	if err != nil {
		t.Fatal(err)
	}
	e := r.Event()
	if e.ChatType != wechatmodel.ChatTypeGroup || e.MessageID == "" {
		t.Fatalf("unexpected event: %+v", e)
	}
	if e.Raw["identity_source"] != "derived" {
		t.Fatalf("identity source: %#v", e.Raw)
	}
}

func TestParseRecordRejectsInvalid(t *testing.T) {
	if _, err := ParseRecord([]byte(`{"chat_name":"x","chat_type":"other","text":"x"}`)); err == nil {
		t.Fatal("expected error")
	}
}
