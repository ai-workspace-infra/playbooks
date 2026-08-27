package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/gateway"
)

var version = "0.1.0"

func main() {
	os.Exit(run())
}

func run() int {
	flags := flag.NewFlagSet("xconnect-gateway-agent", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	configPath := flags.String("config", "", "path to gateway JSON configuration")
	mode := flags.String("mode", "shadow", "runtime mode; v1 only supports shadow")
	showVersion := flags.Bool("version", false, "print version and exit")
	if err := flags.Parse(os.Args[1:]); err != nil {
		return 2
	}
	if *showVersion {
		fmt.Printf("xconnect-gateway-agent %s\n", version)
		return 0
	}
	if *mode != "shadow" || *configPath == "" {
		fmt.Fprintln(os.Stderr, "xconnect-gateway-agent requires --config and --mode shadow")
		return 2
	}
	cfg, err := gateway.LoadConfig(*configPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "xconnect-gateway-agent configuration rejected")
		return 1
	}
	publicKey, err := gateway.LoadPublicKey(cfg.ControlPlane.SnapshotSigningPublicKeyFile)
	if err != nil {
		fmt.Fprintln(os.Stderr, "xconnect-gateway-agent trust key rejected")
		return 1
	}
	store, err := gateway.NewStore(cfg.Snapshots)
	if err != nil {
		fmt.Fprintln(os.Stderr, "xconnect-gateway-agent state initialization failed")
		return 1
	}
	controller, err := gateway.NewHTTPController(cfg.ControlPlane.URL, cfg.ControlPlane.CredentialsFile, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, "xconnect-gateway-agent controller configuration rejected")
		return 1
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	agent := &gateway.Agent{
		Config: cfg, Controller: controller, Store: store,
		WireGuard: gateway.WireGuardReader{Runner: gateway.ExecRunner{}, Binary: "wg"},
		PublicKey: publicKey, Version: version, Logger: logger,
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := agent.Run(ctx); err != nil {
		logger.Error("gateway shadow agent stopped", "event_code", "agent_run_failed")
		return 1
	}
	logger.Info("gateway shadow agent stopped", "event_code", "graceful_shutdown")
	return 0
}
