package wechatmodel

import "testing"

func TestDeduper(t *testing.T) {
	// 首次键应通过、重复键应被拒绝；派生 ID 应保持确定性。
	d := NewDeduper()
	if !d.Add("x") || d.Add("x") {
		t.Fatal("unexpected dedup behavior")
	}
	if DeriveIdentity("a", "b", "c", "d") != DeriveIdentity("a", "b", "c", "d") {
		t.Fatal("identity must be deterministic")
	}
}
