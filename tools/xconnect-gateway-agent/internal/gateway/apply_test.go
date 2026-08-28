package gateway

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type commandRunnerFunc func(context.Context, string, ...string) ([]byte, error)

func (f commandRunnerFunc) Run(ctx context.Context, name string, args ...string) ([]byte, error) {
	return f(ctx, name, args...)
}

type fakePolicyProvider struct {
	raw   []byte
	calls int
}

func (p *fakePolicyProvider) PolicyArtifact(context.Context, string, uint64, string) ([]byte, error) {
	p.calls++
	return p.raw, nil
}

type fakeRuntimeApplier struct {
	applyErrors                                       []error
	applyCalls, recoverCalls, commitCalls, abortCalls int
}

func (r *fakeRuntimeApplier) Recover(context.Context, Checkpoint) error { r.recoverCalls++; return nil }
func (r *fakeRuntimeApplier) Apply(context.Context, GatewaySnapshot, ACLArtifact) (DiffSummary, error) {
	r.applyCalls++
	var err error
	if len(r.applyErrors) > 0 {
		err, r.applyErrors = r.applyErrors[0], r.applyErrors[1:]
	}
	return DiffSummary{Status: "available", Equal: err == nil, ProjectedPeers: 1, CurrentPeers: 1}, err
}
func (r *fakeRuntimeApplier) Commit(context.Context, uint64, string) error {
	r.commitCalls++
	return nil
}
func (r *fakeRuntimeApplier) Abort(context.Context, uint64, string) error { r.abortCalls++; return nil }

func TestAccountsPolicyGoldenAndScopedNFT(t *testing.T) {
	rawWithNewline, err := os.ReadFile("testdata/network-policy-enforcement.golden.json")
	if err != nil {
		t.Fatal(err)
	}
	raw := bytes.TrimSuffix(rawWithNewline, []byte("\n"))
	digest := sha256.Sum256(raw)
	if got := hex.EncodeToString(digest[:]); got != "58941760a9ab4568d2e72a6f34a2cede891d8e678346da8e886d86263e5b780c" {
		t.Fatalf("Accounts golden drift: %s", got)
	}
	keyA := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{2}, 32))
	keyB := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{3}, 32))
	snapshot := GatewaySnapshot{Policy: GatewayPolicy{Generation: 9, Backend: "nftables", RulesetSHA256: hex.EncodeToString(digest[:])}, WireGuard: GatewayWireGuard{InterfaceName: "wg-xco", Peers: []GatewayPeer{
		{DeviceID: "dev-a", PublicKey: keyA, AllowedIPs: []string{"10.77.0.10/32"}},
		{DeviceID: "dev-b", PublicKey: keyB, AllowedIPs: []string{"10.77.0.11/32"}},
	}}}
	artifact, err := ValidatePolicyArtifact(raw, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	rules, err := RenderNFTables(snapshot, artifact)
	if err != nil {
		t.Fatal(err)
	}
	text := string(rules)
	for _, forbidden := range []string{"flush ruleset", "tcp dport 443 accept", " deny ", "table ip ", "table inet filter"} {
		if strings.Contains(text, forbidden) {
			t.Fatalf("unsafe nft output contains %q: %s", forbidden, text)
		}
	}
	for _, required := range []string{"table inet xconnect_one", "hook forward", "policy accept", `iifname "wg-xco" jump overlay`, `oifname "wg-xco" jump overlay`, "tcp dport { 8787 } accept", "tcp sport { 8787 } ct state established,related accept", "    drop"} {
		if !strings.Contains(text, required) {
			t.Fatalf("nft output missing %q: %s", required, text)
		}
	}
	if strings.Contains(text, "chain forward { type filter hook forward priority filter; policy drop") {
		t.Fatal("XConnect table would capture unrelated host forwarding")
	}
	artifact.Rules[0].DestinationDevices = []string{"dev-unknown"}
	if _, err := RenderNFTables(snapshot, artifact); err == nil {
		t.Fatal("unknown device was not rejected fail-closed")
	}
}

