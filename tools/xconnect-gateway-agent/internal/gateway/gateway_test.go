package gateway

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func testConfig(root string) Config {
	return Config{
		SchemaVersion: 1, NodeID: "gw_test_01", Mode: "shadow",
		ControlPlane:     ControlPlaneConfig{URL: "https://controller.example", PollIntervalSeconds: 60, CredentialsFile: filepath.Join(root, "credential.token"), SnapshotSigningPublicKeyFile: filepath.Join(root, "signing.pub"), SnapshotSigningKeyID: "key_test_01", APIVersion: "v1"},
		Identity:         IdentityConfig{NodeID: "gw_test_01", CredentialType: "short-lived-bearer-file", CredentialFile: filepath.Join(root, "credential.token")},
		ProviderManifest: filepath.Join(root, "provider.json"),
		Authority:        AuthorityConfig{ProjectionSource: "static-shadow", ReadinessEvidenceFile: filepath.Join(root, "accounts-only-readiness.json")},
		Runtime:          RuntimeConfig{ProxyCore: "xray", ProxyCoreVersion: "26.3.27", XrayBinary: "/usr/local/bin/xray", XrayConfig: "/etc/xray/config.json", XrayService: "xray", WireGuardInterface: "wg-xco", WireGuardConfig: "/etc/wireguard/wg-xco.conf", WireGuardService: "wg-quick@wg-xco"},
		Snapshots:        SnapshotConfig{CandidateDir: filepath.Join(root, "candidate"), LastKnownGoodDir: filepath.Join(root, "lkg"), EvidenceDir: filepath.Join(root, "evidence"), MinimumSchema: 1, MaximumSchema: 1, EmptyPeerSnapshot: "require-explicit-override"},
		Health:           HealthConfig{ListenHost: "127.0.0.1", ListenPort: 9789, Path: "/healthz"},
		Logging:          LoggingConfig{Format: "json", RedactFields: []string{"authorization", "credential", "token", "signature.value"}},
	}
}

func TestConfigStrictShadowDecode(t *testing.T) {
	root := t.TempDir()
	cfg := testConfig(root)
	if err := cfg.Validate(); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(cfg)
	path := filepath.Join(root, "gateway.json")
	os.WriteFile(path, raw, 0o600)
	if _, err := LoadConfig(path); err != nil {
		t.Fatal(err)
	}
	var object map[string]any
	json.Unmarshal(raw, &object)
	object["unknown_runtime_switch"] = true
	raw, _ = json.Marshal(object)
	os.WriteFile(path, raw, 0o600)
	if _, err := LoadConfig(path); err == nil {
		t.Fatal("unknown config field accepted")
	}
	cfg.Apply.Enabled = true
	if err := cfg.Validate(); err == nil {
		t.Fatal("runtime apply enabled config accepted")
	}
}

func TestAccountsOnlyAuthorityRequiresRuntimeApply(t *testing.T) {
	cfg := testConfig(t.TempDir())
	cfg.Authority.ProjectionSource = "accounts-only"
	if err := cfg.Validate(); err == nil {
		t.Fatal("accounts-only authority was accepted in shadow mode")
	}
	if cfg.Authority.ProjectionSource = "static-shadow"; cfg.Validate() != nil {
		t.Fatal("default static-shadow authority was rejected")
	}
}

func TestRoleFixtureLoadsAsRuntimeConfig(t *testing.T) {
	fixture := filepath.Join("..", "..", "..", "..", "tests", "fixtures", "xconnect-gateway", "gateway.json")
	cfg, err := LoadConfig(fixture)
	if err != nil {
		t.Fatalf("role fixture cannot start the real Agent: %v", err)
	}
	if cfg.Mode != "shadow" || cfg.Runtime.ProxyCore != "xray" || cfg.Apply.Enabled {
		t.Fatalf("unsafe role fixture: %+v", cfg)
	}
}

