package cutover

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/gateway"
	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/staticmigration"
)

func readinessTestNow() time.Time {
	return time.Date(2026, 8, 28, 12, 0, 0, 0, time.UTC)
}

func validBundle(t *testing.T) ([]byte, ed25519.PublicKey, ed25519.PublicKey, Bundle) {
	t.Helper()
	now := readinessTestNow()
	nodeID := "gw_test_01"
	key := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{2}, 32))
	document, err := staticmigration.BuildImportDocument("network-test", "11111111-1111-4111-8111-111111111111", []staticmigration.StaticClient{{DeviceID: "dev_test_01", Address: "10.77.0.10", PublicKey: key, Attachments: []string{nodeID}, Tags: []string{staticmigration.MigrationSourceTag}}})
	if err != nil {
		t.Fatal(err)
	}
	canonicalDocument, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	policy := gateway.ACLArtifact{SchemaVersion: 1, CompilerVersion: "xconnect-acl-v1alpha1.1", NetworkID: "network-test", Revision: 0, DefaultAction: "deny", ProtectedFlows: []string{"control:controller-session", "control:gateway-apply-result", "control:gateway-heartbeat", "control:gateway-policy-artifact", "control:gateway-snapshot"}, Rules: []gateway.ACLRule{}}
	policyRaw, err := json.Marshal(policy)
	if err != nil {
		t.Fatal(err)
	}
	policyDigest := sha256.Sum256(policyRaw)
	seed := sha256.Sum256([]byte("xconnect-cutover-readiness-test-key"))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	publicKey := privateKey.Public().(ed25519.PublicKey)
	authorizationSeed := sha256.Sum256([]byte("xconnect-cutover-authorization-test-key"))
	authorizationPrivateKey := ed25519.NewKeyFromSeed(authorizationSeed[:])
	authorizationPublicKey := authorizationPrivateKey.Public().(ed25519.PublicKey)
	snapshot := gateway.GatewaySnapshot{
		SchemaVersion: 1, SnapshotID: "snapshot_test_01", NodeID: nodeID, Generation: 1, ExpectedPreviousGeneration: 0,
		IssuedAt: now.Add(-time.Minute), ExpiresAt: now.Add(time.Hour), ProxyCore: "xray",
		Safety:    gateway.GatewaySafety{AllowEmptyPeers: false, MaxPeerRemovalPercent: 100},
		WireGuard: gateway.GatewayWireGuard{InterfaceName: "wg-xco", ListenPort: 51820, Addresses: []string{"10.77.0.1/32"}, Peers: []gateway.GatewayPeer{{DeviceID: "dev_test_01", PublicKey: key, AllowedIPs: []string{"10.77.0.10/32"}, PersistentKeepaliveSeconds: 25}}},
		Relay:     gateway.GatewayRelay{Transport: "vless-tls-xudp", ListenHost: "0.0.0.0", ListenPort: 443, ServerNames: []string{"gateway.example"}, CredentialRefs: []string{"relay_credential_" + nodeID}},
		Policy:    gateway.GatewayPolicy{Generation: 1, Backend: "nftables", RulesetSHA256: hex.EncodeToString(policyDigest[:])},
		Signature: gateway.Signature{Algorithm: "Ed25519", KeyID: "signing_key_01"},
	}
	signingBytes, err := snapshot.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	snapshot.Signature.Value = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, signingBytes))
	snapshotRaw, err := json.Marshal(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	projection := []ProjectionDevice{{DeviceID: "dev_test_01", PublicKey: key, Addresses: []string{"10.77.0.10/32"}}}
	projectionDigest, err := projectionSHA256(projection)
	if err != nil {
		t.Fatal(err)
	}
	diff := gateway.DiffSummary{Status: "available", Equal: true, ProjectedPeers: 1, CurrentPeers: 1}
	reconcile := ReconcileEvidence{Processed: 2, Completed: 2}
	authorization := CutoverAuthorization{
		SchemaVersion: 1, Kind: "xconnect.accounts-only-cutover-authorization", RequestedMode: "accounts-only",
		NodeID: nodeID, NetworkID: document.NetworkID, Generation: snapshot.Generation, SnapshotID: snapshot.SnapshotID,
		BaselineSHA256: document.Source.BaselineSHA256, ProjectionSHA256: projectionDigest, PolicySHA256: snapshot.Policy.RulesetSHA256,
		Reconcile: reconcile, IssuedAt: now.Add(-time.Minute), ExpiresAt: now.Add(10 * time.Minute),
		Signature: gateway.Signature{Algorithm: "Ed25519", KeyID: "cutover_key_01"},
	}
	authorizationPayload, err := authorization.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	authorization.Signature.Value = base64.StdEncoding.EncodeToString(ed25519.Sign(authorizationPrivateKey, authorizationPayload))
	bundle := Bundle{
		SchemaVersion: 1, Kind: BundleKind, RequestedMode: "accounts-only", NodeID: nodeID, NetworkID: "network-test", EvaluatedAt: now, SigningKeyID: "signing_key_01",
		StaticImport:       document,
		ImportReceipt:      ImportReceipt{ImportID: "import_test_01", IdempotencyKey: staticmigration.IdempotencyKey(canonicalDocument), OwnerUserID: document.OwnerUserID, NetworkID: document.NetworkID, BaselineSHA256: document.Source.BaselineSHA256, DeviceCount: 1, CreatedAt: now.Add(-time.Hour)},
		AccountsProjection: projection, Snapshot: snapshotRaw, PolicyArtifact: policyRaw,
		Controller: ControllerEvidence{Authorization: authorization, PendingReconcile: reconcile},
		Gateway: GatewayEvidence{
			Heartbeat:       gateway.Heartbeat{NodeID: nodeID, AgentVersion: "0.1.0", Mode: "apply", ProxyCore: "xray", ObservedGeneration: 1, AppliedGeneration: 1},
			ApplyResult:     gateway.ApplyResult{NodeID: nodeID, SnapshotID: snapshot.SnapshotID, ObservedGeneration: 1, AppliedGeneration: 1, RuntimeApplied: true, Result: "applied", Diff: diff},
			Checkpoint:      CheckpointEvidence{ObservedGeneration: 1, AppliedGeneration: 1, ObservedSnapshotID: snapshot.SnapshotID},
			RuntimeReadback: RuntimeReadback{SnapshotID: snapshot.SnapshotID, Generation: 1, PolicySHA256: snapshot.Policy.RulesetSHA256, Diff: diff, ProjectedDevices: projection},
			HealthSamples: []HealthSample{
				readySample(now.Add(-3*time.Minute), 1),
				readySample(now.Add(-2*time.Minute), 1),
				readySample(now.Add(-time.Minute), 1),
			},
		},
	}
	raw, err := json.Marshal(bundle)
	if err != nil {
		t.Fatal(err)
	}
	return raw, publicKey, authorizationPublicKey, bundle
}

