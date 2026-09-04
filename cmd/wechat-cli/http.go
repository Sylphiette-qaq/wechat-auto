package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"os/exec"
	"strings"
	"sync"
	"time"

	"wechat-auto/internal/probe"
	"wechat-auto/internal/wechatmodel"
)

const maxHTTPBody = 1 << 20

var httpHeartbeatInterval = 15 * time.Second

var (
	errHTTPNotReady       = errors.New("observe probe is not ready")
	errHTTPReceiveBusy    = errors.New("an SSE receive connection is already active")
	errHTTPStreamNotReady = errors.New("SSE streaming is not supported")
)

type httpConfig struct {
	ProbeCommand string
	ProbeArgs    []string
	SendKey      string
	SendTimeout  string
	Stderr       io.Writer
}

// httpRuntime owns the long-running watch process and the single local SSE
// subscription. It intentionally has no persistence or queue semantics.
type httpRuntime struct {
	cfg httpConfig

	mu         sync.RWMutex
	ready      bool
	subscriber chan wechatmodel.Event
}

func newHTTPRuntime(cfg httpConfig) *httpRuntime {
	if cfg.Stderr == nil {
		cfg.Stderr = io.Discard
	}
	cfg.ProbeArgs = append([]string(nil), cfg.ProbeArgs...)
	return &httpRuntime{cfg: cfg}
}

// runHTTP starts the watch process in the background and serves the two HTTP
// endpoints until the HTTP server exits.
func runHTTP(ctx context.Context, addr, probeCommand string, probeArgs []string, sendKey, sendTimeout string, stderr io.Writer) error {
	rt := newHTTPRuntime(httpConfig{
		ProbeCommand: probeCommand,
		ProbeArgs:    append([]string(nil), probeArgs...),
		SendKey:      sendKey,
		SendTimeout:  sendTimeout,
		Stderr:       stderr,
	})
	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	go rt.observe(runCtx)
	server := &http.Server{Addr: addr, Handler: rt}
	go func() {
		<-runCtx.Done()
		// SSE handlers are intentionally long-lived; close the listener and
		// active streams together when the container receives a stop signal.
		_ = server.Close()
	}()
	err := server.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func (rt *httpRuntime) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/v1/messages/send":
		rt.handleSend(w, r)
	case "/v1/messages/receive":
		rt.handleReceive(w, r)
	default:
		http.NotFound(w, r)
	}
}

func (rt *httpRuntime) handleSend(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeHTTPError(w, http.StatusBadRequest, "invalid_input", "method must be POST")
		return
	}
	if !rt.isReady() {
		writeHTTPError(w, http.StatusServiceUnavailable, "not_ready", errHTTPNotReady.Error())
		return
	}
	text, err := decodeSendText(w, r)
	if err != nil {
		writeHTTPError(w, http.StatusBadRequest, "invalid_input", err.Error())
		return
	}

	result, runErr := rt.send(r.Context(), text)
	if result == nil {
		writeHTTPError(w, http.StatusBadGateway, "send_failed", runErrString(runErr))
		return
	}
	status := sendHTTPStatus(result, runErr)
	writeJSON(w, status, result)
}

func decodeSendText(w http.ResponseWriter, r *http.Request) (string, error) {
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		return "", errors.New("Content-Type must be application/json")
	}

	body := http.MaxBytesReader(w, r.Body, maxHTTPBody)
	defer body.Close()
	decoder := json.NewDecoder(body)
	decoder.DisallowUnknownFields()
	var request struct {
		Text string `json:"text"`
	}
	if err := decoder.Decode(&request); err != nil {
		return "", errors.New("request body must be a JSON object with text")
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return "", errors.New("request body must contain exactly one JSON value")
	}
	if strings.TrimSpace(request.Text) == "" {
		return "", errors.New("text must contain a non-whitespace character")
	}
	return request.Text, nil
}

func (rt *httpRuntime) handleReceive(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeHTTPError(w, http.StatusBadRequest, "invalid_input", "method must be GET")
		return
	}
	ch, err := rt.subscribe()
	if err != nil {
		if errors.Is(err, errHTTPReceiveBusy) {
			writeHTTPError(w, http.StatusConflict, "receive_busy", err.Error())
			return
		}
		writeHTTPError(w, http.StatusServiceUnavailable, "not_ready", err.Error())
		return
	}
	defer rt.unsubscribe(ch)

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeHTTPError(w, http.StatusInternalServerError, "sse_unavailable", errHTTPStreamNotReady.Error())
		return
	}
	flusher.Flush()
	ticker := time.NewTicker(httpHeartbeatInterval)
	defer ticker.Stop()
	for {
		select {
		case event, open := <-ch:
			if !open {
				return
			}
			if err := writeSSEEvent(w, event); err != nil {
				return
			}
			flusher.Flush()
		case <-ticker.C:
			if _, err := io.WriteString(w, ": heartbeat\n\n"); err != nil {
				return
			}
			flusher.Flush()
		case <-r.Context().Done():
			return
		}
	}
}

