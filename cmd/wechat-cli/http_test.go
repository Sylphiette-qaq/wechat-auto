package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"wechat-auto/internal/wechatmodel"
)

func readyHTTPRuntime() *httpRuntime {
	rt := newHTTPRuntime(httpConfig{})
	rt.setReady(true)
	return rt
}

func decodeHTTPResponse(t *testing.T, rr *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var body map[string]any
	if err := json.Unmarshal(rr.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode response JSON: %v; body=%q", err, rr.Body.String())
	}
	return body
}

func TestHandleSendRejectsInvalidJSONRequests(t *testing.T) {
	cases := []struct {
		name        string
		contentType string
		body        string
	}{
		{name: "non-json content type", contentType: "text/plain", body: `{"text":"hello"}`},
		{name: "malformed json", contentType: "application/json", body: `{"text":`},
		{name: "unknown field", contentType: "application/json", body: `{"text":"hello","chat_name":"other"}`},
		{name: "missing text", contentType: "application/json", body: `{}`},
		{name: "non-string text", contentType: "application/json", body: `{"text":123}`},
		{name: "empty text", contentType: "application/json", body: `{"text":""}`},
		{name: "whitespace text", contentType: "application/json", body: `{"text":" \t\n "}`},
		{name: "multiple json values", contentType: "application/json", body: `{"text":"hello"} {}`},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rt := readyHTTPRuntime()
			req := httptest.NewRequest(http.MethodPost, "/v1/messages/send", strings.NewReader(tc.body))
			req.Header.Set("Content-Type", tc.contentType)
			rr := httptest.NewRecorder()

			rt.ServeHTTP(rr, req)

			if rr.Code != http.StatusBadRequest {
				t.Fatalf("status=%d, want %d; body=%q", rr.Code, http.StatusBadRequest, rr.Body.String())
			}
			body := decodeHTTPResponse(t, rr)
			if body["error_code"] != "invalid_input" {
				t.Fatalf("error_code=%v, want invalid_input", body["error_code"])
			}
		})
	}
}

func TestHandleSendRejectsBodyOverOneMiB(t *testing.T) {
	rt := readyHTTPRuntime()
	// The body is intentionally larger than maxHTTPBody; MaxBytesReader must
	// reject it before the send probe can be invoked.
	body := `{"text":"` + strings.Repeat("x", maxHTTPBody) + `"}`
	req := httptest.NewRequest(http.MethodPost, "/v1/messages/send", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	rt.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status=%d, want %d", rr.Code, http.StatusBadRequest)
	}
	if got := decodeHTTPResponse(t, rr)["error_code"]; got != "invalid_input" {
		t.Fatalf("error_code=%v, want invalid_input", got)
	}
}

func TestHandlersReturnNotReadyWhenObserveIsUnavailable(t *testing.T) {
	rt := newHTTPRuntime(httpConfig{})

	sendReq := httptest.NewRequest(http.MethodPost, "/v1/messages/send", strings.NewReader(`{"text":"hello"}`))
	sendReq.Header.Set("Content-Type", "application/json")
	sendRR := httptest.NewRecorder()
	rt.ServeHTTP(sendRR, sendReq)
	if sendRR.Code != http.StatusServiceUnavailable {
		t.Fatalf("send status=%d, want %d", sendRR.Code, http.StatusServiceUnavailable)
	}

	receiveReq := httptest.NewRequest(http.MethodGet, "/v1/messages/receive", nil)
	receiveRR := httptest.NewRecorder()
	rt.ServeHTTP(receiveRR, receiveReq)
	if receiveRR.Code != http.StatusServiceUnavailable {
		t.Fatalf("receive status=%d, want %d", receiveRR.Code, http.StatusServiceUnavailable)
	}
}

func TestSendHTTPStatusMapping(t *testing.T) {
	cases := []struct {
		name     string
		result   map[string]any
		runErr   error
		wantCode int
	}{
		{name: "accepted", result: map[string]any{"accepted": true}, wantCode: http.StatusOK},
		{name: "busy", result: map[string]any{"accepted": false, "error_code": "send_busy"}, wantCode: http.StatusConflict},
		{name: "timeout", result: map[string]any{"accepted": false, "error_code": "send_timeout"}, wantCode: http.StatusGatewayTimeout},
		{name: "probe error", result: map[string]any{"accepted": false, "error_code": "input_not_found"}, runErr: context.Canceled, wantCode: http.StatusBadGateway},
		{name: "structured failure", result: map[string]any{"accepted": false, "error_code": "input_not_found"}, wantCode: http.StatusBadGateway},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := sendHTTPStatus(tc.result, tc.runErr); got != tc.wantCode {
				t.Fatalf("status=%d, want %d", got, tc.wantCode)
			}
		})
	}
}

