package cutover

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/gateway"
	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/staticmigration"
)

const (
	SchemaVersion        = 1
	BundleKind           = "xconnect.accounts-only-readiness-bundle"
	EvidenceKind         = "xconnect.accounts-only-readiness-evidence"
	ReadyExitCode        = 0
	RejectedExitCode     = 3
	InvalidInputExitCode = 2
)

var idPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{2,127}$`)

type Bundle struct {
	SchemaVersion      int                            `json:"schema_version"`
	Kind               string                         `json:"kind"`
	RequestedMode      string                         `json:"requested_mode"`
	NodeID             string                         `json:"node_id"`
	NetworkID          string                         `json:"network_id"`
	EvaluatedAt        time.Time                      `json:"evaluated_at"`
	SigningKeyID       string                         `json:"signing_key_id"`
	StaticImport       staticmigration.ImportDocument `json:"static_import"`
	ImportReceipt      ImportReceipt                  `json:"import_receipt"`
	AccountsProjection []ProjectionDevice             `json:"accounts_projection"`
	Snapshot           json.RawMessage                `json:"snapshot"`
	PolicyArtifact     json.RawMessage                `json:"policy_artifact"`
	Controller         ControllerEvidence             `json:"controller"`
	Gateway            GatewayEvidence                `json:"gateway"`
}

type ImportReceipt struct {
	ImportID       string    `json:"import_id"`
	IdempotencyKey string    `json:"idempotency_key"`
	OwnerUserID    string    `json:"owner_user_id"`
	NetworkID      string    `json:"network_id"`
	BaselineSHA256 string    `json:"baseline_sha256"`
	DeviceCount    int       `json:"device_count"`
	CreatedAt      time.Time `json:"created_at"`
}

type ProjectionDevice struct {
	DeviceID  string   `json:"device_id"`
	PublicKey string   `json:"public_key"`
	Addresses []string `json:"addresses"`
}

type ControllerEvidence struct {
	Authorization    CutoverAuthorization `json:"authorization"`
	PendingReconcile ReconcileEvidence    `json:"pending_reconcile"`
}

type CutoverAuthorization struct {
	SchemaVersion    int               `json:"schema_version"`
	Kind             string            `json:"kind"`
	RequestedMode    string            `json:"requested_mode"`
	NodeID           string            `json:"node_id"`
	NetworkID        string            `json:"network_id"`
	Generation       uint64            `json:"generation"`
	SnapshotID       string            `json:"snapshot_id"`
	BaselineSHA256   string            `json:"baseline_sha256"`
	ProjectionSHA256 string            `json:"projection_sha256"`
	PolicySHA256     string            `json:"policy_sha256"`
	Reconcile        ReconcileEvidence `json:"reconcile"`
	IssuedAt         time.Time         `json:"issued_at"`
	ExpiresAt        time.Time         `json:"expires_at"`
	Signature        gateway.Signature `json:"signature"`
}

func (authorization CutoverAuthorization) SigningBytes() ([]byte, error) {
	payload := struct {
		SchemaVersion    int               `json:"schema_version"`
		Kind             string            `json:"kind"`
		RequestedMode    string            `json:"requested_mode"`
		NodeID           string            `json:"node_id"`
		NetworkID        string            `json:"network_id"`
		Generation       uint64            `json:"generation"`
		SnapshotID       string            `json:"snapshot_id"`
		BaselineSHA256   string            `json:"baseline_sha256"`
		ProjectionSHA256 string            `json:"projection_sha256"`
		PolicySHA256     string            `json:"policy_sha256"`
		Reconcile        ReconcileEvidence `json:"reconcile"`
		IssuedAt         time.Time         `json:"issued_at"`
		ExpiresAt        time.Time         `json:"expires_at"`
	}{authorization.SchemaVersion, authorization.Kind, authorization.RequestedMode, authorization.NodeID, authorization.NetworkID, authorization.Generation, authorization.SnapshotID, authorization.BaselineSHA256, authorization.ProjectionSHA256, authorization.PolicySHA256, authorization.Reconcile, authorization.IssuedAt, authorization.ExpiresAt}
	return json.Marshal(payload)
}

type ReconcileEvidence struct {
	Processed int `json:"processed"`
	Completed int `json:"completed"`
	Failed    int `json:"failed"`
	Pending   int `json:"pending"`
}

type GatewayEvidence struct {
	Heartbeat       gateway.Heartbeat   `json:"heartbeat"`
	ApplyResult     gateway.ApplyResult `json:"apply_result"`
	Checkpoint      CheckpointEvidence  `json:"checkpoint"`
	RuntimeReadback RuntimeReadback     `json:"runtime_readback"`
	HealthSamples   []HealthSample      `json:"health_samples"`
}

type CheckpointEvidence struct {
	ObservedGeneration uint64 `json:"observed_generation"`
	AppliedGeneration  uint64 `json:"applied_generation"`
	ObservedSnapshotID string `json:"observed_snapshot_id"`
	PendingResult      bool   `json:"pending_result"`
	RuntimeFault       bool   `json:"runtime_fault"`
	RuntimeQuarantined bool   `json:"runtime_quarantined"`
}

type RuntimeReadback struct {
	SnapshotID       string              `json:"snapshot_id"`
	Generation       uint64              `json:"generation"`
	PolicySHA256     string              `json:"policy_sha256"`
	Diff             gateway.DiffSummary `json:"diff"`
	ProjectedDevices []ProjectionDevice  `json:"projected_devices"`
}

type HealthSample struct {
	SampledAt           time.Time `json:"sampled_at"`
	Status              string    `json:"status"`
	Mode                string    `json:"mode"`
	ProxyCore           string    `json:"proxy_core"`
	RuntimeApplyEnabled bool      `json:"runtime_apply_enabled"`
	ObservedGeneration  uint64    `json:"observed_generation"`
	AppliedGeneration   uint64    `json:"applied_generation"`
	ControllerStatus    string    `json:"controller_status"`
	DiffStatus          string    `json:"diff_status"`
	DiffEqual           bool      `json:"diff_equal"`
	RuntimeFault        bool      `json:"runtime_fault"`
}

type Check struct {
	Name   string `json:"name"`
	Status string `json:"status"`
}

type Evidence struct {
	SchemaVersion         int       `json:"schema_version"`
	Kind                  string    `json:"kind"`
	Decision              string    `json:"decision"`
	RequestedMode         string    `json:"requested_mode"`
	NodeID                string    `json:"node_id"`
	NetworkID             string    `json:"network_id"`
	SnapshotID            string    `json:"snapshot_id,omitempty"`
	Generation            uint64    `json:"generation,omitempty"`
	BaselineSHA256        string    `json:"baseline_sha256,omitempty"`
	PolicySHA256          string    `json:"policy_sha256,omitempty"`
	ProjectionSHA256      string    `json:"projection_sha256,omitempty"`
	HealthSampleCount     int       `json:"health_sample_count"`
	RequiredHealthSamples int       `json:"required_health_samples"`
	EvaluatedAt           time.Time `json:"evaluated_at"`
	Checks                []Check   `json:"checks"`
}

func DecodeBundle(raw []byte) (Bundle, error) {
	if len(raw) == 0 || len(raw) > 8<<20 {
		return Bundle{}, errors.New("readiness bundle size is invalid")
	}
	var bundle Bundle
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&bundle); err != nil {
		return Bundle{}, errors.New("decode readiness bundle")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return Bundle{}, errors.New("readiness bundle contains multiple JSON values")
	}
	return bundle, nil
}

func Evaluate(raw []byte, snapshotPublicKey, authorizationPublicKey ed25519.PublicKey, authorizationKeyID string, now time.Time, minimumHealthSamples int) (Evidence, error) {
	if len(snapshotPublicKey) != ed25519.PublicKeySize || len(authorizationPublicKey) != ed25519.PublicKeySize {
		return Evidence{}, errors.New("readiness signing public key is invalid")
	}
	bundle, err := DecodeBundle(raw)
	if err != nil {
		return Evidence{}, err
	}
	if minimumHealthSamples < 2 || minimumHealthSamples > 100 {
		return Evidence{}, errors.New("health sample threshold must be between 2 and 100")
	}
	evidence := Evidence{
		SchemaVersion: SchemaVersion, Kind: EvidenceKind, Decision: "rejected",
		RequestedMode: bundle.RequestedMode, NodeID: bundle.NodeID, NetworkID: bundle.NetworkID,
		HealthSampleCount: len(bundle.Gateway.HealthSamples), RequiredHealthSamples: minimumHealthSamples,
		EvaluatedAt: now.UTC().Truncate(time.Second),
	}
	failed := false
	check := func(name string, checkErr error) {
		status := "passed"
		if checkErr != nil {
			status = "failed"
			failed = true
		}
		evidence.Checks = append(evidence.Checks, Check{Name: name, Status: status})
	}

	check("bundle_contract", validateBundleContract(bundle, now))
	document, documentErr := staticmigration.ValidateImportDocument(bundle.StaticImport)
	check("static_import_baseline", documentErr)
	if documentErr == nil {
		evidence.BaselineSHA256 = document.Source.BaselineSHA256
	}
	check("import_receipt", validateReceipt(bundle, document))

	staticProjection, staticErr := projectionFromImport(document, bundle.NodeID)
	accountsProjection, accountsErr := validateProjection(bundle.AccountsProjection)
	check("accounts_projection_contract", accountsErr)
	check("static_accounts_projection_exact", firstError(staticErr, compareProjection(staticProjection, accountsProjection)))

	snapshot, snapshotErr := gateway.DecodeGatewaySnapshot(bundle.Snapshot)
	if snapshotErr == nil {
		snapshotErr = snapshot.ValidateAgainstGeneration(now, bundle.NodeID, bundle.SigningKeyID, snapshotPublicKey, snapshot.ExpectedPreviousGeneration)
	}
	check("signed_snapshot", snapshotErr)
	if snapshotErr == nil {
		evidence.SnapshotID, evidence.Generation = snapshot.SnapshotID, snapshot.Generation
		evidence.PolicySHA256 = snapshot.Policy.RulesetSHA256
	}
	policyErr := snapshotErr
	if policyErr == nil {
		_, policyErr = gateway.ValidatePolicyArtifact(bundle.PolicyArtifact, snapshot)
	}
	check("policy_digest", policyErr)

	snapshotProjection, snapshotProjectionErr := projectionFromSnapshot(snapshot)
	check("snapshot_projection_exact", firstError(snapshotErr, snapshotProjectionErr, compareProjection(accountsProjection, snapshotProjection)))
	projectionDigest, digestErr := projectionSHA256(accountsProjection)
	check("projection_digest", digestErr)
	if digestErr == nil {
		evidence.ProjectionSHA256 = projectionDigest
	}

	check("controller_apply_authorization", validateControllerAuthorization(bundle, document, snapshot, projectionDigest, authorizationPublicKey, authorizationKeyID, now))
	check("no_pending_reconcile", validateReconcile(bundle.Controller.PendingReconcile))
	check("gateway_checkpoint", validateCheckpoint(bundle.Gateway.Checkpoint, snapshot))
	check("gateway_heartbeat", validateHeartbeat(bundle.Gateway.Heartbeat, bundle.NodeID, snapshot.Generation))
	check("gateway_apply_result", validateApplyResult(bundle.Gateway.ApplyResult, bundle.NodeID, snapshot))
	check("runtime_readback_exact", validateRuntimeReadback(bundle.Gateway.RuntimeReadback, snapshot, accountsProjection))
	check("consecutive_health_samples", validateHealthSamples(bundle.Gateway.HealthSamples, snapshot.Generation, minimumHealthSamples))

	if failed {
		return evidence, errors.New("accounts-only readiness checks failed")
	}
	evidence.Decision = "ready"
	return evidence, nil
}

func validateBundleContract(bundle Bundle, now time.Time) error {
	if bundle.SchemaVersion != SchemaVersion || bundle.Kind != BundleKind || bundle.RequestedMode != "accounts-only" || !idPattern.MatchString(bundle.NodeID) || !idPattern.MatchString(bundle.NetworkID) || !idPattern.MatchString(bundle.SigningKeyID) {
		return errors.New("readiness bundle contract is invalid")
	}
	if !canonicalTime(bundle.EvaluatedAt) || bundle.EvaluatedAt.After(now.UTC()) || now.UTC().Sub(bundle.EvaluatedAt) > 5*time.Minute {
		return errors.New("bundle evaluated_at must be recent UTC whole-second evidence")
	}
	if len(bundle.Snapshot) == 0 || len(bundle.PolicyArtifact) == 0 {
		return errors.New("snapshot and policy artifact are required")
	}
	return nil
}

func validateReceipt(bundle Bundle, document staticmigration.ImportDocument) error {
	receipt := bundle.ImportReceipt
	canonical, err := json.Marshal(document)
	if err != nil {
		return err
	}
	if !idPattern.MatchString(receipt.ImportID) || receipt.IdempotencyKey != staticmigration.IdempotencyKey(canonical) || receipt.OwnerUserID != document.OwnerUserID || receipt.NetworkID != bundle.NetworkID || receipt.NetworkID != document.NetworkID || receipt.BaselineSHA256 != document.Source.BaselineSHA256 || receipt.DeviceCount != len(document.Devices) || !canonicalTime(receipt.CreatedAt) || receipt.CreatedAt.After(bundle.EvaluatedAt) {
		return errors.New("static import receipt does not bind the reviewed baseline")
	}
	return nil
}

func projectionFromImport(document staticmigration.ImportDocument, nodeID string) ([]ProjectionDevice, error) {
	devices := make([]ProjectionDevice, 0, len(document.Devices))
	for _, device := range document.Devices {
		for _, attachment := range device.Attachments {
			if attachment == nodeID {
				devices = append(devices, ProjectionDevice{DeviceID: device.DeviceID, PublicKey: device.WireGuardPublicKey, Addresses: append([]string(nil), device.Addresses...)})
				break
			}
		}
	}
	return validateProjection(devices)
}

func projectionFromSnapshot(snapshot gateway.GatewaySnapshot) ([]ProjectionDevice, error) {
	devices := make([]ProjectionDevice, 0, len(snapshot.WireGuard.Peers))
	for _, peer := range snapshot.WireGuard.Peers {
		devices = append(devices, ProjectionDevice{DeviceID: peer.DeviceID, PublicKey: peer.PublicKey, Addresses: append([]string(nil), peer.AllowedIPs...)})
	}
	return validateProjection(devices)
}

func validateProjection(devices []ProjectionDevice) ([]ProjectionDevice, error) {
	if len(devices) == 0 || len(devices) > 10000 {
		return nil, errors.New("projection must contain between 1 and 10000 devices")
	}
	result := append([]ProjectionDevice(nil), devices...)
	seenIDs, seenKeys, seenAddresses := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for index := range result {
		device := &result[index]
		if !idPattern.MatchString(device.DeviceID) || seenIDs[device.DeviceID] {
			return nil, errors.New("projection device identity is invalid or duplicated")
		}
		key, err := base64.StdEncoding.DecodeString(device.PublicKey)
		if err != nil || len(key) != 32 || seenKeys[device.PublicKey] {
			return nil, errors.New("projection public key is invalid or duplicated")
		}
		if len(device.Addresses) != 1 {
			return nil, errors.New("projection device requires one IPv4 /32")
		}
		prefix, err := netip.ParsePrefix(device.Addresses[0])
		if err != nil || !prefix.Addr().Is4() || prefix.Bits() != 32 || prefix.String() != device.Addresses[0] || seenAddresses[device.Addresses[0]] {
			return nil, errors.New("projection address is invalid or duplicated")
		}
		seenIDs[device.DeviceID], seenKeys[device.PublicKey], seenAddresses[device.Addresses[0]] = true, true, true
		device.Addresses = []string{prefix.String()}
	}
	sort.Slice(result, func(i, j int) bool { return result[i].DeviceID < result[j].DeviceID })
	for index := range result {
		if index >= len(devices) || devices[index].DeviceID != result[index].DeviceID {
			return nil, errors.New("projection devices are not in canonical order")
		}
	}
	return result, nil
}

func compareProjection(expected, actual []ProjectionDevice) error {
	if expected == nil || actual == nil || len(expected) != len(actual) {
		return errors.New("projection device count differs")
	}
	for index := range expected {
		if expected[index].DeviceID != actual[index].DeviceID || expected[index].PublicKey != actual[index].PublicKey || strings.Join(expected[index].Addresses, "\x00") != strings.Join(actual[index].Addresses, "\x00") {
			return errors.New("projection device, public key, or address differs")
		}
	}
	return nil
}

func projectionSHA256(projection []ProjectionDevice) (string, error) {
	raw, err := json.Marshal(projection)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:]), nil
}

func validateControllerAuthorization(bundle Bundle, document staticmigration.ImportDocument, snapshot gateway.GatewaySnapshot, projectionDigest string, publicKey ed25519.PublicKey, keyID string, now time.Time) error {
	authorization := bundle.Controller.Authorization
	if authorization.SchemaVersion != 1 || authorization.Kind != "xconnect.accounts-only-cutover-authorization" || authorization.RequestedMode != "accounts-only" || authorization.NodeID != bundle.NodeID || authorization.NetworkID != bundle.NetworkID || authorization.Generation != snapshot.Generation || authorization.SnapshotID != snapshot.SnapshotID || authorization.BaselineSHA256 != document.Source.BaselineSHA256 || authorization.ProjectionSHA256 != projectionDigest || authorization.PolicySHA256 != snapshot.Policy.RulesetSHA256 || authorization.Reconcile != bundle.Controller.PendingReconcile {
		return errors.New("Controller authorization does not bind the exact cutover evidence")
	}
	if !canonicalTime(authorization.IssuedAt) || !canonicalTime(authorization.ExpiresAt) || authorization.IssuedAt.After(now.UTC()) || !authorization.ExpiresAt.After(now.UTC()) || !authorization.ExpiresAt.After(authorization.IssuedAt) {
		return errors.New("Controller authorization validity window is invalid")
	}
	if authorization.Signature.Algorithm != "Ed25519" || authorization.Signature.KeyID != keyID || !idPattern.MatchString(keyID) {
		return errors.New("Controller authorization signing metadata is invalid")
	}
	signature, err := base64.StdEncoding.DecodeString(authorization.Signature.Value)
	if err != nil || len(signature) != ed25519.SignatureSize {
		return errors.New("Controller authorization signature encoding is invalid")
	}
	payload, err := authorization.SigningBytes()
	if err != nil || !ed25519.Verify(publicKey, payload, signature) {
		return errors.New("Controller authorization signature is invalid")
	}
	return nil
}

func validateReconcile(reconcile ReconcileEvidence) error {
	if reconcile.Processed < 0 || reconcile.Completed < 0 || reconcile.Failed < 0 || reconcile.Pending < 0 || reconcile.Processed != reconcile.Completed+reconcile.Failed || reconcile.Failed != 0 || reconcile.Pending != 0 {
		return errors.New("pending or failed control-plane reconcile work exists")
	}
	return nil
}

func validateCheckpoint(checkpoint CheckpointEvidence, snapshot gateway.GatewaySnapshot) error {
	if checkpoint.ObservedGeneration != snapshot.Generation || checkpoint.AppliedGeneration != snapshot.Generation || checkpoint.ObservedSnapshotID != snapshot.SnapshotID || checkpoint.PendingResult || checkpoint.RuntimeFault || checkpoint.RuntimeQuarantined {
		return errors.New("Gateway checkpoint is not cleanly applied at the signed generation")
	}
	return nil
}

func validateHeartbeat(heartbeat gateway.Heartbeat, nodeID string, generation uint64) error {
	if heartbeat.NodeID != nodeID || heartbeat.AgentVersion == "" || heartbeat.Mode != "apply" || heartbeat.ProxyCore != "xray" || heartbeat.ObservedGeneration != generation || heartbeat.AppliedGeneration != generation {
		return errors.New("Gateway heartbeat does not prove applied generation")
	}
	return nil
}

func validateApplyResult(result gateway.ApplyResult, nodeID string, snapshot gateway.GatewaySnapshot) error {
	if result.NodeID != nodeID || result.SnapshotID != snapshot.SnapshotID || result.ObservedGeneration != snapshot.Generation || result.AppliedGeneration != snapshot.Generation || !result.RuntimeApplied || result.Result != "applied" || !equalDiff(result.Diff, len(snapshot.WireGuard.Peers)) {
		return errors.New("Gateway apply result is not exact successful runtime evidence")
	}
	return nil
}

func validateRuntimeReadback(readback RuntimeReadback, snapshot gateway.GatewaySnapshot, projection []ProjectionDevice) error {
	readbackProjection, err := validateProjection(readback.ProjectedDevices)
	if err != nil || readback.SnapshotID != snapshot.SnapshotID || readback.Generation != snapshot.Generation || readback.PolicySHA256 != snapshot.Policy.RulesetSHA256 || !equalDiff(readback.Diff, len(snapshot.WireGuard.Peers)) || compareProjection(projection, readbackProjection) != nil {
		return errors.New("runtime readback does not equal signed projection and policy")
	}
	return nil
}

func validateHealthSamples(samples []HealthSample, generation uint64, minimum int) error {
	if len(samples) < minimum {
		return errors.New("insufficient consecutive healthy samples")
	}
	previous := time.Time{}
	for _, sample := range samples {
		if !canonicalTime(sample.SampledAt) || (!previous.IsZero() && !sample.SampledAt.After(previous)) || (sample.Status != "ok" && sample.Status != "ready") || sample.Mode != "apply" || sample.ProxyCore != "xray" || !sample.RuntimeApplyEnabled || sample.ObservedGeneration != generation || sample.AppliedGeneration != generation || sample.ControllerStatus != "ok" || sample.DiffStatus != "available" || !sample.DiffEqual || sample.RuntimeFault {
			return errors.New("health sample sequence is not continuously ready")
		}
		previous = sample.SampledAt
	}
	return nil
}

func equalDiff(diff gateway.DiffSummary, peers int) bool {
	return diff.Status == "available" && diff.Equal && diff.ProjectedPeers == peers && diff.CurrentPeers == peers && diff.MissingPeers == 0 && diff.UnexpectedPeers == 0 && diff.RouteMismatches == 0
}

func canonicalTime(value time.Time) bool {
	_, offset := value.Zone()
	return !value.IsZero() && offset == 0 && value.Nanosecond() == 0
}

func firstError(values ...error) error {
	for _, value := range values {
		if value != nil {
			return value
		}
	}
	return nil
}

func MarshalEvidence(evidence Evidence) ([]byte, error) {
	if evidence.SchemaVersion != SchemaVersion || evidence.Kind != EvidenceKind || (evidence.Decision != "ready" && evidence.Decision != "rejected") {
		return nil, fmt.Errorf("readiness evidence contract is invalid")
	}
	raw, err := json.MarshalIndent(evidence, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(raw, '\n'), nil
}