func signedSnapshot(t *testing.T, privateKey ed25519.PrivateKey, now time.Time, generation, previous uint64, snapshotID string) ([]byte, GatewaySnapshot) {
	t.Helper()
	peerKey := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{2}, 32))
	snapshot := GatewaySnapshot{
		SchemaVersion: 1, SnapshotID: snapshotID, NodeID: "gw_test_01", Generation: generation, ExpectedPreviousGeneration: previous,
		IssuedAt: now.Add(-time.Minute).UTC().Truncate(time.Second), ExpiresAt: now.Add(time.Hour).UTC().Truncate(time.Second), ProxyCore: "xray",
		Safety:    GatewaySafety{MaxPeerRemovalPercent: 100},
		WireGuard: GatewayWireGuard{InterfaceName: "wg-xco", ListenPort: 51820, Addresses: []string{"10.77.0.1/32"}, Peers: []GatewayPeer{{DeviceID: "dev_test_01", PublicKey: peerKey, AllowedIPs: []string{"10.77.0.10/32"}, PersistentKeepaliveSeconds: 25}}},
		Relay:     GatewayRelay{Transport: "vless-tls-xudp", ListenHost: "0.0.0.0", ListenPort: 443, ServerNames: []string{"gateway.example"}, CredentialRefs: []string{"vault_test_01"}},
		Policy:    GatewayPolicy{Generation: 1, Backend: "nftables", RulesetSHA256: strings.Repeat("b", 64)},
		Signature: Signature{Algorithm: "Ed25519", KeyID: "key_test_01"},
	}
	payload, err := snapshot.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	snapshot.Signature.Value = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, payload))
	raw, err := json.Marshal(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	return raw, snapshot
}

func TestSnapshotSecurityGates(t *testing.T) {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 8, 28, 1, 0, 0, 0, time.UTC)
	raw, snapshot := signedSnapshot(t, privateKey, now, 1, 0, "snap_test_01")
	decoded, err := DecodeGatewaySnapshot(raw)
	if err != nil || decoded.Validate(now, "gw_test_01", "key_test_01", publicKey, nil) != nil {
		t.Fatalf("valid snapshot rejected: decode=%v validate=%v", err, decoded.Validate(now, "gw_test_01", "key_test_01", publicKey, nil))
	}

	tests := []struct {
		name   string
		mutate func(*GatewaySnapshot)
		prev   *GatewaySnapshot
	}{
		{"expired", func(s *GatewaySnapshot) { s.ExpiresAt = now.Add(-time.Second) }, nil},
		{"only xray", func(s *GatewaySnapshot) { s.ProxyCore = "unsupported" }, nil},
		{"bad signature", func(s *GatewaySnapshot) { s.Signature.Value = base64.StdEncoding.EncodeToString(make([]byte, 64)) }, nil},
		{"empty peers", func(s *GatewaySnapshot) { s.WireGuard.Peers = nil }, nil},
		{"replay", func(s *GatewaySnapshot) { s.Generation = 2; s.ExpectedPreviousGeneration = 1 }, &snapshot},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidate := snapshot
			test.mutate(&candidate)
			if err := candidate.Validate(now, "gw_test_01", "key_test_01", publicKey, test.prev); err == nil {
				t.Fatal("unsafe snapshot accepted")
			}
		})
	}

	var object map[string]any
	json.Unmarshal(raw, &object)
	object["auth_id"] = "must-not-cross"
	secretRaw, _ := json.Marshal(object)
	if _, err := DecodeGatewaySnapshot(secretRaw); err == nil {
		t.Fatal("secret field accepted")
	}
}

func TestSnapshotTransitionAndEmptyOverride(t *testing.T) {
	publicKey, privateKey, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().UTC()
	_, previous := signedSnapshot(t, privateKey, now, 1, 0, "snap_test_01")
	_, next := signedSnapshot(t, privateKey, now, 2, 1, "snap_test_02")
	if err := next.Validate(now, "gw_test_01", "key_test_01", publicKey, &previous); err != nil {
		t.Fatal(err)
	}
	next.WireGuard.Peers = nil
	next.Safety.AllowEmptyPeers = true
	next.Safety.MaxPeerRemovalPercent = 100
	payload, _ := next.SigningBytes()
	next.Signature.Value = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, payload))
	if err := next.Validate(now, "gw_test_01", "key_test_01", publicKey, &previous); err != nil {
		t.Fatalf("explicit safe empty snapshot rejected: %v", err)
	}
}

