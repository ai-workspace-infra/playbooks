.PHONY: build test race vet check

VERSION ?= 0.1.0
BIN_DIR ?= bin
LDFLAGS := -s -w -X main.version=$(VERSION)

build:
	mkdir -p "$(BIN_DIR)"
	go build -trimpath -ldflags "$(LDFLAGS)" -o "$(BIN_DIR)/xconnect-gateway-agent" ./cmd/xconnect-gateway-agent

test:
	go test ./...

race:
	go test -race ./...

vet:
	go vet ./...

check: vet race
