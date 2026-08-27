package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/gateway"
	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/staticmigration"
)

var version = "0.1.0"

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(arguments []string, stdout, stderr io.Writer) int {
	if len(arguments) == 1 && arguments[0] == "--version" {
		fmt.Fprintf(stdout, "xconnect-static-import %s\n", version)
		return 0
	}
	if len(arguments) == 0 {
		fmt.Fprintln(stderr, "usage: xconnect-static-import <import|diff> [options]")
		return staticmigration.InputErrorExitCode
	}
	switch arguments[0] {
	case "import":
		return runImport(arguments[1:], stdout, stderr)
	case "diff":
		return runDiff(arguments[1:], stdout, stderr)
	default:
		fmt.Fprintln(stderr, "operation must be import or diff")
		return staticmigration.InputErrorExitCode
	}
}

func runImport(arguments []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("import", flag.ContinueOnError)
	flags.SetOutput(stderr)
	input := flags.String("input", "", "group_vars YAML path")
	networkID := flags.String("network-id", "", "accounts network identity")
	ownerUserID := flags.String("owner-user-id", "", "accounts owner user UUID")
	output := flags.String("output", "-", "deterministic import document path or -")
	apply := flags.Bool("apply", false, "explicitly submit the import document")
	controllerURL := flags.String("controller-url", "", "HTTPS accounts Controller URL")
	serviceTokenFile := flags.String("service-token-file", "", "protected accounts service token file")
	if err := flags.Parse(arguments); err != nil || flags.NArg() != 0 || *input == "" || *networkID == "" || *ownerUserID == "" {
		fmt.Fprintln(stderr, "import requires --input, --network-id, and --owner-user-id")
		return staticmigration.InputErrorExitCode
	}
	clients, err := staticmigration.ParseGroupVarsFile(*input)
	if err != nil {
		fmt.Fprintln(stderr, "static client input rejected")
		return staticmigration.InputErrorExitCode
	}
	document, err := staticmigration.BuildImportDocument(*networkID, *ownerUserID, clients)
	if err != nil {
		fmt.Fprintln(stderr, "static import document rejected")
		return staticmigration.InputErrorExitCode
	}
	raw, err := staticmigration.MarshalDocument(document)
	if err != nil || writeOutput(*output, raw, stdout) != nil {
		fmt.Fprintln(stderr, "write static import document failed")
		return 1
	}
	if !*apply {
		fmt.Fprintln(stderr, "dry-run: no Controller request sent")
		return 0
	}
	if *controllerURL == "" || *serviceTokenFile == "" {
		fmt.Fprintln(stderr, "--apply requires --controller-url and --service-token-file")
		return staticmigration.InputErrorExitCode
	}
	client, err := staticmigration.NewImportClient(*controllerURL, *serviceTokenFile, nil)
	if err != nil {
		fmt.Fprintln(stderr, "Controller import configuration rejected")
		return staticmigration.InputErrorExitCode
	}
	if _, err := client.Apply(context.Background(), raw); err != nil {
		fmt.Fprintln(stderr, "Controller import failed")
		return 1
	}
	fmt.Fprintln(stderr, "Controller import accepted")
	return 0
}

func runDiff(arguments []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("diff", flag.ContinueOnError)
	flags.SetOutput(stderr)
	input := flags.String("input", "", "group_vars YAML path")
	snapshotPath := flags.String("snapshot", "", "GatewaySnapshot JSON path")
	attachment := flags.String("attachment", "", "inventory gateway identity used by attach_to")
	evidencePath := flags.String("evidence", "-", "JSON evidence path or -")
	if err := flags.Parse(arguments); err != nil || flags.NArg() != 0 || *input == "" || *snapshotPath == "" || *attachment == "" {
		fmt.Fprintln(stderr, "diff requires --input, --snapshot, and --attachment")
		return staticmigration.InputErrorExitCode
	}
	clients, err := staticmigration.ParseGroupVarsFile(*input)
	if err != nil {
		fmt.Fprintln(stderr, "static client input rejected")
		return staticmigration.InputErrorExitCode
	}
	rawSnapshot, err := readLimited(*snapshotPath, staticmigration.DefaultMaxInputSize)
	if err != nil {
		fmt.Fprintln(stderr, "GatewaySnapshot input rejected")
		return staticmigration.InputErrorExitCode
	}
	snapshot, err := gateway.DecodeGatewaySnapshot(rawSnapshot)
	if err != nil {
		fmt.Fprintln(stderr, "GatewaySnapshot input rejected")
		return staticmigration.InputErrorExitCode
	}
	evidence, err := staticmigration.CompareSnapshot(clients, *attachment, snapshot)
	if err != nil {
		fmt.Fprintln(stderr, "static snapshot comparison rejected")
		return staticmigration.InputErrorExitCode
	}
	rawEvidence, err := json.MarshalIndent(evidence, "", "  ")
	if err != nil || writeOutput(*evidencePath, append(rawEvidence, '\n'), stdout) != nil {
		fmt.Fprintln(stderr, "write diff evidence failed")
		return 1
	}
	if evidence.Status == "drift" {
		return staticmigration.DiffExitCode
	}
	return 0
}

func readLimited(path string, limit int64) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil || int64(len(raw)) > limit {
		return nil, errors.New("input is unreadable or oversized")
	}
	return raw, nil
}

func writeOutput(path string, raw []byte, stdout io.Writer) error {
	if path == "-" {
		_, err := stdout.Write(raw)
		return err
	}
	if !filepath.IsAbs(path) {
		return errors.New("output path must be absolute or -")
	}
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(directory, ".xconnect-static-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(raw); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}