func TestGatewaySnapshotSigningGoldenVector(t *testing.T) {
	seed := make([]byte, ed25519.SeedSize)
	for index := range seed {
		seed[index] = byte(index)
	}
	privateKey := ed25519.NewKeyFromSeed(seed)
	now := time.Date(2026, 8, 28, 12, 0, 0, 0, time.UTC)
	_, snapshot := signedSnapshot(t, privateKey, now, 42, 41, "snap_vector_42")
	payload, err := snapshot.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	wantPayload := `{"schema_version":1,"snapshot_id":"snap_vector_42","node_id":"gw_test_01","generation":42,"expected_previous_generation":41,"issued_at":"2026-08-28T11:59:00Z","expires_at":"2026-08-28T13:00:00Z","proxy_core":"xray","safety":{"allow_empty_peers":false,"max_peer_removal_percent":100},"wireguard":{"interface_name":"wg-xco","listen_port":51820,"addresses":["10.77.0.1/32"],"peers":[{"device_id":"dev_test_01","public_key":"AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI=","allowed_ips":["10.77.0.10/32"],"persistent_keepalive_seconds":25}]},"relay":{"transport":"vless-tls-xudp","listen_host":"0.0.0.0","listen_port":443,"server_names":["gateway.example"],"credential_refs":["vault_test_01"]},"policy":{"generation":1,"backend":"nftables","ruleset_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}`
	if string(payload) != wantPayload {
		t.Fatalf("signing payload drifted\ngot:  %s\nwant: %s", payload, wantPayload)
	}
	gotSignature := base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, payload))
	wantSignature := "6ntypTf83jAGH4aTxIsmkPvGnBiI+3d+YLmAtRLi2G6d/BZW/PPB00ANbMH/yVrg+cOOpDDQMSDtKB8WUeIyBw=="
	if gotSignature != wantSignature {
		t.Fatalf("signature vector drifted: got %s", gotSignature)
	}
}

func TestSnapshotCanonicalDomainGates(t *testing.T) {
	publicKey, privateKey, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Date(2026, 8, 28, 12, 0, 0, 0, time.UTC)
	_, baseline := signedSnapshot(t, privateKey, now, 1, 0, "snap_domain_01")
	tests := []struct {
		name   string
		mutate func(*GatewaySnapshot)
		keyID  string
	}{
		{"subsecond timestamp", func(s *GatewaySnapshot) { s.IssuedAt = s.IssuedAt.Add(time.Nanosecond) }, "key_test_01"},
		{"non UTC timestamp", func(s *GatewaySnapshot) { s.IssuedAt = s.IssuedAt.In(time.FixedZone("offset", 3600)) }, "key_test_01"},
		{"keepalive overflow", func(s *GatewaySnapshot) { s.WireGuard.Peers[0].PersistentKeepaliveSeconds = 65536 }, "key_test_01"},
		{"relay host", func(s *GatewaySnapshot) { s.Relay.ListenHost = "" }, "key_test_01"},
		{"relay port", func(s *GatewaySnapshot) { s.Relay.ListenPort = 0 }, "key_test_01"},
		{"relay server names", func(s *GatewaySnapshot) { s.Relay.ServerNames = nil }, "key_test_01"},
		{"relay duplicate credential refs", func(s *GatewaySnapshot) { s.Relay.CredentialRefs = []string{"vault_test_01", "vault_test_01"} }, "key_test_01"},
		{"policy generation", func(s *GatewaySnapshot) { s.Policy.Generation = 0 }, "key_test_01"},
		{"invalid key id", func(s *GatewaySnapshot) { s.Signature.KeyID = "x" }, "x"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidate := baseline
			candidate.WireGuard.Peers = append([]GatewayPeer(nil), baseline.WireGuard.Peers...)
			candidate.Relay.ServerNames = append([]string(nil), baseline.Relay.ServerNames...)
			candidate.Relay.CredentialRefs = append([]string(nil), baseline.Relay.CredentialRefs...)
			test.mutate(&candidate)
			payload, _ := candidate.SigningBytes()
			candidate.Signature.Value = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, payload))
			if err := candidate.Validate(now, "gw_test_01", test.keyID, publicKey, nil); err == nil {
				t.Fatal("non-canonical snapshot accepted")
			}
		})
	}
}