func TestBootstrapRevisionZeroAndICMPDeny(t *testing.T) {
	empty := ACLArtifact{SchemaVersion: 1, CompilerVersion: policyCompilerV1, NetworkID: "network-golden", DefaultAction: "deny", ProtectedFlows: append([]string(nil), requiredProtectedFlows...), Rules: []ACLRule{}}
	raw, _ := jsonMarshal(empty)
	digest := sha256.Sum256(raw)
	snapshot := GatewaySnapshot{Policy: GatewayPolicy{RulesetSHA256: hex.EncodeToString(digest[:])}, WireGuard: GatewayWireGuard{InterfaceName: "wg-xco"}}
	if _, err := ValidatePolicyArtifact(raw, snapshot); err != nil {
		t.Fatalf("valid revision-zero default deny rejected: %v", err)
	}
	emptyRendered, err := RenderNFTables(GatewaySnapshot{WireGuard: GatewayWireGuard{InterfaceName: "wg-xco"}}, empty)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(emptyRendered), "ct state established") {
		t.Fatal("revoked/empty generation retained an established-flow bypass")
	}
	peerKey := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{4}, 32))
	deny := empty
	deny.Revision = 1
	deny.Rules = []ACLRule{{ID: "deny-ping", Action: "deny", SourceDevices: []string{"dev-a"}, DestinationDevices: []string{"dev-b"}, Protocols: []string{"icmp"}, Ports: []int{}}}
	snapshot.WireGuard.Peers = []GatewayPeer{{DeviceID: "dev-a", PublicKey: peerKey, AllowedIPs: []string{"10.0.0.1/32"}}, {DeviceID: "dev-b", PublicKey: peerKey, AllowedIPs: []string{"10.0.0.2/32"}}}
	rendered, err := RenderNFTables(snapshot, deny)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(rendered), "ip protocol icmp drop") {
		t.Fatalf("ICMP deny was not rendered to legal nft drop: %s", rendered)
	}
	deny.Rules[0].Action = "accept"
	accepted, err := RenderNFTables(snapshot, deny)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(accepted), "ip protocol icmp accept") || !strings.Contains(string(accepted), "ip protocol icmp ct state established,related accept") {
		t.Fatalf("ICMP request/reply allowance incomplete: %s", accepted)
	}
}

func jsonMarshal(value any) ([]byte, error) { return json.Marshal(value) }

