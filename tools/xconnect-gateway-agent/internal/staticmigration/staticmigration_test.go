package staticmigration

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/gateway"
)

func fixturePath(name string) string {
	return filepath.Join("..", "..", "..", "..", "tests", "fixtures", "xconnect-static-import", name)
}

func TestImportDocumentGoldenAndSecretRejection(t *testing.T) {
	clients, err := ParseGroupVarsFile(fixturePath("group-vars.yml"))
	if err != nil {
		t.Fatal(err)
	}
	if len(clients) != 2 || clients[0].DeviceID != "device-alpha" || strings.Contains(strings.Join(clients[0].Tags, ","), "VAULT") {
		t.Fatalf("unexpected normalized clients: %+v", clients)
	}
	document, err := BuildImportDocument("network-private", "11111111-1111-4111-8111-111111111111", clients)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := MarshalDocument(document)
	if err != nil {
		t.Fatal(err)
	}
	want, err := os.ReadFile(fixturePath("import.golden.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(raw, want) {
		t.Fatalf("import golden drifted\ngot:\n%s\nwant:\n%s", raw, want)
	}
	if _, err := ParseGroupVarsFile(fixturePath("group-vars-secret.yml")); err == nil {
		t.Fatal("private key in static client was accepted")
	}
	if _, err := BuildImportDocument("network-private", "not-a-uuid", clients); err == nil {
		t.Fatal("invalid owner user UUID accepted")
	}
}

func TestCanonicalImportRejectsTampering(t *testing.T) {
	raw, err := os.ReadFile(fixturePath("import.golden.json"))
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := CanonicalDocumentBytes(raw)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(canonical, []byte("\n")) || canonical[len(canonical)-1] == '\n' {
		t.Fatal("canonical request bytes contain formatting whitespace")
	}
	wantCanonical, _ := os.ReadFile(fixturePath("import.canonical.golden.json"))
	wantIdempotencyKey, _ := os.ReadFile(fixturePath("idempotency-key.golden.txt"))
	if !bytes.Equal(canonical, bytes.TrimSpace(wantCanonical)) || IdempotencyKey(canonical) != strings.TrimSpace(string(wantIdempotencyKey)) {
		t.Fatalf("canonical import wire vector drifted: key=%s body=%s", IdempotencyKey(canonical), canonical)
	}
	mutate := func(change func(*ImportDocument)) []byte {
		var document ImportDocument
		if err := json.Unmarshal(raw, &document); err != nil {
			t.Fatal(err)
		}
		change(&document)
		encoded, _ := json.Marshal(document)
		return encoded
	}
	tests := map[string][]byte{
		"baseline":         mutate(func(document *ImportDocument) { document.Source.BaselineSHA256 = strings.Repeat("0", 64) }),
		"duplicate device": mutate(func(document *ImportDocument) { document.Devices = append(document.Devices, document.Devices[0]) }),
		"secret tag": mutate(func(document *ImportDocument) {
			document.Devices[0].Tags = append(document.Devices[0].Tags, "token:plaintext")
		}),
		"duplicate attachment": mutate(func(document *ImportDocument) {
			document.Devices[0].Attachments = append(document.Devices[0].Attachments, document.Devices[0].Attachments[0])
		}),
		"non host address": mutate(func(document *ImportDocument) { document.Devices[0].Addresses = []string{"10.77.0.0/24"} }),
		"invalid key":      mutate(func(document *ImportDocument) { document.Devices[0].WireGuardPublicKey = "invalid" }),
		"unknown field":    append(bytes.TrimSpace(raw), []byte(" trailing")...),
	}
	for name, candidate := range tests {
		t.Run(name, func(t *testing.T) {
			if _, err := CanonicalDocumentBytes(candidate); err == nil {
				t.Fatal("tampered import document accepted")
			}
		})
	}
}

func TestParserRejectsAmbiguousOrUnsafeInput(t *testing.T) {
	validClient := "id: device-test\n    wg_ip: 10.77.0.10\n    public_key: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n    attach_to: [gateway-a]\n"
	topology := "xworkmate_bridge_distributed_vpn_nodes:\n  gateway-a: {}\n"
	tests := map[string]string{
		"alias":      topology + "client: &client\n  " + validClient + SourceVariable + ": [*client]\n",
		"unknown":    topology + SourceVariable + ":\n  - " + validClient + "    endpoint: hidden\n",
		"credential": topology + SourceVariable + ":\n  - " + validClient + "    auth_id: plaintext\n",
		"secret tag": topology + SourceVariable + ":\n  - " + validClient + "    tags: [token:plaintext]\n",
		"duplicate":  topology + SourceVariable + ":\n  - " + validClient + "  - " + validClient,
	}
	for name, raw := range tests {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseGroupVars([]byte(raw)); err == nil {
				t.Fatal("unsafe YAML accepted")
			}
		})
	}
}