func TestStoreAtomicPermissionsAndLKG(t *testing.T) {
	root := t.TempDir()
	store, err := NewStore(testConfig(root).Snapshots)
	if err != nil {
		t.Fatal(err)
	}
	_, privateKey, _ := ed25519.GenerateKey(rand.Reader)
	raw, snapshot := signedSnapshot(t, privateKey, time.Now(), 1, 0, "snap_test_01")
	if err := store.SaveCandidate(raw); err != nil {
		t.Fatal(err)
	}
	pending := newShadowResult("gw_test_01", snapshot, "shadow_validated", DiffSummary{Status: "available", Equal: true})
	if err := store.CommitObserved(raw, snapshot, pending); err != nil {
		t.Fatal(err)
	}
	if err := store.SaveEvidence(DiffSummary{Status: "available", Equal: true}, 1); err != nil {
		t.Fatal(err)
	}
	for _, dir := range []string{store.candidateDir, store.lkgDir, store.evidenceDir} {
		info, _ := os.Stat(dir)
		if info.Mode().Perm() != 0o700 {
			t.Fatalf("directory mode = %o", info.Mode().Perm())
		}
	}
	for _, file := range []string{filepath.Join(store.candidateDir, "gateway-snapshot.json"), filepath.Join(store.lkgDir, "snapshots", snapshot.SnapshotID+".json"), filepath.Join(store.lkgDir, "checkpoint.json"), filepath.Join(store.evidenceDir, "shadow-diff.json")} {
		info, _ := os.Stat(file)
		if info == nil {
			t.Fatalf("missing state file %s", file)
		}
		if info.Mode().Perm() != 0o600 {
			t.Fatalf("file mode = %o", info.Mode().Perm())
		}
	}
	checkpoint, _ := store.LoadCheckpoint()
	if checkpoint.ObservedGeneration != 1 || checkpoint.ObservedSnapshotID != "snap_test_01" || checkpoint.AppliedGeneration != 0 || checkpoint.PendingResult == nil || checkpoint.PendingResult.RuntimeApplied {
		t.Fatalf("unexpected checkpoint: %+v", checkpoint)
	}
	if err := store.MarkResultReported(checkpoint, pending); err != nil {
		t.Fatal(err)
	}
	checkpoint, _ = store.LoadCheckpoint()
	if checkpoint.PendingResult != nil {
		t.Fatal("reported shadow result remained queued")
	}
	checkpointPath := filepath.Join(store.lkgDir, "checkpoint.json")
	for _, invalid := range []string{`{"observed_generation":1,"unknown":true}`, `{} {}`} {
		if err := os.WriteFile(checkpointPath, []byte(invalid), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := store.LoadCheckpoint(); err == nil {
			t.Fatalf("non-strict checkpoint accepted: %q", invalid)
		}
	}
}

func TestStoreCheckpointFailureKeepsPreviousImmutableLKG(t *testing.T) {
	root := t.TempDir()
	store, err := NewStore(testConfig(root).Snapshots)
	if err != nil {
		t.Fatal(err)
	}
	_, privateKey, _ := ed25519.GenerateKey(rand.Reader)
	oldRaw, oldSnapshot := signedSnapshot(t, privateKey, time.Now(), 1, 0, "snap_old_01")
	oldResult := newShadowResult("gw_test_01", oldSnapshot, "shadow_validated", DiffSummary{Status: "available", Equal: true})
	if err := store.CommitObserved(oldRaw, oldSnapshot, oldResult); err != nil {
		t.Fatal(err)
	}
	newRaw, newSnapshot := signedSnapshot(t, privateKey, time.Now(), 2, 1, "snap_new_02")
	newResult := newShadowResult("gw_test_01", newSnapshot, "shadow_validated", DiffSummary{Status: "available", Equal: true})
	originalWrite := store.writeFile
	store.writeFile = func(path string, raw []byte) error {
		if path == filepath.Join(store.lkgDir, "checkpoint.json") {
			return errors.New("injected checkpoint promotion failure")
		}
		return originalWrite(path, raw)
	}
	if err := store.CommitObserved(newRaw, newSnapshot, newResult); err == nil {
		t.Fatal("checkpoint failure was accepted")
	}
	restarted, err := NewStore(testConfig(root).Snapshots)
	if err != nil {
		t.Fatal(err)
	}
	loaded, err := restarted.LoadLastKnownGood()
	if err != nil {
		t.Fatal(err)
	}
	if loaded == nil || loaded.SnapshotID != oldSnapshot.SnapshotID || loaded.Generation != oldSnapshot.Generation {
		t.Fatalf("restart did not retain previous LKG: %+v", loaded)
	}
	if _, err := os.Stat(filepath.Join(store.lkgDir, "snapshots", newSnapshot.SnapshotID+".json")); err != nil {
		t.Fatalf("new immutable orphan was not safely written: %v", err)
	}
}

type fakeRunner struct {
	name string
	args []string
	out  []byte
	err  error
}

func (r *fakeRunner) Run(_ context.Context, name string, args ...string) ([]byte, error) {
	r.name, r.args = name, append([]string(nil), args...)
	return r.out, r.err
}

func TestWireGuardReadOnlyDiffAndFailure(t *testing.T) {
	key := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{2}, 32))
	runner := &fakeRunner{out: []byte("priv\tpub\t51820\toff\n" + key + "\t(none)\t127.0.0.1:1\t10.77.0.10/32\t0\t0\t0\t25\n")}
	reader := WireGuardReader{Runner: runner, Binary: "wg"}
	peers, err := reader.Peers(context.Background(), "wg-xco")
	if err != nil {
		t.Fatal(err)
	}
	if runner.name != "wg" || strings.Join(runner.args, " ") != "show wg-xco dump" {
		t.Fatalf("unexpected command: %s %v", runner.name, runner.args)
	}
	diff := ComparePeers([]GatewayPeer{{PublicKey: key, AllowedIPs: []string{"10.77.0.10/32"}}}, peers)
	if !diff.Equal || diff.ProjectedPeers != 1 || diff.CurrentPeers != 1 {
		t.Fatalf("unexpected diff: %+v", diff)
	}
	runner.err = errors.New("denied")
	if _, err := reader.Peers(context.Background(), "wg-xco"); err == nil || strings.Contains(err.Error(), key) {
		t.Fatal("WireGuard failure missing or leaked key")
	}
}