func readySample(at time.Time, generation uint64) HealthSample {
	return HealthSample{SampledAt: at, Status: "ready", Mode: "apply", ProxyCore: "xray", RuntimeApplyEnabled: true, ObservedGeneration: generation, AppliedGeneration: generation, ControllerStatus: "ok", DiffStatus: "available", DiffEqual: true}
}

func TestReadinessRequiresEveryGate(t *testing.T) {
	raw, publicKey, authorizationPublicKey, _ := validBundle(t)
	evidence, err := Evaluate(raw, publicKey, authorizationPublicKey, "cutover_key_01", readinessTestNow(), 3)
	if err != nil || evidence.Decision != "ready" || len(evidence.Checks) != 16 {
		t.Fatalf("valid readiness bundle rejected: evidence=%+v err=%v", evidence, err)
	}
	for _, check := range evidence.Checks {
		if check.Status != "passed" {
			t.Fatalf("unexpected failed check: %+v", check)
		}
	}
}

func TestCutoverAuthorizationSigningBytesGolden(t *testing.T) {
	_, _, _, bundle := validBundle(t)
	raw, err := bundle.Controller.Authorization.SigningBytes()
	if err != nil {
		t.Fatal(err)
	}
	want := `{"schema_version":1,"kind":"xconnect.accounts-only-cutover-authorization","requested_mode":"accounts-only","node_id":"gw_test_01","network_id":"network-test","generation":1,"snapshot_id":"snapshot_test_01","baseline_sha256":"428299362e559ca17adeab49cfa137e8c20c8b28e5c5ad488b28f0b5aeba2794","projection_sha256":"3efc8c92d20f2cf18b93980119c2cd82b67d7d32ef655e64ba55a006d8eca32d","policy_sha256":"966ea0c4becfa9c3ed9f5120aedfed0cad64f220423acb33a16137af6c525075","reconcile":{"processed":2,"completed":2,"failed":0,"pending":0},"issued_at":"2026-08-28T11:59:00Z","expires_at":"2026-08-28T12:10:00Z"}`
	if string(raw) != want {
		t.Fatalf("cutover authorization signing bytes drifted:\n%s", raw)
	}
}

