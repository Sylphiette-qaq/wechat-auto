package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"wechat-auto/internal/probe"
	"wechat-auto/internal/wechatmodel"
)

// stringList 实现 flag.Value，用于收集可重复出现的字符串参数。
type stringList []string

// String 将参数列表拼接成逗号分隔的文本，供 flag 包展示当前值。
func (s *stringList) String() string { return strings.Join(*s, ",") }

// Set 接收一次 --probe-arg 参数，并追加到参数列表中。
func (s *stringList) Set(v string) error { *s = append(*s, v); return nil }

// main 解析命令行参数，并启动探针进程处理函数。
func main() {
	// mode 决定是原样转发探针输出，还是解析成统一事件后输出。
	mode := flag.String("mode", "observe", "运行模式：probe、observe、send 或 http")
	// probeCommand 默认使用 python3，也允许调用方替换为其他可执行文件。
	probeCommand := flag.String("probe", "python3", "AT-SPI 探针可执行文件")
	sendKey := flag.String("send-key", getenvOr("SEND_KEY", "enter"), "发送快捷键：仅支持 enter")
	sendTimeout := flag.String("send-timeout", getenvOr("SEND_TIMEOUT", "10s"), "发送超时时间，传递给探针")
	httpAddr := flag.String("http-addr", getenvOr("HTTP_ADDR", "0.0.0.0:8090"), "HTTP 服务监听地址")
	var probeArgs stringList
	// 同一个参数可以重复出现，以便完整传递 Python 探针的参数序列。
	flag.Var(&probeArgs, "probe-arg", "传递给探针的参数，可重复指定")
	flag.Parse()

	// 入口处立即拒绝未知模式，避免进入含糊的处理分支。
	if *mode != "probe" && *mode != "observe" && *mode != "send" && *mode != "http" {
		fatalf("无效 --mode %q，必须是 probe、observe、send 或 http", *mode)
	}
	// 未被 flag 消费的位置参数也作为探针参数，保持命令行调用的兼容性。
	if flag.NArg() > 0 {
		probeArgs = append(probeArgs, flag.Args()...)
	}
	var stdin io.Reader
	if *mode == "send" {
		payload, err := io.ReadAll(os.Stdin)
		if err != nil {
			fatalf("读取 stdin 失败: %v", err)
		}
		if len(payload) == 0 || len(bytes.TrimSpace(payload)) == 0 {
			_ = json.NewEncoder(os.Stdout).Encode(map[string]any{
				"kind":       "send_result",
				"accepted":   false,
				"verified":   false,
				"error_code": "invalid_input",
				"error":      "stdin text must contain a non-whitespace character",
			})
			os.Exit(1)
		}
		if *sendKey != "enter" {
			fatalf("无效 --send-key %q，当前仅支持 enter", *sendKey)
		}
		parsedTimeout, err := time.ParseDuration(*sendTimeout)
		if err != nil || parsedTimeout <= 0 {
			fatalf("无效 --send-timeout %q，必须是正时长（如 10s）", *sendTimeout)
		}
		probeArgs = append(probeArgs, "send", "--send-key", *sendKey, "--send-timeout", *sendTimeout)
		stdin = bytes.NewReader(payload)
	}
	if *mode == "http" {
		if *sendKey != "enter" {
			fatalf("无效 --send-key %q，当前仅支持 enter", *sendKey)
		}
		if parsedTimeout, err := time.ParseDuration(*sendTimeout); err != nil || parsedTimeout <= 0 {
			fatalf("无效 --send-timeout %q，必须是正时长（如 10s）", *sendTimeout)
		}
		httpCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		if err := runHTTP(httpCtx, *httpAddr, *probeCommand, probeArgs, *sendKey, *sendTimeout, os.Stderr); err != nil {
			fatalf("%v", err)
		}
		return
	}
	// 统一由 run 管理子进程生命周期和输出流；失败时以退出码 2 结束。
	if err := run(context.Background(), *mode, *probeCommand, probeArgs, stdin, os.Stdout, os.Stderr); err != nil {
		fatalf("%v", err)
	}
}

// run 启动 AT-SPI 探针，并根据模式转发或归一化其 JSONL 输出。
func run(ctx context.Context, mode, command string, args []string, stdin io.Reader, stdout, stderr io.Writer) error {
	// CommandContext 将父上下文的取消信号绑定到探针子进程。
	cmd := exec.CommandContext(ctx, command, args...)
	// 探针诊断日志走 stderr，避免污染 stdout 上的业务 JSONL。
	cmd.Stderr = stderr
	if stdin != nil {
		cmd.Stdin = stdin
	}
	// 只接管标准输出，后续由当前函数逐行读取或原样转发。
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("create probe stdout pipe: %w", err)
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start probe: %w", err)
	}
	if mode == "probe" {
		// probe 模式用于导出控件树，Go 不解释内容，直接复制给调用方。
		_, copyErr := io.Copy(stdout, pipe)
		waitErr := cmd.Wait()
		if copyErr != nil {
			return fmt.Errorf("forward probe output: %w", copyErr)
		}
		if waitErr != nil {
			return fmt.Errorf("probe exited: %w", waitErr)
		}
		return nil
	}
	if mode == "send" {
		payload, copyErr := io.ReadAll(pipe)
		waitErr := cmd.Wait()
		if copyErr != nil {
			return fmt.Errorf("forward send result: %w", copyErr)
		}
		if _, err := stdout.Write(payload); err != nil {
			return fmt.Errorf("write send result: %w", err)
		}
		var result struct {
			Accepted  bool   `json:"accepted"`
			ErrorCode string `json:"error_code"`
		}
		if err := json.Unmarshal(bytes.TrimSpace(payload), &result); err != nil {
			return fmt.Errorf("parse send result: %w", err)
		}
		if !result.Accepted && result.ErrorCode != "" {
			return fmt.Errorf("send failed: %s", result.ErrorCode)
		}
		if waitErr != nil {
			return fmt.Errorf("send probe exited: %w", waitErr)
		}
		return nil
	}

	// observe 模式在进程内保存已输出事件的键，抑制轮询造成的重复记录。
	dedup := wechatmodel.NewDeduper()
	scanner := bufio.NewScanner(pipe)
	// 控件树可能包含很长的文本节点，因此设置 4MB 的有界扫描上限。
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	encoder := json.NewEncoder(stdout)
	for scanner.Scan() {
		// 每一行必须符合探针 Record 契约；非法行立即终止，避免坏数据进入事件流。
		record, parseErr := probe.ParseRecord(scanner.Bytes())
		if parseErr != nil {
			return fmt.Errorf("invalid probe record: %w", parseErr)
		}
		// Event 只做已完整记录到统一事件模型的字段映射。
		event := record.Event()
		// 以 account_id + chat_id + message_id 作为进程内去重键。
		if !dedup.Add(wechatmodel.DedupKey(event)) {
			continue
		}
		if err := encoder.Encode(event); err != nil {
			return fmt.Errorf("encode event: %w", err)
		}
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("read probe output: %w", err)
	}
	// 读取完 stdout 后等待子进程退出，确保探针异常不会被静默忽略。
	if err := cmd.Wait(); err != nil {
		return fmt.Errorf("probe exited: %w", err)
	}
	return nil
}

// fatalf 输出命令行级错误并以约定的参数错误退出码结束程序。
func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(2)
}

func getenvOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
