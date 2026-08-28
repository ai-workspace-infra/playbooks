package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
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
	mode := flags.String("mode", "shadow", "runtime mode: shadow or explicitly enabled apply")
	showVersion := flags.Bool("version", false, "print version and exit")
	clearRuntimeFault := flags.String("clear-runtime-fault", "", "acknowledge an exactly matching manually recovered snapshot fault")
	if err := flags.Parse(os.Args[1:]); err != nil {
		return 2
	}
	if *showVersion {
		fmt.Printf("xconnect-gateway-agent %s\n", version)
		return 0
	}
	if (*mode != "shadow" && *mode != "apply") || *configPath == "" {
		fmt.Fprintln(os.Stderr, "xconnect-gateway-agent requires --config and --mode shadow|apply")
		return 2
	}
	cfg, err := gateway.LoadConfig(*configPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "xconnect-gateway-agent configuration rejected")
		return 1
	}
	if cfg.Mode != *mode || cfg.RuntimeApplyEnabled() != (*mode == "apply") {
		fmt.Fprintln(os.Stderr, "xconnect-gateway-agent mode does not match configuration feature flag")
		return 1
	}
	if *clearRuntimeFault != "" {
		if !cfg.RuntimeApplyEnabled() {
			fmt.Fprintln(os.Stderr, "runtime fault acknowledgement requires apply mode")
			return 1
		}
		if _, err := os.Stat(filepath.Join(cfg.Apply.RuntimeLastKnownGood, "runtime-transaction.json")); !os.IsNotExist(err) {
			fmt.Fprintln(os.Stderr, "runtime fault acknowledgement requires the transaction journal to be manually resolved and removed")
			return 1
		}
		runner := gateway.DefaultExecRunner(cfg.Runtime.IPBinary)
		if err := gateway.VerifyInterfaceState(context.Background(), runner, cfg.Runtime.IPBinary, cfg.Runtime.WireGuardInterface, "UP"); err != nil {
			fmt.Fprintln(os.Stderr, "runtime fault acknowledgement requires verified WireGuard interface UP state")
			return 1
		}
		store, err := gateway.NewStore(cfg.Snapshots)
		if err != nil || store.ClearRuntimeFault(*clearRuntimeFault) != nil {
			fmt.Fprintln(os.Stderr, "runtime fault acknowledgement rejected")
			return 1
		}
		fmt.Println("xconnect-gateway-agent runtime fault acknowledgement recorded")
		return 0
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
	wgBinary := cfg.Runtime.WireGuardBinary
	if wgBinary == "" {
		wgBinary = "/usr/bin/wg"
	}
	runner := gateway.DefaultExecRunner(wgBinary, cfg.Runtime.NFTablesBinary, cfg.Runtime.IPBinary, cfg.Runtime.XrayBinary)
	wireGuard := gateway.WireGuardReader{Runner: runner, Binary: wgBinary}
	agent := &gateway.Agent{
		Config: cfg, Controller: controller, Store: store,
		WireGuard: wireGuard,
		PublicKey: publicKey, Version: version, Logger: logger,
	}
	if cfg.RuntimeApplyEnabled() {
		agent.Policy = controller
		agent.Runtime = &gateway.RuntimeTransaction{Config: cfg, Runner: runner, Reader: wireGuard}
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := agent.Run(ctx); err != nil {
		logger.Error("gateway agent stopped", "event_code", "agent_run_failed", "mode", cfg.Mode)
		return 1
	}
	logger.Info("gateway agent stopped", "event_code", "graceful_shutdown", "mode", cfg.Mode)
	return 0
}