func TestHTTPControllerHeartbeatRotationAnd401Redaction(t *testing.T) {
	root := t.TempDir()
	credential := filepath.Join(root, "credential.token")
	os.WriteFile(credential, []byte("token-one\n"), 0o600)
	var mu sync.Mutex
	seen := []string{}
	paths := []string{}
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		mu.Lock()
		seen = append(seen, request.Header.Get("Authorization"))
		paths = append(paths, request.URL.Path)
		mu.Unlock()
		if strings.HasSuffix(request.URL.Path, "/snapshot") {
			response.WriteHeader(http.StatusNoContent)
			return
		}
		response.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	controller, _ := NewHTTPController(server.URL, credential, server.Client())
	if err := controller.Heartbeat(context.Background(), Heartbeat{NodeID: "gw_test_01"}); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(credential, []byte("token-two\n"), 0o600)
	if _, err := controller.PlannedSnapshot(context.Background(), "gw_test_01"); !errors.Is(err, ErrNoPlannedSnapshot) {
		t.Fatalf("expected no snapshot, got %v", err)
	}
	if err := controller.ReportApplyResult(context.Background(), ApplyResult{NodeID: "gw_test_01"}); err != nil {
		t.Fatal(err)
	}
	if strings.Join(seen, ",") != "Bearer token-one,Bearer token-two,Bearer token-two" {
		t.Fatalf("credential was not re-read: %v", seen)
	}
	if strings.Join(paths, ",") != "/api/internal/overlay/v1/nodes/heartbeat,/api/internal/overlay/v1/nodes/gw_test_01/snapshot,/api/internal/overlay/v1/nodes/gw_test_01/apply-result" {
		t.Fatalf("unexpected internal API paths: %v", paths)
	}

	secretBody := "server-body-must-not-leak"
	unauthorized := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		http.Error(response, secretBody, http.StatusUnauthorized)
	}))
	defer unauthorized.Close()
	controller, _ = NewHTTPController(unauthorized.URL, credential, unauthorized.Client())
	err := controller.Heartbeat(context.Background(), Heartbeat{})
	if err == nil || strings.Contains(err.Error(), "token-two") || strings.Contains(err.Error(), secretBody) {
		t.Fatalf("401 error leaked sensitive content: %v", err)
	}
	if _, err := NewHTTPController("http://controller.example", credential, nil); err == nil {
		t.Fatal("non-HTTPS Controller URL accepted")
	}
	for _, responseBody := range []string{`{"unexpected":true}`, `{} {}`} {
		strictServer := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
			response.Header().Set("Content-Type", "application/json")
			_, _ = response.Write([]byte(responseBody))
		}))
		strictController, newErr := NewHTTPController(strictServer.URL, credential, strictServer.Client())
		if newErr != nil {
			strictServer.Close()
			t.Fatal(newErr)
		}
		_, strictErr := strictController.PlannedSnapshot(context.Background(), "gw_test_01")
		strictServer.Close()
		if strictErr == nil {
			t.Fatalf("non-strict Controller response accepted: %q", responseBody)
		}
	}
	wrongTypeServer := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "text/plain")
		_, _ = response.Write([]byte(`{}`))
	}))
	wrongTypeController, _ := NewHTTPController(wrongTypeServer.URL, credential, wrongTypeServer.Client())
	_, wrongTypeErr := wrongTypeController.PlannedSnapshot(context.Background(), "gw_test_01")
	wrongTypeServer.Close()
	if wrongTypeErr == nil {
		t.Fatal("non-JSON Controller response accepted")
	}
}

