package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"

	"wechat-auto/internal/probe"
	"wechat-auto/internal/wechatmodel"
)

type stringList []string

func (s *stringList) String() string     { return strings.Join(*s, ",") }
func (s *stringList) Set(v string) error { *s = append(*s, v); return nil }

func main() {
	mode := flag.String("mode", "observe", "运行模式：probe 或 observe")
	probeCommand := flag.String("probe", "python3", "AT-SPI 探针可执行文件")
	var probeArgs stringList
	flag.Var(&probeArgs, "probe-arg", "传递给探针的参数，可重复指定")
	flag.Parse()

	if *mode != "probe" && *mode != "observe" {
		fatalf("无效 --mode %q，必须是 probe 或 observe", *mode)
	}
	if flag.NArg() > 0 {
		probeArgs = append(probeArgs, flag.Args()...)
	}
	if err := run(context.Background(), *mode, *probeCommand, probeArgs, os.Stdout, os.Stderr); err != nil {
		fatalf("%v", err)
	}
}

func run(ctx context.Context, mode, command string, args []string, stdout, stderr io.Writer) error {
	cmd := exec.CommandContext(ctx, command, args...)
	cmd.Stderr = stderr
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("create probe stdout pipe: %w", err)
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start probe: %w", err)
	}
	if mode == "probe" {
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

	dedup := wechatmodel.NewDeduper()
	scanner := bufio.NewScanner(pipe)
	// Accessible trees can contain long text nodes; retain a bounded but useful limit.
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	encoder := json.NewEncoder(stdout)
	for scanner.Scan() {
		record, parseErr := probe.ParseRecord(scanner.Bytes())
		if parseErr != nil {
			fmt.Fprintf(stderr, "probe record ignored: %v\n", parseErr)
			continue
		}
		event := record.Event()
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
	if err := cmd.Wait(); err != nil {
		return fmt.Errorf("probe exited: %w", err)
	}
	return nil
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(2)
}
