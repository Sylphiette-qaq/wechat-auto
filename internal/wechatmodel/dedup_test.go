package wechatmodel

import "testing"

func TestDeduper(t *testing.T) {
	d := NewDeduper()
	if !d.Add("x") || d.Add("x") || d.Add("") {
		t.Fatal("unexpected dedup behavior")
	}
	if DeriveIdentity("a", "b", "c", "d") != DeriveIdentity("a", "b", "c", "d") {
		t.Fatal("identity must be deterministic")
	}
}