func TestHTTPControllerPolicyArtifactInternalContract(t *testing.T) {
	root := t.TempDir()
	credential := filepath.Join(root, "credential.token")
	if err := os.WriteFile(credential, []byte("xgn_test_token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	rawWithNewline, err := os.ReadFile("testdata/network-policy-enforcement.golden.json")
	if err != nil {
		t.Fatal(err)
	}
	raw := bytes.TrimSuffix(rawWithNewline, []byte("\n"))
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/internal/overlay/v1/nodes/gw_test_01/policy-artifacts/9/58941760a9ab4568d2e72a6f34a2cede891d8e678346da8e886d86263e5b780c" || request.Header.Get("Authorization") != "Bearer xgn_test_token" || request.Header.Get("Accept") != "application/vnd.xconnect.gateway-policy.v1+json" {
			http.Error(response, "bad internal contract", http.StatusBadRequest)
			return
		}
		response.Header().Set("Content-Type", "application/vnd.xconnect.gateway-policy.v1+json")
		response.Header().Set("Cache-Control", "no-store")
		response.Header().Set("Vary", "Authorization")
		_, _ = response.Write(raw)
	}))
	defer server.Close()
	controller, err := NewHTTPController(server.URL, credential, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	got, err := controller.PolicyArtifact(context.Background(), "gw_test_01", 9, "58941760a9ab4568d2e72a6f34a2cede891d8e678346da8e886d86263e5b780c")
	if err != nil || !bytes.Equal(got, raw) {
		t.Fatalf("policy artifact contract failed: %v", err)
	}
}

type fakeController struct {
	raw            []byte
	heartbeats     []Heartbeat
	results        []ApplyResult
	reportAttempts int
	reportFailures int
}

func (c *fakeController) Heartbeat(_ context.Context, heartbeat Heartbeat) error {
	c.heartbeats = append(c.heartbeats, heartbeat)
	return nil
}
func (c *fakeController) PlannedSnapshot(context.Context, string) ([]byte, error) { return c.raw, nil }
func (c *fakeController) ReportApplyResult(_ context.Context, result ApplyResult) error {
	c.reportAttempts++
	if c.reportFailures > 0 {
		c.reportFailures--
		return errors.New("unavailable")
	}
	c.results = append(c.results, result)
	return nil
}