func TestReadinessFailClosedMatrix(t *testing.T) {
	validRaw, publicKey, authorizationPublicKey, _ := validBundle(t)
	tests := []struct {
		name   string
		mutate func(*Bundle)
	}{
		{"baseline receipt mismatch", func(bundle *Bundle) { bundle.ImportReceipt.BaselineSHA256 = strings.Repeat("0", 64) }},
		{"projection public key drift", func(bundle *Bundle) {
			bundle.AccountsProjection[0].PublicKey = base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{3}, 32))
		}},
		{"bad snapshot signature", func(bundle *Bundle) {
			var snapshot gateway.GatewaySnapshot
			_ = json.Unmarshal(bundle.Snapshot, &snapshot)
			snapshot.Signature.Value = base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{9}, ed25519.SignatureSize))
			bundle.Snapshot, _ = json.Marshal(snapshot)
		}},
		{"policy digest mismatch", func(bundle *Bundle) {
			var artifact gateway.ACLArtifact
			_ = json.Unmarshal(bundle.PolicyArtifact, &artifact)
			artifact.NetworkID = "network-tampered"
			bundle.PolicyArtifact, _ = json.Marshal(artifact)
		}},
		{"generation authorization mismatch", func(bundle *Bundle) { bundle.Controller.Authorization.Generation = 2 }},
		{"authorization mode tamper", func(bundle *Bundle) { bundle.Controller.Authorization.RequestedMode = "shadow" }},
		{"authorization projection tamper", func(bundle *Bundle) { bundle.Controller.Authorization.ProjectionSHA256 = strings.Repeat("0", 64) }},
		{"authorization signature tamper", func(bundle *Bundle) {
			bundle.Controller.Authorization.Signature.Value = base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{9}, ed25519.SignatureSize))
		}},
		{"expired authorization", func(bundle *Bundle) { bundle.Controller.Authorization.ExpiresAt = readinessTestNow().Add(-time.Second) }},
		{"backdated local evaluation", func(bundle *Bundle) { bundle.EvaluatedAt = readinessTestNow().Add(-6 * time.Minute) }},
		{"pending reconcile", func(bundle *Bundle) { bundle.Controller.PendingReconcile.Pending = 1 }},
		{"pending apply result", func(bundle *Bundle) { bundle.Gateway.Checkpoint.PendingResult = true }},
		{"runtime rollback fault", func(bundle *Bundle) { bundle.Gateway.Checkpoint.RuntimeFault = true }},
		{"runtime quarantine", func(bundle *Bundle) {
			bundle.Gateway.Checkpoint.RuntimeFault, bundle.Gateway.Checkpoint.RuntimeQuarantined = true, true
		}},
		{"runtime readback drift", func(bundle *Bundle) { bundle.Gateway.RuntimeReadback.Diff.Equal = false }},
		{"health threshold", func(bundle *Bundle) { bundle.Gateway.HealthSamples = bundle.Gateway.HealthSamples[:2] }},
		{"unhealthy sample", func(bundle *Bundle) { bundle.Gateway.HealthSamples[1].Status = "fail-closed" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			bundle, err := DecodeBundle(validRaw)
			if err != nil {
				t.Fatal(err)
			}
			test.mutate(&bundle)
			raw, err := json.Marshal(bundle)
			if err != nil {
				t.Fatal(err)
			}
			evidence, err := Evaluate(raw, publicKey, authorizationPublicKey, "cutover_key_01", readinessTestNow(), 3)
			if err == nil || evidence.Decision != "rejected" {
				t.Fatalf("unsafe bundle accepted: %+v", evidence)
			}
		})
	}
}