func TestHTTPRuntimeAllowsOnlyOneSSESubscriber(t *testing.T) {
	rt := readyHTTPRuntime()
	first, err := rt.subscribe()
	if err != nil || first == nil {
		t.Fatal("first subscriber should be accepted")
	}
	second, err := rt.subscribe()
	if !errors.Is(err, errHTTPReceiveBusy) || second != nil {
		t.Fatal("second subscriber should be rejected")
	}
	rt.unsubscribe(first)
	third, err := rt.subscribe()
	if err != nil || third == nil {
		t.Fatal("subscriber should be accepted after first disconnects")
	}
	rt.unsubscribe(third)
}

func TestHandleReceiveWritesSSEMessage(t *testing.T) {
	rt := readyHTTPRuntime()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req := httptest.NewRequest(http.MethodGet, "/v1/messages/receive", nil).WithContext(ctx)
	rr := newSSERecorder()
	done := make(chan struct{})
	go func() {
		rt.ServeHTTP(rr, req)
		close(done)
	}()

	awaitSSEFlush(t, rr)
	if contentType := rr.contentType(); contentType != "text/event-stream" {
		t.Fatalf("content type=%q, want text/event-stream", contentType)
	}
	event := wechatmodel.Event{
		AccountID:   "default",
		ChatID:      "chat-1",
		ChatName:    "测试群",
		ChatType:    wechatmodel.ChatTypeGroup,
		SenderName:  "张三",
		MessageID:   "msg-1",
		Text:        "hello",
		MessageType: wechatmodel.MessageTypeText,
		CreatedAt:   time.Unix(1, 0).UTC(),
	}
	rt.publish(event)

	awaitSSEFlush(t, rr)
	body := rr.bodyString()
	if !strings.Contains(body, "event: message\n") || !strings.Contains(body, "data: ") || !strings.Contains(body, `"message_id":"msg-1"`) {
		t.Fatalf("unexpected SSE body: %q", body)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("SSE handler did not stop after request cancellation")
	}
}

func TestHandleReceiveWritesHeartbeat(t *testing.T) {
	rt := readyHTTPRuntime()
	oldInterval := httpHeartbeatInterval
	httpHeartbeatInterval = time.Millisecond
	defer func() { httpHeartbeatInterval = oldInterval }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req := httptest.NewRequest(http.MethodGet, "/v1/messages/receive", nil).WithContext(ctx)
	rr := newSSERecorder()
	done := make(chan struct{})
	go func() {
		rt.ServeHTTP(rr, req)
		close(done)
	}()

	awaitSSEFlush(t, rr)
	awaitSSEFlush(t, rr)
	if body := rr.bodyString(); !strings.Contains(body, ": heartbeat\n\n") {
		t.Fatalf("unexpected SSE heartbeat body: %q", body)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("SSE heartbeat handler did not stop after request cancellation")
	}
}

// sseRecorder serializes writes and gives tests a synchronization point after
// each Flush, avoiding concurrent reads from httptest.ResponseRecorder.
type sseRecorder struct {
	mu      sync.Mutex
	header  http.Header
	status  int
	body    bytes.Buffer
	flushed chan struct{}
}

func newSSERecorder() *sseRecorder {
	return &sseRecorder{header: make(http.Header), flushed: make(chan struct{}, 8)}
}

func (r *sseRecorder) Header() http.Header { return r.header }

func (r *sseRecorder) WriteHeader(status int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.status == 0 {
		r.status = status
	}
}

func (r *sseRecorder) Write(p []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.status == 0 {
		r.status = http.StatusOK
	}
	return r.body.Write(p)
}

func (r *sseRecorder) Flush() {
	select {
	case r.flushed <- struct{}{}:
	default:
	}
}

func (r *sseRecorder) contentType() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.header.Get("Content-Type")
}

func (r *sseRecorder) bodyString() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.body.String()
}

func awaitSSEFlush(t *testing.T, rr *sseRecorder) {
	t.Helper()
	select {
	case <-rr.flushed:
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for SSE flush")
	}
}

func TestHTTPErrorResponseIsJSON(t *testing.T) {
	rr := httptest.NewRecorder()
	writeHTTPError(rr, http.StatusBadRequest, "invalid_input", "bad request")
	if got := rr.Header().Get("Content-Type"); got != "application/json" {
		t.Fatalf("content type=%q, want application/json", got)
	}
	if !bytes.Contains(rr.Body.Bytes(), []byte(`"accepted":false`)) {
		t.Fatalf("response missing accepted=false: %q", rr.Body.String())
	}
}