func TestCredentialResolverAndXrayCandidateProtection(t *testing.T) {
	root := t.TempDir()
	cert := filepath.Join(root, "tls.crt")
	key := filepath.Join(root, "tls.key")
	credentialDir := filepath.Join(root, "credentials")
	if err := os.Mkdir(credentialDir, 0o700); err != nil {
		t.Fatal(err)
	}
	for path, raw := range map[string]string{cert: "certificate", key: "private-key"} {
		if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	credential := `{"id":"7b7f7a4f-938f-4aeb-86eb-53ee67c1a001","certificate_file":"` + cert + `","private_key_file":"` + key + `"}`
	if err := os.WriteFile(filepath.Join(credentialDir, "vault_test_01.json"), []byte(credential), 0o600); err != nil {
		t.Fatal(err)
	}
	snapshot := GatewaySnapshot{ProxyCore: "xray", Relay: GatewayRelay{Transport: "vless-tls-xudp", ListenHost: "0.0.0.0", ListenPort: 443, ServerNames: []string{"gateway.example"}, CredentialRefs: []string{"vault_test_01"}}}
	raw, err := RenderXrayRelayConfig(snapshot, "xconnect-one-relay", CredentialResolver{Directory: credentialDir})
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(raw, []byte("7b7f7a4f-938f-4aeb-86eb-53ee67c1a001")) {
		t.Fatal("runtime config did not resolve credential")
	}
	plan, _ := RenderXrayRelayPlan(snapshot)
	if bytes.Contains(plan, []byte("7b7f7a4f")) {
		t.Fatal("ordinary evidence leaked resolved credential")
	}
	if _, err := RenderXrayRelayConfig(snapshot, "xconnect-one-relay", CredentialResolver{Directory: credentialDir, ExpectedCertificate: cert, ExpectedPrivateKey: filepath.Join(root, "wrong.key")}); err == nil {
		t.Fatal("relay credential outside the node TLS binding was accepted")
	}
	if _, err := (CredentialResolver{Directory: credentialDir}).Resolve("../escape"); err == nil {
		t.Fatal("credential traversal accepted")
	}
	if err := os.Chmod(filepath.Join(credentialDir, "vault_test_01.json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := RenderXrayRelayConfig(snapshot, "xconnect-one-relay", CredentialResolver{Directory: credentialDir}); err == nil {
		t.Fatal("wide credential permissions accepted")
	}
}

func TestRuntimeTransactionOrderCommitAndNoHostNetwork(t *testing.T) {
	root := t.TempDir()
	cfg := testConfig(root)
	cfg.Mode = "apply"
	cfg.Apply = ApplyConfig{Enabled: true, RelayEnabled: true, LockFile: filepath.Join(root, "run", "apply.lock"), TransactionDir: filepath.Join(root, "run", "transactions"), RuntimeLastKnownGood: filepath.Join(root, "runtime-lkg"), RuntimeSecretLKG: filepath.Join(root, "secret-lkg"), ReadbackRetries: 1}
	cfg.Runtime.WireGuardBinary, cfg.Runtime.NFTablesBinary, cfg.Runtime.IPBinary = "/usr/bin/wg", "/usr/sbin/nft", "/usr/sbin/ip"
	cfg.Runtime.WireGuardListenPort, cfg.Runtime.WireGuardAddresses = 51820, []string{"10.77.0.1/32"}
	cfg.Runtime.RelayListenHost, cfg.Runtime.RelayListenPort = "0.0.0.0", 443
	cfg.Runtime.RelayCredentialDir = filepath.Join(root, "credentials")
	cfg.Runtime.XrayAPIEndpoint, cfg.Runtime.XrayInboundTag = "127.0.0.1:10085", "xconnect-one-relay"
	cert, key := filepath.Join(root, "tls.crt"), filepath.Join(root, "tls.key")
	cfg.Runtime.RelayTLSCertificate, cfg.Runtime.RelayTLSPrivateKey = cert, key
	if err := cfg.Validate(); err != nil {
		t.Fatalf("valid apply config rejected: %v", err)
	}
	if err := os.MkdirAll(cfg.Runtime.RelayCredentialDir, 0o700); err != nil {
		t.Fatal(err)
	}
	for path, raw := range map[string]string{cert: "cert", key: "key"} {
		if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	credential := `{"id":"7b7f7a4f-938f-4aeb-86eb-53ee67c1a001","certificate_file":"` + cert + `","private_key_file":"` + key + `"}`
	credentialRef := "relay_credential_" + cfg.NodeID
	if err := os.WriteFile(filepath.Join(cfg.Runtime.RelayCredentialDir, credentialRef+".json"), []byte(credential), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(cfg.Apply.RuntimeSecretLKG, 0o700); err != nil {
		t.Fatal(err)
	}
	snapshot := GatewaySnapshot{SnapshotID: "snap_apply_01", Generation: 1, ProxyCore: "xray", WireGuard: GatewayWireGuard{InterfaceName: "wg-xco", ListenPort: 51820, Addresses: []string{"10.77.0.1/32"}, Peers: []GatewayPeer{{DeviceID: "dev-a", PublicKey: base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{2}, 32)), AllowedIPs: []string{"10.77.0.10/32"}}}}, Relay: GatewayRelay{Transport: "vless-tls-xudp", ListenHost: "0.0.0.0", ListenPort: 443, ServerNames: []string{"gateway.example"}, CredentialRefs: []string{credentialRef}}}
	baseline, err := RenderXrayRelayConfig(snapshot, cfg.Runtime.XrayInboundTag, CredentialResolver{Directory: cfg.Runtime.RelayCredentialDir})
	if err != nil {
		t.Fatal(err)
	}
	if err := atomicWrite(filepath.Join(cfg.Apply.RuntimeSecretLKG, "xray-runtime.json"), baseline); err != nil {
		t.Fatal(err)
	}
	artifact := ACLArtifact{SchemaVersion: 1, CompilerVersion: policyCompilerV1, NetworkID: "network", Revision: 0, DefaultAction: "deny", ProtectedFlows: requiredProtectedFlows, Rules: []ACLRule{}}
	var commands []string
	keyText := snapshot.WireGuard.Peers[0].PublicKey
	runner := commandRunnerFunc(func(_ context.Context, name string, args ...string) ([]byte, error) {
		commands = append(commands, name+" "+strings.Join(args, " "))
		joined := strings.Join(args, " ")
		switch {
		case name == "/usr/sbin/ip":
			return []byte(`[{"addr_info":[{"local":"10.77.0.1","prefixlen":32}]}]`), nil
		case name == "/usr/sbin/nft" && joined == "list tables":
			return []byte(""), nil
		case name == "/usr/bin/wg" && strings.HasPrefix(joined, "showconf "):
			return []byte("[Interface]\nListenPort = 51820\n"), nil
		case name == "/usr/bin/wg" && joined == "show wg-xco dump":
			return []byte("priv\tpub\t51820\toff\n" + keyText + "\t(none)\t(none)\t10.77.0.10/32\t0\t0\t0\t0\n"), nil
		default:
			return []byte("ok"), nil
		}
	})
	tx := RuntimeTransaction{Config: cfg, Runner: runner, Reader: WireGuardReader{Runner: runner, Binary: "/usr/bin/wg"}, DialContext: func(_ context.Context, _, _ string) (net.Conn, error) {
		a, b := net.Pipe()
		_ = b.Close()
		return a, nil
	}}
	if _, err := tx.Apply(context.Background(), snapshot, artifact); err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(commands, "\n")
	nftApply, xrayRemove, xrayAdd, wgApply := strings.Index(joined, "/usr/sbin/nft -f "), strings.Index(joined, " api rmi "), strings.Index(joined, " api adi "), strings.Index(joined, "/usr/bin/wg syncconf ")
	if !(nftApply >= 0 && xrayRemove > nftApply && xrayAdd > xrayRemove && wgApply > xrayAdd) {
		t.Fatalf("unsafe transaction order:\n%s", joined)
	}
	if strings.Contains(joined, "flush ruleset") || strings.Contains(joined, "systemctl") || strings.Contains(joined, "sudo") {
		t.Fatalf("unsafe command:\n%s", joined)
	}
	if err := tx.Commit(context.Background(), 1, snapshot.SnapshotID); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(filepath.Join(cfg.Apply.RuntimeSecretLKG, "xray-runtime.json"))
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("secret runtime LKG not protected: %v %+v", err, info)
	}
}

func TestExecRunnerRejectsShellAndEnvironment(t *testing.T) {
	runner := DefaultExecRunner("/usr/bin/wg")
	if _, err := runner.Run(context.Background(), "wg", "show"); err == nil {
		t.Fatal("relative binary accepted")
	}
	if _, err := runner.Run(context.Background(), "/bin/sh", "-c", "wg show"); err == nil {
		t.Fatal("shell outside allowlist accepted")
	}
	if _, err := runner.Run(context.Background(), "/usr/bin/wg", "show\x00bad"); err == nil {
		t.Fatal("NUL argv accepted")
	}
}

func TestWireGuardRollbackEvidenceNeverPersistsPrivateKeys(t *testing.T) {
	raw := []byte("[Interface]\nPrivateKey = secret-private-key\nListenPort = 51820\n\n[Peer]\nPublicKey = public\nPresharedKey = (none)\nAllowedIPs = 10.0.0.2/32\n")
	clean, err := sanitizeWireGuardBackup(raw)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(bytes.ToLower(clean), []byte("privatekey")) || bytes.Contains(clean, []byte("secret-private-key")) {
		t.Fatalf("private key leaked into rollback evidence: %s", clean)
	}
	if _, err := sanitizeWireGuardBackup([]byte("[Peer]\nPresharedKey = raw-secret\n")); err == nil {
		t.Fatal("non-empty preshared key would be persisted")
	}
}

func TestXrayRollbackAttemptsRestoreAfterRemoveFailure(t *testing.T) {
	var calls []string
	runner := commandRunnerFunc(func(_ context.Context, _ string, args ...string) ([]byte, error) {
		calls = append(calls, strings.Join(args, " "))
		if len(args) > 1 && args[1] == "rmi" {
			return nil, errors.New("not found")
		}
		return []byte("ok"), nil
	})
	previous := filepath.Join(t.TempDir(), "previous.json")
	if err := os.WriteFile(previous, []byte(`{"inbounds":[{"tag":"xconnect-one-relay","listen":"0.0.0.0","port":443}]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	tx := RuntimeTransaction{Config: Config{Runtime: RuntimeConfig{XrayBinary: "/usr/local/bin/xray", XrayAPIEndpoint: "127.0.0.1:10085", XrayInboundTag: "xconnect-one-relay"}}, Runner: runner, DialContext: func(_ context.Context, _, _ string) (net.Conn, error) {
		a, b := net.Pipe()
		_ = b.Close()
		return a, nil
	}}
	if err := tx.xrayRollback(context.Background(), previous, true); err != nil {
		t.Fatalf("restored state should win over remove not-found: %v", err)
	}
	if len(calls) < 3 || !strings.Contains(calls[1], "adi") || !strings.Contains(calls[2], "stats") {
		t.Fatalf("rollback did not add and verify baseline: %v", calls)
	}
}

func TestRuntimeRejectsSnapshotNodeBindingMismatchBeforeCommands(t *testing.T) {
	called := false
	tx := RuntimeTransaction{Config: Config{Runtime: RuntimeConfig{WireGuardInterface: "wg-xco", WireGuardListenPort: 51820, WireGuardAddresses: []string{"10.77.0.1/32"}, RelayListenHost: "0.0.0.0", RelayListenPort: 443}}, Runner: commandRunnerFunc(func(context.Context, string, ...string) ([]byte, error) { called = true; return nil, nil })}
	snapshot := GatewaySnapshot{WireGuard: GatewayWireGuard{InterfaceName: "wg-other", ListenPort: 51820, Addresses: []string{"10.77.0.1/32"}}, Relay: GatewayRelay{ListenHost: "0.0.0.0", ListenPort: 443}}
	if _, err := tx.Apply(context.Background(), snapshot, ACLArtifact{}); err == nil {
		t.Fatal("snapshot for another interface was accepted")
	}
	if called {
		t.Fatal("binding mismatch reached command runner")
	}
}

func TestRuntimeRejectsUnsortedAddressBindingBeforeCommands(t *testing.T) {
	called := false
	tx := RuntimeTransaction{Config: Config{Runtime: RuntimeConfig{WireGuardInterface: "wg-xco", WireGuardListenPort: 51820, WireGuardAddresses: []string{"10.77.0.2/32", "10.77.0.1/32"}, RelayListenHost: "0.0.0.0", RelayListenPort: 443}}, Runner: commandRunnerFunc(func(context.Context, string, ...string) ([]byte, error) { called = true; return nil, nil })}
	snapshot := GatewaySnapshot{WireGuard: GatewayWireGuard{InterfaceName: "wg-xco", ListenPort: 51820, Addresses: []string{"10.77.0.2/32", "10.77.0.1/32"}}, Relay: GatewayRelay{ListenHost: "0.0.0.0", ListenPort: 443}}
	if _, err := tx.Apply(context.Background(), snapshot, ACLArtifact{}); err == nil {
		t.Fatal("unsorted address binding was accepted")
	}
	if called {
		t.Fatal("unsorted binding reached command runner")
	}
}

func TestRuntimeRejectsRelayCredentialForAnotherNodeBeforeCommands(t *testing.T) {
	called := false
	tx := RuntimeTransaction{Config: Config{NodeID: "gw_test_01", Runtime: RuntimeConfig{WireGuardInterface: "wg-xco", WireGuardListenPort: 51820, WireGuardAddresses: []string{"10.77.0.1/32"}, RelayListenHost: "0.0.0.0", RelayListenPort: 443}}, Runner: commandRunnerFunc(func(context.Context, string, ...string) ([]byte, error) { called = true; return nil, nil })}
	snapshot := GatewaySnapshot{WireGuard: GatewayWireGuard{InterfaceName: "wg-xco", ListenPort: 51820, Addresses: []string{"10.77.0.1/32"}}, Relay: GatewayRelay{ListenHost: "0.0.0.0", ListenPort: 443, CredentialRefs: []string{"relay_credential_other_node"}}}
	if _, err := tx.Apply(context.Background(), snapshot, ACLArtifact{}); err == nil {
		t.Fatal("relay credential for another node was accepted")
	}
	if called {
		t.Fatal("relay credential mismatch reached command runner")
	}
}

func TestRollbackCoversCommandsThatFailAfterPartialMutation(t *testing.T) {
	for _, stage := range []string{"acl_mutating", "wg_mutating"} {
		t.Run(stage, func(t *testing.T) {
			root := t.TempDir()
			directory := filepath.Join(root, "transaction")
			if err := os.MkdirAll(directory, 0o700); err != nil {
				t.Fatal(err)
			}
			for name, raw := range map[string]string{
				"wireguard.previous":         "[Interface]\nListenPort = 51820\n",
				"nftables.previous":          "table inet xconnect_one { chain forward { type filter hook forward priority filter; policy accept; } }\n",
				"xray-runtime.previous.json": `{"inbounds":[{"tag":"xconnect-one-relay","listen":"0.0.0.0","port":443}]}`,
			} {
				if err := os.WriteFile(filepath.Join(directory, name), []byte(raw), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			var calls []string
			runner := commandRunnerFunc(func(_ context.Context, name string, args ...string) ([]byte, error) {
				calls = append(calls, name+" "+strings.Join(args, " "))
				return []byte("ok"), nil
			})
			tx := RuntimeTransaction{Config: Config{Runtime: RuntimeConfig{WireGuardBinary: "/usr/bin/wg", NFTablesBinary: "/usr/sbin/nft", XrayBinary: "/usr/local/bin/xray", WireGuardInterface: "wg-xco", XrayAPIEndpoint: "127.0.0.1:10085", XrayInboundTag: "xconnect-one-relay"}, Apply: ApplyConfig{RuntimeLastKnownGood: filepath.Join(root, "lkg")}}, Runner: runner, DialContext: func(_ context.Context, _, _ string) (net.Conn, error) {
				a, b := net.Pipe()
				_ = b.Close()
				return a, nil
			}}
			journal := runtimeJournal{SchemaVersion: 1, SnapshotID: "snap_partial_01", Generation: 1, Stage: stage, Directory: directory, HadNFTTable: true, HadXrayConfig: true}
			if err := tx.rollbackLocked(context.Background(), journal); err != nil {
				t.Fatal(err)
			}
			joined := strings.Join(calls, "\n")
			if !strings.Contains(joined, "/usr/sbin/nft -f ") {
				t.Fatalf("partial ACL was not restored: %s", joined)
			}
			if stage == "wg_mutating" {
				wg, xray, nft := strings.Index(joined, "/usr/bin/wg syncconf"), strings.Index(joined, " api rmi "), strings.LastIndex(joined, "/usr/sbin/nft -f ")
				if !(wg >= 0 && xray > wg && nft > xray) {
					t.Fatalf("partial WG rollback order unsafe: %s", joined)
				}
			}
		})
	}
}

func TestColdStartReconcilesProtectedXrayBaselineOnce(t *testing.T) {
	root := t.TempDir()
	secret := filepath.Join(root, "secret")
	if err := os.MkdirAll(secret, 0o700); err != nil {
		t.Fatal(err)
	}
	baseline := filepath.Join(secret, "xray-runtime.json")
	if err := os.WriteFile(baseline, []byte(`{"inbounds":[{"tag":"xconnect-one-relay","listen":"0.0.0.0","port":443}]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var calls []string
	runner := commandRunnerFunc(func(_ context.Context, name string, args ...string) ([]byte, error) {
		calls = append(calls, name+" "+strings.Join(args, " "))
		if len(args) > 1 && args[1] == "rmi" {
			return nil, errors.New("not found")
		}
		return []byte("ok"), nil
	})
	tx := RuntimeTransaction{Config: Config{Runtime: RuntimeConfig{XrayBinary: "/usr/local/bin/xray", XrayAPIEndpoint: "127.0.0.1:10085", XrayInboundTag: "xconnect-one-relay"}, Apply: ApplyConfig{LockFile: filepath.Join(root, "run", "lock"), RuntimeLastKnownGood: filepath.Join(root, "lkg"), RuntimeSecretLKG: secret}}, Runner: runner, DialContext: func(_ context.Context, _, _ string) (net.Conn, error) {
		a, b := net.Pipe()
		_ = b.Close()
		return a, nil
	}}
	if err := tx.Recover(context.Background(), Checkpoint{AppliedGeneration: 0}); err != nil {
		t.Fatal(err)
	}
	firstCount := len(calls)
	if firstCount < 3 || !strings.Contains(calls[1], " api adi ") || !strings.Contains(calls[2], " api statsquery ") {
		t.Fatalf("cold start did not reconcile+verify baseline: %v", calls)
	}
	if err := tx.Recover(context.Background(), Checkpoint{AppliedGeneration: 0}); err != nil {
		t.Fatal(err)
	}
	if len(calls) != firstCount {
		t.Fatalf("successful startup reconciliation repeated during polling: %v", calls)
	}
}

func TestApplyFailureRolledBackCanUpgradeToApplied(t *testing.T) {
	root := t.TempDir()
	cfg := testConfig(root)
	cfg.Mode = "apply"
	cfg.Apply.Enabled = true
	store, _ := NewStore(cfg.Snapshots)
	artifact := ACLArtifact{SchemaVersion: 1, CompilerVersion: policyCompilerV1, NetworkID: "network", Revision: 0, DefaultAction: "deny", ProtectedFlows: requiredProtectedFlows, Rules: []ACLRule{}}
	policyRaw, _ := json.Marshal(artifact)
	digest := sha256.Sum256(policyRaw)
	snapshot := GatewaySnapshot{SnapshotID: "snap_retry_01", Generation: 1, Policy: GatewayPolicy{Generation: 1, Backend: "nftables", RulesetSHA256: hex.EncodeToString(digest[:])}, WireGuard: GatewayWireGuard{Peers: []GatewayPeer{{DeviceID: "dev-a"}}}}
	raw, _ := json.Marshal(snapshot)
	controller := &fakeController{}
	runtime := &fakeRuntimeApplier{applyErrors: []error{errors.New("partial apply rolled back"), nil}}
	agent := &Agent{Config: cfg, Controller: controller, Policy: &fakePolicyProvider{raw: policyRaw}, Runtime: runtime, Store: store}
	agent.initialize()
	agent.runApply(context.Background(), raw, snapshot, Checkpoint{})
	checkpoint, _ := store.LoadCheckpoint()
	if checkpoint.RuntimeFault || len(controller.results) != 1 || controller.results[0].Result != "apply_failed_rolled_back" {
		t.Fatalf("safe failure semantics drifted: %+v %+v", checkpoint, controller.results)
	}
	agent.runApply(context.Background(), raw, snapshot, checkpoint)
	checkpoint, _ = store.LoadCheckpoint()
	if checkpoint.AppliedGeneration != 1 || checkpoint.RuntimeFault || len(controller.results) != 2 || controller.results[1].Result != "applied" || !controller.results[1].RuntimeApplied {
		t.Fatalf("failure did not upgrade exactly once to applied: %+v %+v", checkpoint, controller.results)
	}
	runtime.applyErrors = []error{errors.New("must not execute after success")}
	agent.runApply(context.Background(), raw, snapshot, checkpoint)
	if runtime.applyCalls != 2 || len(controller.results) != 2 {
		t.Fatalf("applied state regressed or re-executed: calls=%d results=%+v", runtime.applyCalls, controller.results)
	}
}

func TestRollbackFailurePersistsFailClosedAndBlocksRetry(t *testing.T) {
	root := t.TempDir()
	cfg := testConfig(root)
	cfg.Mode = "apply"
	cfg.Apply.Enabled = true
	store, _ := NewStore(cfg.Snapshots)
	artifact := ACLArtifact{SchemaVersion: 1, CompilerVersion: policyCompilerV1, NetworkID: "network", Revision: 0, DefaultAction: "deny", ProtectedFlows: requiredProtectedFlows, Rules: []ACLRule{}}
	policyRaw, _ := json.Marshal(artifact)
	digest := sha256.Sum256(policyRaw)
	snapshot := GatewaySnapshot{SnapshotID: "snap_fault_01", Generation: 9, Policy: GatewayPolicy{Generation: 9, Backend: "nftables", RulesetSHA256: hex.EncodeToString(digest[:])}}
	raw, _ := json.Marshal(snapshot)
	controller := &fakeController{raw: raw}
	policy := &fakePolicyProvider{raw: policyRaw}
	runtime := &fakeRuntimeApplier{applyErrors: []error{fmt.Errorf("%w: injected", ErrRuntimeRollbackFailed)}}
	agent := &Agent{Config: cfg, Controller: controller, Policy: policy, Runtime: runtime, Store: store}
	agent.initialize()
	agent.runApply(context.Background(), raw, snapshot, Checkpoint{})
	checkpoint, _ := store.LoadCheckpoint()
	if !checkpoint.RuntimeFault || checkpoint.RuntimeFaultGeneration != 9 || checkpoint.AppliedGeneration != 0 {
		t.Fatalf("rollback failure not persisted fail-closed: %+v", checkpoint)
	}
	agent.RunCycle(context.Background())
	if runtime.applyCalls != 1 || runtime.recoverCalls != 0 || policy.calls != 1 {
		t.Fatalf("fail-closed state retried runtime: apply=%d recover=%d policy=%d", runtime.applyCalls, runtime.recoverCalls, policy.calls)
	}
	if len(controller.heartbeats) != 1 || controller.heartbeats[0].ObservedGeneration != 9 || controller.heartbeats[0].AppliedGeneration != 0 {
		t.Fatalf("fault heartbeat generation semantics drifted: %+v", controller.heartbeats)
	}
	health := agent.Health()
	if health.Status != "unsafe-manual-recovery" || health.LastEventCode != "runtime_manual_recovery_required" || health.ObservedGeneration != 9 || health.AppliedGeneration != 0 {
		t.Fatalf("fault health is ambiguous: %+v", health)
	}
}

func TestPolicyFailureUsesPublicApplyRejectedEnum(t *testing.T) {
	root := t.TempDir()
	cfg := testConfig(root)
	cfg.Mode = "apply"
	cfg.Apply.Enabled = true
	store, _ := NewStore(cfg.Snapshots)
	controller := &fakeController{}
	snapshot := GatewaySnapshot{SnapshotID: "snap_policy_bad", Generation: 2, Policy: GatewayPolicy{Generation: 2, RulesetSHA256: strings.Repeat("a", 64)}}
	agent := &Agent{Config: cfg, Controller: controller, Policy: &fakePolicyProvider{raw: []byte(`{"unexpected":true}`)}, Runtime: &fakeRuntimeApplier{}, Store: store}
	agent.initialize()
	agent.runApply(context.Background(), []byte(`{}`), snapshot, Checkpoint{})
	if len(controller.results) != 1 || controller.results[0].Result != "apply_rejected" || controller.results[0].RuntimeApplied {
		t.Fatalf("policy failure escaped public enum: %+v", controller.results)
	}
	if agent.Health().LastEventCode != "policy_artifact_rejected" {
		t.Fatalf("local reason was not retained: %+v", agent.Health())
	}
}

func TestRollbackFailureInstallsAndVerifiesEmergencyQuarantine(t *testing.T) {
	root := t.TempDir()
	var calls []string
	runner := commandRunnerFunc(func(_ context.Context, name string, args ...string) ([]byte, error) {
		calls = append(calls, name+" "+strings.Join(args, " "))
		if strings.Join(args, " ") == "-json link show dev wg-xco" {
			return []byte(`[{"operstate":"DOWN"}]`), nil
		}
		return []byte("ok"), nil
	})
	tx := RuntimeTransaction{Config: Config{Runtime: RuntimeConfig{IPBinary: "/usr/sbin/ip", WireGuardInterface: "wg-xco"}, Apply: ApplyConfig{RuntimeLastKnownGood: filepath.Join(root, "lkg")}}, Runner: runner}
	err := tx.rollbackFailure(context.Background(), runtimeJournal{SchemaVersion: 1, SnapshotID: "snap_fault_01", Generation: 2, Directory: root}, errors.New("rollback failed"))
	if !errors.Is(err, ErrRuntimeRollbackFailed) || !errors.Is(err, ErrRuntimeQuarantined) || errors.Is(err, ErrRuntimeUnsafe) {
		t.Fatalf("quarantine classification wrong: %v", err)
	}
	joined := strings.Join(calls, "\n")
	if !strings.Contains(joined, "/usr/sbin/ip link set dev wg-xco down") || !strings.Contains(joined, "/usr/sbin/ip -json link show dev wg-xco") {
		t.Fatalf("emergency isolation not applied/read back: %s", joined)
	}
	unsafe := RuntimeTransaction{Config: tx.Config, Runner: commandRunnerFunc(func(context.Context, string, ...string) ([]byte, error) { return nil, errors.New("denied") })}
	err = unsafe.rollbackFailure(context.Background(), runtimeJournal{}, errors.New("rollback failed"))
	if !errors.Is(err, ErrRuntimeUnsafe) || errors.Is(err, ErrRuntimeQuarantined) {
		t.Fatalf("unverified isolation called fail-closed: %v", err)
	}
}

func TestRuntimeFaultCheckpointInvariantAndExplicitClear(t *testing.T) {
	root := t.TempDir()
	store, _ := NewStore(testConfig(root).Snapshots)
	fault := Checkpoint{AppliedGeneration: 3, RuntimeFault: true, RuntimeFaultSnapshotID: "snap_fault_04", RuntimeFaultGeneration: 4, RuntimeQuarantined: true}
	if err := store.saveCheckpoint(fault); err != nil {
		t.Fatal(err)
	}
	if _, err := store.LoadCheckpoint(); err != nil {
		t.Fatalf("valid runtime fault rejected: %v", err)
	}
	if err := store.ClearRuntimeFault("wrong_snapshot"); err == nil {
		t.Fatal("mismatched fault acknowledgement accepted")
	}
	if err := store.ClearRuntimeFault("snap_fault_04"); err != nil {
		t.Fatal(err)
	}
	cleared, err := store.LoadCheckpoint()
	if err != nil || cleared.RuntimeFault || cleared.RuntimeQuarantined {
		t.Fatalf("fault clear failed: %+v %v", cleared, err)
	}
	fault.RuntimeFault = false
	if err := store.saveCheckpoint(fault); err != nil {
		t.Fatal(err)
	}
	if _, err := store.LoadCheckpoint(); err == nil {
		t.Fatal("orphan runtime fault fields accepted")
	}
}

func FuzzPolicyArtifactStrictDecoder(f *testing.F) {
	seed, _ := os.ReadFile("testdata/network-policy-enforcement.golden.json")
	f.Add(bytes.TrimSuffix(seed, []byte("\n")))
	f.Add([]byte(`{} {}`))
	f.Fuzz(func(t *testing.T, raw []byte) {
		if len(raw) > 1<<20 {
			return
		}
		digest := sha256.Sum256(raw)
		snapshot := GatewaySnapshot{Policy: GatewayPolicy{RulesetSHA256: hex.EncodeToString(digest[:])}}
		_, _ = ValidatePolicyArtifact(raw, snapshot)
	})
}