func TestAgentCycleHealthAndGracefulShutdown(t *testing.T) {
	root := t.TempDir()
	publicKey, privateKey, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().UTC().Truncate(time.Second)
	raw, _ := signedSnapshot(t, privateKey, now, 1, 0, "snap_test_01")
	store, _ := NewStore(testConfig(root).Snapshots)
	controller := &fakeController{raw: raw}
	key := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{2}, 32))
	runner := &fakeRunner{out: []byte("priv\tpub\t51820\toff\n" + key + "\t(none)\t127.0.0.1:1\t10.77.0.10/32\t0\t0\t0\t25\n")}
	var logs bytes.Buffer
	agent := &Agent{Config: testConfig(root), Controller: controller, Store: store, WireGuard: WireGuardReader{Runner: runner}, PublicKey: publicKey, Version: "0.1.0", Logger: slog.New(slog.NewJSONHandler(&logs, nil)), Now: func() time.Time { return now }}
	agent.RunCycle(context.Background())
	health := agent.Health()
	if health.Status != "ready" || health.Mode != "shadow" || health.ProxyCore != "xray" || health.RuntimeApplyEnabled || health.ObservedGeneration != 1 || health.AppliedGeneration != 0 || !health.Diff.Equal {
		t.Fatalf("unexpected health: %+v", health)
	}
	if len(controller.heartbeats) != 1 || len(controller.results) != 1 || controller.results[0].Result != "shadow_validated" {
		t.Fatalf("controller flow incomplete: heartbeat=%d results=%+v", len(controller.heartbeats), controller.results)
	}
	if controller.results[0].RuntimeApplied || controller.results[0].AppliedGeneration != 0 || controller.results[0].ObservedGeneration != 1 || controller.heartbeats[0].AppliedGeneration != 0 {
		t.Fatalf("shadow telemetry implied runtime apply: heartbeat=%+v result=%+v", controller.heartbeats[0], controller.results[0])
	}
	agent.RunCycle(context.Background())
	if len(controller.results) != 1 || len(controller.heartbeats) != 2 {
		t.Fatalf("identical snapshot was not idempotent: heartbeat=%d results=%d", len(controller.heartbeats), len(controller.results))
	}
	if strings.Contains(logs.String(), key) || strings.Contains(logs.String(), "signature") {
		t.Fatal("logs contain prohibited cryptographic material")
	}

	cfg := testConfig(t.TempDir())
	listener, _ := net.Listen("tcp", "127.0.0.1:0")
	cfg.Health.ListenPort = listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	store, _ = NewStore(cfg.Snapshots)
	agent = &Agent{Config: cfg, Controller: &noSnapshotController{}, Store: store, WireGuard: WireGuardReader{Runner: &fakeRunner{}}, PublicKey: publicKey, Version: "0.1.0", Now: time.Now}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- agent.Run(ctx) }()
	var response *http.Response
	var err error
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		response, err = http.Get("http://" + cfg.HealthAddress() + cfg.Health.Path)
		if err == nil {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err != nil {
		t.Fatalf("health endpoint did not start: %v", err)
	}
	var served HealthState
	json.NewDecoder(response.Body).Decode(&served)
	response.Body.Close()
	if served.Status != "ready" || served.RuntimeApplyEnabled {
		t.Fatalf("unsafe health response: %+v", served)
	}
	request, _ := http.NewRequest(http.MethodPost, "http://"+cfg.HealthAddress()+cfg.Health.Path, nil)
	methodResponse, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	methodResponse.Body.Close()
	if methodResponse.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("health endpoint accepted POST: %d", methodResponse.StatusCode)
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("agent did not shut down gracefully")
	}
}

