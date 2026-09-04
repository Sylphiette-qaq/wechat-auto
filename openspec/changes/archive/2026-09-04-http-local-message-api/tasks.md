## 1. OpenSpec and HTTP contract

- [x] 1.1 Validate proposal, design, and local HTTP API spec with strict OpenSpec checks
- [x] 1.2 Add an implementation task checklist covering HTTP mode, send, SSE receive, readiness, and Docker wiring

## 2. Go HTTP runtime

- [x] 2.1 Add `--mode http` and `--http-addr` argument parsing while preserving `probe`, `observe`, and `send`
- [x] 2.2 Implement the long-running observe subprocess, readiness state, deduplication, and single SSE subscriber
- [x] 2.3 Implement `POST /v1/messages/send` JSON validation and reuse the existing send probe/result contract
- [x] 2.4 Implement `GET /v1/messages/receive` SSE framing, flushing, and heartbeat behavior
- [x] 2.5 Map validation, readiness, busy, timeout, and probe failures to the agreed HTTP status codes

## 3. Tests and runtime configuration

- [x] 3.1 Add unit tests for request validation, send result status mapping, readiness, and SSE output
- [x] 3.2 Update Docker entrypoint and Compose to default to HTTP mode and publish only `127.0.0.1:8090`
- [x] 3.3 Update README, AGENTS, and helper script documentation with local API examples

## 4. Verification and startup

- [x] 4.1 Run gofmt, go test, go build, go vet, shell syntax checks, and strict OpenSpec validation
- [x] 4.2 Build and start Docker Compose, verify port binding and the two HTTP endpoints where the Docker daemon permits
