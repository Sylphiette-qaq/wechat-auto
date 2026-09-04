package main

import (
	"bytes"
	"context"
	"strings"
	"testing"
)

func TestRunSendForwardsStdinAndResult(t *testing.T) {
	var stdout, stderr bytes.Buffer
	err := run(
		context.Background(),
		"send",
		"cat",
		nil,
		strings.NewReader(`{"kind":"send_result","accepted":true}`+"\n"),
		&stdout,
		&stderr,
	)
	if err != nil {
		t.Fatal(err)
	}
	if stdout.String() != `{"kind":"send_result","accepted":true}`+"\n" {
		t.Fatalf("unexpected stdout: %q", stdout.String())
	}
	if stderr.Len() != 0 {
		t.Fatalf("unexpected stderr: %q", stderr.String())
	}
}

func TestRunSendReturnsErrorForRejectedResult(t *testing.T) {
	var stdout, stderr bytes.Buffer
	err := run(
		context.Background(),
		"send",
		"cat",
		nil,
		strings.NewReader(`{"kind":"send_result","accepted":false,"error_code":"input_not_found"}`+"\n"),
		&stdout,
		&stderr,
	)
	if err == nil || !strings.Contains(err.Error(), "input_not_found") {
		t.Fatalf("expected rejected send error, got %v", err)
	}
	if stdout.Len() == 0 {
		t.Fatal("rejected result must still be forwarded")
	}
}
