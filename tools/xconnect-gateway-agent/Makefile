.PHONY: build build-static-import build-cutover-readiness test race vet check

VERSION ?= 0.1.0
BIN_DIR ?= bin
LDFLAGS := -s -w -X main.version=$(VERSION)

build:
	mkdir -p "$(BIN_DIR)"
	go build -trimpath -ldflags "$(LDFLAGS)" -o "$(BIN_DIR)/xconnect-gateway-agent" ./cmd/xconnect-gateway-agent

build-static-import:
	mkdir -p "$(BIN_DIR)"
	go build -trimpath -ldflags "$(LDFLAGS)" -o "$(BIN_DIR)/xconnect-static-import" ./cmd/xconnect-static-import

build-cutover-readiness:
	mkdir -p "$(BIN_DIR)"
	go build -trimpath -ldflags "$(LDFLAGS)" -o "$(BIN_DIR)/xconnect-cutover-readiness" ./cmd/xconnect-cutover-readiness

test:
	go test ./...

race:
	go test -race ./...

vet:
	go vet ./...

check: vet race
