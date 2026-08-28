package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/cutover"
)

var version = "0.1.0"

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(arguments []string, stdout, stderr io.Writer) int {
	if len(arguments) == 1 && arguments[0] == "--version" {
		fmt.Fprintf(stdout, "xconnect-cutover-readiness %s\n", version)
		return cutover.ReadyExitCode
	}
	flags := flag.NewFlagSet("xconnect-cutover-readiness", flag.ContinueOnError)
	flags.SetOutput(stderr)
	bundlePath := flags.String("bundle", "", "protected accounts-only readiness bundle")
	publicKeyPath := flags.String("signing-public-key", "", "protected Ed25519 public key")
	authorizationPublicKeyPath := flags.String("authorization-public-key", "", "protected Accounts cutover authorization Ed25519 public key")
	authorizationKeyID := flags.String("authorization-key-id", "", "pinned Accounts cutover authorization key ID")
	outputPath := flags.String("evidence", "-", "readiness evidence path or -")
	minimumHealthSamples := flags.Int("minimum-health-samples", 3, "required consecutive healthy samples")
	accountsOnly := flags.Bool("accounts-only", false, "explicitly request accounts-only readiness")
	if err := flags.Parse(arguments); err != nil || flags.NArg() != 0 || *bundlePath == "" || *publicKeyPath == "" || *authorizationPublicKeyPath == "" || *authorizationKeyID == "" || !*accountsOnly {
		fmt.Fprintln(stderr, "readiness requires --accounts-only, bundle, snapshot key, and Controller authorization key")
		return cutover.InvalidInputExitCode
	}
	bundleRaw, err := readProtected(*bundlePath, 8<<20)
	if err != nil {
		fmt.Fprintln(stderr, "readiness bundle rejected")
		return cutover.InvalidInputExitCode
	}
	keyRaw, err := readProtected(*publicKeyPath, 4096)
	if err != nil {
		fmt.Fprintln(stderr, "snapshot signing public key rejected")
		return cutover.InvalidInputExitCode
	}
	decodedKey, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(keyRaw)))
	if err != nil || len(decodedKey) != ed25519.PublicKeySize {
		fmt.Fprintln(stderr, "snapshot signing public key rejected")
		return cutover.InvalidInputExitCode
	}
	authorizationKeyRaw, err := readProtected(*authorizationPublicKeyPath, 4096)
	if err != nil {
		fmt.Fprintln(stderr, "Controller authorization public key rejected")
		return cutover.InvalidInputExitCode
	}
	decodedAuthorizationKey, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(authorizationKeyRaw)))
	if err != nil || len(decodedAuthorizationKey) != ed25519.PublicKeySize {
		fmt.Fprintln(stderr, "Controller authorization public key rejected")
		return cutover.InvalidInputExitCode
	}
	evidence, readinessErr := cutover.Evaluate(bundleRaw, ed25519.PublicKey(decodedKey), ed25519.PublicKey(decodedAuthorizationKey), *authorizationKeyID, time.Now().UTC(), *minimumHealthSamples)
	rawEvidence, err := cutover.MarshalEvidence(evidence)
	if err != nil || writeEvidence(*outputPath, rawEvidence, stdout) != nil {
		fmt.Fprintln(stderr, "write readiness evidence failed")
		return 1
	}
	if readinessErr != nil {
		fmt.Fprintln(stderr, "accounts-only cutover rejected")
		return cutover.RejectedExitCode
	}
	fmt.Fprintln(stderr, "accounts-only cutover ready")
	return cutover.ReadyExitCode
}

func readProtected(path string, maximum int64) ([]byte, error) {
	before, err := os.Lstat(path)
	if err != nil || before.Mode()&os.ModeSymlink != 0 || !before.Mode().IsRegular() || before.Mode().Perm()&^os.FileMode(0o640) != 0 {
		return nil, fmt.Errorf("protected input is invalid")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	after, err := file.Stat()
	if err != nil || !os.SameFile(before, after) {
		return nil, fmt.Errorf("protected input changed while opening")
	}
	raw, err := io.ReadAll(io.LimitReader(file, maximum+1))
	if err != nil || int64(len(raw)) > maximum {
		return nil, fmt.Errorf("protected input is too large")
	}
	return raw, nil
}

func writeEvidence(path string, raw []byte, stdout io.Writer) error {
	if path == "-" {
		_, err := stdout.Write(raw)
		return err
	}
	if !filepath.IsAbs(path) {
		return fmt.Errorf("evidence path must be absolute")
	}
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(directory, ".xconnect-cutover-*")
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