func TestParserExpandsRoleDefaultAttachments(t *testing.T) {
	raw := []byte(`
xworkmate_bridge_distributed_vpn_nodes:
  gateway-b: {}
  gateway-a: {}
xworkmate_bridge_distributed_vpn_clients:
  - id: device-default
    wg_ip: 10.77.0.20
    public_key: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
`)
	clients, err := ParseGroupVars(raw)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(clients[0].Attachments, ",") != "gateway-a,gateway-b" {
		t.Fatalf("role default attachments were not expanded: %+v", clients[0].Attachments)
	}
}

func TestStaticSnapshotDiffGoldenAndCategories(t *testing.T) {
	clients, _ := ParseGroupVarsFile(fixturePath("group-vars.yml"))
	raw, _ := os.ReadFile(fixturePath("gateway-snapshot.json"))
	snapshot, err := gateway.DecodeGatewaySnapshot(raw)
	if err != nil {
		t.Fatal(err)
	}
	evidence, err := CompareSnapshot(clients, "gateway-a", snapshot)
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.MarshalIndent(evidence, "", "  ")
	encoded = append(encoded, '\n')
	want, _ := os.ReadFile(fixturePath("diff-equal.golden.json"))
	if !bytes.Equal(encoded, want) {
		t.Fatalf("diff golden drifted\ngot:\n%s\nwant:\n%s", encoded, want)
	}

	snapshot.WireGuard.Peers[0].PublicKey = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
	snapshot.WireGuard.Peers[0].AllowedIPs = []string{"10.77.0.99/32"}
	snapshot.WireGuard.Peers = append(snapshot.WireGuard.Peers, gateway.GatewayPeer{DeviceID: "device-extra", PublicKey: "DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=", AllowedIPs: []string{"10.77.0.12/32"}})
	evidence, err = CompareSnapshot(clients, "gateway-a", snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if evidence.Status != "drift" || len(evidence.PublicKeyMismatches) != 1 || len(evidence.AllowedIPMismatches) != 1 || len(evidence.UnexpectedDevices) != 1 {
		t.Fatalf("diff categories incomplete: %+v", evidence)
	}
	marshaled, _ := json.Marshal(evidence)
	if bytes.Contains(marshaled, []byte(snapshot.WireGuard.Peers[0].PublicKey)) {
		t.Fatal("diff evidence leaked a public key instead of a fingerprint")
	}
	snapshot.ProxyCore = "unsupported"
	if _, err := CompareSnapshot(clients, "gateway-a", snapshot); err == nil {
		t.Fatal("non-Xray GatewaySnapshot accepted")
	}
}

func TestImportClientHTTPSIdempotencyAndRedaction(t *testing.T) {
	root := t.TempDir()
	serviceTokenFile := filepath.Join(root, "service.token")
	if err := os.WriteFile(serviceTokenFile, []byte("short-lived-service-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	document, _ := os.ReadFile(fixturePath("import.golden.json"))
	canonicalDocument, err := CanonicalDocumentBytes(document)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	keys := []string{}
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		body, _ := io.ReadAll(request.Body)
		if request.URL.Path != ImportEndpoint || request.Header.Get("X-Service-Token") != "short-lived-service-token" || request.Header.Get("Authorization") != "" || request.Header.Get("Content-Type") != ImportMediaType || !bytes.Equal(body, canonicalDocument) {
			t.Error("unexpected import request contract")
		}
		mu.Lock()
		keys = append(keys, request.Header.Get("Idempotency-Key"))
		mu.Unlock()
		response.WriteHeader(http.StatusAccepted)
	}))
	defer server.Close()
	client, err := NewImportClient(server.URL, serviceTokenFile, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		receipt, err := client.Apply(context.Background(), document)
		if err != nil || !receipt.Applied || receipt.StatusCode != http.StatusAccepted {
			t.Fatalf("apply failed: receipt=%+v err=%v", receipt, err)
		}
	}
	if len(keys) != 2 || keys[0] == "" || keys[0] != keys[1] || keys[0] != IdempotencyKey(canonicalDocument) {
		t.Fatalf("idempotency key was unstable: %v", keys)
	}

	secretBody := "response-must-not-leak"
	unauthorized := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		http.Error(response, secretBody, http.StatusUnauthorized)
	}))
	defer unauthorized.Close()
	client, _ = NewImportClient(unauthorized.URL, serviceTokenFile, unauthorized.Client())
	_, err = client.Apply(context.Background(), document)
	if err == nil || strings.Contains(err.Error(), "short-lived-service-token") || strings.Contains(err.Error(), secretBody) {
		t.Fatalf("import failure leaked a secret: %v", err)
	}
	if _, err := NewImportClient("http://controller.example", serviceTokenFile, nil); err == nil {
		t.Fatal("non-HTTPS apply endpoint accepted")
	}
	if err := os.Chmod(serviceTokenFile, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := NewImportClient(server.URL, serviceTokenFile, server.Client()); err == nil {
		t.Fatal("world-readable credential accepted")
	}
}

func TestImportClientDoesNotWrapSensitiveErrors(t *testing.T) {
	_, err := NewImportClient("https://controller.example", "/missing/credential", nil)
	if err == nil || errors.Is(err, os.ErrNotExist) {
		t.Fatal("credential error should be present but sanitized")
	}
}