func TestReadinessRejectsInvalidVerificationKeys(t *testing.T) {
	raw, publicKey, authorizationPublicKey, _ := validBundle(t)
	for name, keys := range map[string][2]ed25519.PublicKey{
		"snapshot":      {ed25519.PublicKey("short"), authorizationPublicKey},
		"authorization": {publicKey, ed25519.PublicKey("short")},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := Evaluate(raw, keys[0], keys[1], "cutover_key_01", readinessTestNow(), 3); err == nil {
				t.Fatal("invalid verification key was accepted")
			}
		})
	}
}

func TestEmptyPeerSnapshotCannotCutOver(t *testing.T) {
	_, publicKey, authorizationPublicKey, bundle := validBundle(t)
	var snapshot gateway.GatewaySnapshot
	if err := json.Unmarshal(bundle.Snapshot, &snapshot); err != nil {
		t.Fatal(err)
	}
	snapshot.WireGuard.Peers = nil
	snapshot.Safety.AllowEmptyPeers = true
	// The stale signature is intentionally invalid too; readiness must reject
	// before an empty Accounts projection could become authoritative.
	bundle.Snapshot, _ = json.Marshal(snapshot)
	bundle.AccountsProjection = nil
	raw, _ := json.Marshal(bundle)
	if evidence, err := Evaluate(raw, publicKey, authorizationPublicKey, "cutover_key_01", readinessTestNow(), 3); err == nil || evidence.Decision != "rejected" {
		t.Fatal("empty peer cutover was accepted")
	}
}

func TestMockHTTPSControlPlaneReadinessHarness(t *testing.T) {
	raw, publicKey, authorizationPublicKey, _ := validBundle(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/test-only/accounts-only-readiness" || request.Header.Get("Authorization") != "Bearer xgn_test_redacted" {
			http.Error(response, "unauthorized", http.StatusUnauthorized)
			return
		}
		response.Header().Set("Content-Type", "application/vnd.xconnect.accounts-only-readiness.v1+json")
		response.Header().Set("Cache-Control", "no-store")
		response.Header().Set("Vary", "Authorization")
		_, _ = response.Write(raw)
	}))
	defer server.Close()
	request, _ := http.NewRequest(http.MethodGet, server.URL+"/test-only/accounts-only-readiness", nil)
	request.Header.Set("Authorization", "Bearer xgn_test_redacted")
	response, err := server.Client().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.Header.Get("Cache-Control") != "no-store" || response.Header.Get("Vary") != "Authorization" {
		t.Fatal("mock Controller did not preserve protected response headers")
	}
	fetched, err := io.ReadAll(io.LimitReader(response.Body, 8<<20+1))
	if err != nil {
		t.Fatal(err)
	}
	evidence, err := Evaluate(fetched, publicKey, authorizationPublicKey, "cutover_key_01", readinessTestNow(), 3)
	if err != nil || evidence.Decision != "ready" {
		t.Fatalf("mock HTTPS integration readiness rejected: %+v %v", evidence, err)
	}
}

func FuzzReadinessNeverPanics(f *testing.F) {
	seed := sha256.Sum256([]byte("xconnect-cutover-readiness-test-key"))
	publicKey := ed25519.NewKeyFromSeed(seed[:]).Public().(ed25519.PublicKey)
	authorizationSeed := sha256.Sum256([]byte("xconnect-cutover-authorization-test-key"))
	authorizationPublicKey := ed25519.NewKeyFromSeed(authorizationSeed[:]).Public().(ed25519.PublicKey)
	f.Add([]byte(`{"schema_version":1,"kind":"xconnect.accounts-only-readiness-bundle"}`))
	f.Add([]byte(`{"schema_version":1}`))
	f.Fuzz(func(t *testing.T, input []byte) {
		_, _ = Evaluate(input, publicKey, authorizationPublicKey, "cutover_key_01", readinessTestNow(), 3)
	})
}
