.PHONY: build build-static-import test race vet check

VERSION ?= 0.1.0
BIN_DIR ?= bin
LDFLAGS := -s -w -X main.version=$(VERSION)

build:
	mkdir -p "$(BIN_DIR)"
	go build -trimpath -ldflags "$(LDFLAGS)" -o "$(BIN_DIR)/xconnect-gateway-agent" ./cmd/xconnect-gateway-agent

build-static-import:
	mkdir -p "$(BIN_DIR)"
	go build -trimpath -ldflags "$(LDFLAGS)" -o "$(BIN_DIR)/xconnect-static-import" ./cmd/xconnect-static-import

test:
	go test ./...

race:
	go test -race ./...

vet:
	go vet ./...

check: vet race