func writeSSEEvent(w io.Writer, event wechatmodel.Event) error {
	payload, err := json.Marshal(event)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(w, "event: message\ndata: %s\n\n", payload)
	return err
}

func (rt *httpRuntime) isReady() bool {
	rt.mu.RLock()
	defer rt.mu.RUnlock()
	return rt.ready
}

func (rt *httpRuntime) subscribe() (chan wechatmodel.Event, error) {
	rt.mu.Lock()
	defer rt.mu.Unlock()
	if !rt.ready {
		return nil, errHTTPNotReady
	}
	if rt.subscriber != nil {
		return nil, errHTTPReceiveBusy
	}
	rt.subscriber = make(chan wechatmodel.Event, 8)
	return rt.subscriber, nil
}

func (rt *httpRuntime) unsubscribe(ch chan wechatmodel.Event) {
	rt.mu.Lock()
	defer rt.mu.Unlock()
	if rt.subscriber == ch {
		rt.subscriber = nil
	}
}

func (rt *httpRuntime) publish(event wechatmodel.Event) {
	rt.mu.Lock()
	defer rt.mu.Unlock()
	ch := rt.subscriber
	if ch == nil {
		return
	}
	select {
	case ch <- event:
	default:
		// A stalled local client must not stop the AT-SPI watch loop.
	}
}

func (rt *httpRuntime) setReady(value bool) {
	rt.mu.Lock()
	defer rt.mu.Unlock()
	rt.ready = value
	if !value && rt.subscriber != nil {
		close(rt.subscriber)
		rt.subscriber = nil
	}
}

func (rt *httpRuntime) observe(ctx context.Context) {
	args := operationArgs(rt.cfg.ProbeArgs, "watch")
	cmd := exec.CommandContext(ctx, rt.cfg.ProbeCommand, args...)
	cmd.Stderr = rt.cfg.Stderr
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		return
	}
	if err := cmd.Start(); err != nil {
		return
	}
	rt.setReady(true)
	defer rt.setReady(false)
	dedup := wechatmodel.NewDeduper()
	scanner := bufio.NewScanner(pipe)
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		record, err := probe.ParseRecord(scanner.Bytes())
		if err != nil {
			continue
		}
		event := record.Event()
		if dedup.Add(wechatmodel.DedupKey(event)) {
			rt.publish(event)
		}
	}
	_ = cmd.Wait()
}

func (rt *httpRuntime) send(ctx context.Context, text string) (map[string]any, error) {
	args := operationArgs(rt.cfg.ProbeArgs, "send")
	args = append(args, "--send-key", rt.cfg.SendKey, "--send-timeout", rt.cfg.SendTimeout)
	var stdout bytes.Buffer
	err := run(ctx, "send", rt.cfg.ProbeCommand, args, strings.NewReader(text), &stdout, rt.cfg.Stderr)
	payload := bytes.TrimSpace(stdout.Bytes())
	if len(payload) == 0 {
		return nil, err
	}
	var result map[string]any
	if decodeErr := json.Unmarshal(payload, &result); decodeErr != nil {
		return nil, fmt.Errorf("parse send result: %w", decodeErr)
	}
	return result, err
}

func operationArgs(args []string, operation string) []string {
	result := append([]string(nil), args...)
	for i, arg := range result {
		if arg == "dump" || arg == "watch" || arg == "send" {
			result[i] = operation
			return result
		}
	}
	return append(result, operation)
}

func sendHTTPStatus(result map[string]any, runErr error) int {
	if accepted, _ := result["accepted"].(bool); accepted {
		return http.StatusOK
	}
	code, _ := result["error_code"].(string)
	switch code {
	case "send_busy":
		return http.StatusConflict
	case "send_timeout":
		return http.StatusGatewayTimeout
	}
	if runErr != nil {
		return http.StatusBadGateway
	}
	return http.StatusBadGateway
}

func runErrString(err error) string {
	if err == nil {
		return "send probe failed"
	}
	return err.Error()
}

func writeHTTPError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{
		"accepted":   false,
		"verified":   false,
		"error_code": code,
		"error":      message,
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