func TestApplyResultFailureIsRetriedWithoutReobserving(t *testing.T) {
	root := t.TempDir()
	publicKey, privateKey, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().UTC().Truncate(time.Second)
	raw, _ := signedSnapshot(t, privateKey, now, 1, 0, "snap_retry_01")
	store, _ := NewStore(testConfig(root).Snapshots)
	controller := &fakeController{raw: raw, reportFailures: 1}
	agent := &Agent{Config: testConfig(root), Controller: controller, Store: store, WireGuard: WireGuardReader{Runner: &fakeRunner{err: errors.New("unavailable")}}, PublicKey: publicKey, Version: "0.1.0", Now: func() time.Time { return now }}
	agent.RunCycle(context.Background())
	checkpoint, _ := store.LoadCheckpoint()
	if checkpoint.PendingResult == nil || checkpoint.ObservedGeneration != 1 || checkpoint.AppliedGeneration != 0 {
		t.Fatalf("failed result was not durably queued: %+v", checkpoint)
	}
	agent.RunCycle(context.Background())
	checkpoint, _ = store.LoadCheckpoint()
	if checkpoint.PendingResult != nil || controller.reportAttempts != 2 || len(controller.results) != 1 {
		t.Fatalf("result retry was not idempotent: checkpoint=%+v attempts=%d results=%d", checkpoint, controller.reportAttempts, len(controller.results))
	}
}

func TestProtectedFilesRejectWidePermissionsAndSymlinks(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "credential.token")
	if err := os.WriteFile(path, []byte("short-lived"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := readProtectedFile(path, "credential"); err == nil {
		t.Fatal("world-readable credential accepted")
	}
	if err := os.Chmod(path, 0o640); err != nil {
		t.Fatal(err)
	}
	if _, err := readProtectedFile(path, "credential"); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "credential-link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readProtectedFile(link, "credential"); err == nil {
		t.Fatal("credential symlink accepted")
	}
	if err := os.WriteFile(path, make([]byte, (1<<20)+1), 0o640); err != nil {
		t.Fatal(err)
	}
	if _, err := readProtectedFile(path, "credential"); err == nil {
		t.Fatal("oversized protected file was silently truncated")
	}
}

func TestRejectedSnapshotPreservesLastKnownGood(t *testing.T) {
	root := t.TempDir()
	publicKey, privateKey, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().UTC().Truncate(time.Second)
	validRaw, valid := signedSnapshot(t, privateKey, now, 1, 0, "snap_test_01")
	store, _ := NewStore(testConfig(root).Snapshots)
	if err := store.CommitObserved(validRaw, valid, newShadowResult("gw_test_01", valid, "shadow_validated", DiffSummary{})); err != nil {
		t.Fatal(err)
	}
	checkpoint, _ := store.LoadCheckpoint()
	if err := store.MarkResultReported(checkpoint, *checkpoint.PendingResult); err != nil {
		t.Fatal(err)
	}
	invalidRaw, invalid := signedSnapshot(t, privateKey, now, 2, 1, "snap_test_02")
	invalid.Signature.Value = base64.StdEncoding.EncodeToString(make([]byte, 64))
	invalidRaw, _ = json.Marshal(invalid)
	controller := &fakeController{raw: invalidRaw}
	agent := &Agent{Config: testConfig(root), Controller: controller, Store: store, WireGuard: WireGuardReader{Runner: &fakeRunner{}}, PublicKey: publicKey, Version: "0.1.0", Now: func() time.Time { return now }}
	agent.RunCycle(context.Background())
	checkpoint, _ = store.LoadCheckpoint()
	if checkpoint.ObservedGeneration != 1 || checkpoint.ObservedSnapshotID != "snap_test_01" {
		t.Fatalf("rejected snapshot replaced LKG: %+v", checkpoint)
	}
	agent.RunCycle(context.Background())
	if len(controller.results) != 1 {
		t.Fatalf("identical rejected snapshot was reported repeatedly: %d", len(controller.results))
	}
}

type noSnapshotController struct{}

func (*noSnapshotController) Heartbeat(context.Context, Heartbeat) error { return nil }
func (*noSnapshotController) PlannedSnapshot(context.Context, string) ([]byte, error) {
	return nil, ErrNoPlannedSnapshot
}
func (*noSnapshotController) ReportApplyResult(context.Context, ApplyResult) error { return nil }
