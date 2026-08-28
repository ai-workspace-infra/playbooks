package gateway

import (
	"bytes"
	"crypto/ed25519"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"regexp"
	"strings"
	"time"
)

var (
	idPattern        = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{2,127}$`)
	interfacePattern = regexp.MustCompile(`^[a-zA-Z0-9_=+.-]{1,15}$`)
)

type Signature struct {
	Algorithm string `json:"algorithm"`
	KeyID     string `json:"key_id"`
	Value     string `json:"value"`
}

type GatewaySnapshot struct {
	SchemaVersion              int              `json:"schema_version"`
	SnapshotID                 string           `json:"snapshot_id"`
	NodeID                     string           `json:"node_id"`
	Generation                 uint64           `json:"generation"`
	ExpectedPreviousGeneration uint64           `json:"expected_previous_generation"`
	IssuedAt                   time.Time        `json:"issued_at"`
	ExpiresAt                  time.Time        `json:"expires_at"`
	ProxyCore                  string           `json:"proxy_core"`
	Safety                     GatewaySafety    `json:"safety"`
	WireGuard                  GatewayWireGuard `json:"wireguard"`
	Relay                      GatewayRelay     `json:"relay"`
	Policy                     GatewayPolicy    `json:"policy"`
	Signature                  Signature        `json:"signature"`
}

type GatewaySafety struct {
	AllowEmptyPeers       bool    `json:"allow_empty_peers"`
	MaxPeerRemovalPercent float64 `json:"max_peer_removal_percent"`
}

type GatewayWireGuard struct {
	InterfaceName string        `json:"interface_name"`
	ListenPort    int           `json:"listen_port"`
	Addresses     []string      `json:"addresses"`
	Peers         []GatewayPeer `json:"peers"`
}

type GatewayPeer struct {
	DeviceID                   string   `json:"device_id"`
	PublicKey                  string   `json:"public_key"`
	AllowedIPs                 []string `json:"allowed_ips"`
	PersistentKeepaliveSeconds int      `json:"persistent_keepalive_seconds,omitempty"`
}

type GatewayRelay struct {
	Transport      string   `json:"transport"`
	ListenHost     string   `json:"listen_host"`
	ListenPort     int      `json:"listen_port"`
	ServerNames    []string `json:"server_names"`
	CredentialRefs []string `json:"credential_refs"`
}

type GatewayPolicy struct {
	Generation    uint64 `json:"generation"`
	Backend       string `json:"backend"`
	RulesetSHA256 string `json:"ruleset_sha256"`
}

func DecodeGatewaySnapshot(raw []byte) (GatewaySnapshot, error) {
	if err := rejectSecretFields(raw); err != nil {
		return GatewaySnapshot{}, err
	}
	var snapshot GatewaySnapshot
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&snapshot); err != nil {
		return GatewaySnapshot{}, fmt.Errorf("decode gateway snapshot: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return GatewaySnapshot{}, errors.New("decode gateway snapshot: multiple JSON values")
	}
	return snapshot, nil
}

func (s GatewaySnapshot) SigningBytes() ([]byte, error) {
	payload := struct {
		SchemaVersion              int              `json:"schema_version"`
		SnapshotID                 string           `json:"snapshot_id"`
		NodeID                     string           `json:"node_id"`
		Generation                 uint64           `json:"generation"`
		ExpectedPreviousGeneration uint64           `json:"expected_previous_generation"`
		IssuedAt                   time.Time        `json:"issued_at"`
		ExpiresAt                  time.Time        `json:"expires_at"`
		ProxyCore                  string           `json:"proxy_core"`
		Safety                     GatewaySafety    `json:"safety"`
		WireGuard                  GatewayWireGuard `json:"wireguard"`
		Relay                      GatewayRelay     `json:"relay"`
		Policy                     GatewayPolicy    `json:"policy"`
	}{s.SchemaVersion, s.SnapshotID, s.NodeID, s.Generation, s.ExpectedPreviousGeneration, s.IssuedAt, s.ExpiresAt, s.ProxyCore, s.Safety, s.WireGuard, s.Relay, s.Policy}
	return json.Marshal(payload)
}

func (s GatewaySnapshot) Validate(now time.Time, nodeID, keyID string, publicKey ed25519.PublicKey, previous *GatewaySnapshot) error {
	if s.SchemaVersion != 1 || s.ProxyCore != "xray" {
		return errors.New("snapshot must be schema v1 using Xray")
	}
	if !idPattern.MatchString(s.SnapshotID) || !idPattern.MatchString(s.NodeID) || s.NodeID != nodeID {
		return errors.New("snapshot or node identity is invalid")
	}
	if s.Generation == 0 || s.Generation <= s.ExpectedPreviousGeneration {
		return errors.New("generation must advance expected_previous_generation")
	}
	if !s.ExpiresAt.After(s.IssuedAt) || !s.ExpiresAt.After(now.UTC()) {
		return errors.New("snapshot is expired or has an invalid validity window")
	}
	if !isCanonicalContractTime(s.IssuedAt) || !isCanonicalContractTime(s.ExpiresAt) {
		return errors.New("snapshot times must be UTC with whole-second precision")
	}
	if s.IssuedAt.After(now.UTC().Add(5 * time.Minute)) {
		return errors.New("snapshot issued_at is too far in the future")
	}
	sameAsPrevious := false
	if previous == nil {
		if s.ExpectedPreviousGeneration != 0 {
			return errors.New("first snapshot must expect generation zero")
		}
	} else {
		sameAsPrevious = s.Generation == previous.Generation && s.SnapshotID == previous.SnapshotID
		if !sameAsPrevious && (s.ExpectedPreviousGeneration != previous.Generation || s.Generation <= previous.Generation || s.SnapshotID == previous.SnapshotID) {
			return errors.New("snapshot generation transition is stale or replayed")
		}
		if !sameAsPrevious {
			if err := s.validateRemoval(previous); err != nil {
				return err
			}
		}
	}
	if sameAsPrevious && s.ExpectedPreviousGeneration != previous.ExpectedPreviousGeneration {
		return errors.New("observed snapshot identity was reused with different transition metadata")
	}
	if s.Safety.MaxPeerRemovalPercent < 0 || s.Safety.MaxPeerRemovalPercent > 100 {
		return errors.New("peer removal safety percentage is invalid")
	}
	if len(s.WireGuard.Peers) == 0 && !s.Safety.AllowEmptyPeers {
		return errors.New("empty peers require an explicit safety override")
	}
	if !interfacePattern.MatchString(s.WireGuard.InterfaceName) || s.WireGuard.ListenPort < 1 || s.WireGuard.ListenPort > 65535 {
		return errors.New("WireGuard interface or port is invalid")
	}
	if err := validatePrefixes(s.WireGuard.Addresses); err != nil {
		return fmt.Errorf("WireGuard address: %w", err)
	}
	seen := make(map[string]bool, len(s.WireGuard.Peers))
	for _, peer := range s.WireGuard.Peers {
		if !idPattern.MatchString(peer.DeviceID) || seen[peer.DeviceID] {
			return errors.New("peer device identity is invalid or duplicated")
		}
		seen[peer.DeviceID] = true
		key, err := base64.StdEncoding.DecodeString(peer.PublicKey)
		if err != nil || len(key) != 32 {
			return errors.New("peer public key must encode 32 bytes")
		}
		if err := validatePrefixes(peer.AllowedIPs); err != nil {
			return fmt.Errorf("peer allowed IP: %w", err)
		}
		if peer.PersistentKeepaliveSeconds < 0 || peer.PersistentKeepaliveSeconds > 65535 {
			return errors.New("peer persistent keepalive is invalid")
		}
	}
	if s.Relay.Transport != "vless-tls-xudp" || !validHost(s.Relay.ListenHost) || s.Relay.ListenPort < 1 || s.Relay.ListenPort > 65535 {
		return errors.New("relay contract is invalid")
	}
	if err := validateUniqueNonempty("relay server names", s.Relay.ServerNames, validHost); err != nil {
		return err
	}
	if err := validateUniqueNonempty("relay credential references", s.Relay.CredentialRefs, idPattern.MatchString); err != nil {
		return err
	}
	if s.Policy.Generation == 0 || s.Policy.Backend != "nftables" {
		return errors.New("relay or policy backend contract is invalid")
	}
	if digest, err := hex.DecodeString(s.Policy.RulesetSHA256); err != nil || len(digest) != 32 || s.Policy.RulesetSHA256 != strings.ToLower(s.Policy.RulesetSHA256) {
		return errors.New("policy digest is invalid")
	}
	if s.Signature.Algorithm != "Ed25519" || !idPattern.MatchString(s.Signature.KeyID) || !idPattern.MatchString(keyID) || subtle.ConstantTimeCompare([]byte(s.Signature.KeyID), []byte(keyID)) != 1 {
		return errors.New("snapshot signature metadata is invalid")
	}
	signature, err := base64.StdEncoding.DecodeString(s.Signature.Value)
	if err != nil || len(signature) != ed25519.SignatureSize {
		return errors.New("snapshot signature encoding is invalid")
	}
	payload, err := s.SigningBytes()
	if err != nil || !ed25519.Verify(publicKey, payload, signature) {
		return errors.New("snapshot signature is invalid")
	}
	if sameAsPrevious {
		previousPayload, err := previous.SigningBytes()
		if err != nil || !bytes.Equal(payload, previousPayload) || s.Signature.Value != previous.Signature.Value {
			return errors.New("observed snapshot identity was reused with different content")
		}
	}
	return nil
}

// ValidateAgainstGeneration verifies a signed snapshot for an offline rollout
// decision when the immutable previous generation is supplied by a protected
// Gateway checkpoint. The synthetic previous value is used only for transition
// and removal-safety checks; signature verification always covers the current
// snapshot byte-for-byte.
func (s GatewaySnapshot) ValidateAgainstGeneration(now time.Time, nodeID, keyID string, publicKey ed25519.PublicKey, previousGeneration uint64) error {
	if previousGeneration == 0 {
		return s.Validate(now, nodeID, keyID, publicKey, nil)
	}
	previous := &GatewaySnapshot{
		SnapshotID: "checkpoint_previous_generation",
		Generation: previousGeneration,
	}
	return s.Validate(now, nodeID, keyID, publicKey, previous)
}

func (s GatewaySnapshot) validateRemoval(previous *GatewaySnapshot) error {
	if len(previous.WireGuard.Peers) == 0 {
		return nil
	}
	current := make(map[string]bool, len(s.WireGuard.Peers))
	for _, peer := range s.WireGuard.Peers {
		current[peer.DeviceID] = true
	}
	removed := 0
	for _, peer := range previous.WireGuard.Peers {
		if !current[peer.DeviceID] {
			removed++
		}
	}
	percent := float64(removed) * 100 / float64(len(previous.WireGuard.Peers))
	if percent > s.Safety.MaxPeerRemovalPercent {
		return errors.New("peer removal exceeds signed safety limit")
	}
	return nil
}

func validatePrefixes(values []string) error {
	if len(values) == 0 {
		return errors.New("at least one prefix is required")
	}
	seen := make(map[string]bool, len(values))
	for _, value := range values {
		prefix, err := netip.ParsePrefix(value)
		if err != nil || !prefix.Addr().Is4() || seen[value] {
			return errors.New("invalid or duplicated IPv4 prefix")
		}
		seen[value] = true
	}
	return nil
}

func validateUniqueNonempty(label string, values []string, validate func(string) bool) error {
	if len(values) == 0 {
		return fmt.Errorf("%s must not be empty", label)
	}
	seen := make(map[string]bool, len(values))
	for _, value := range values {
		if seen[value] || !validate(value) {
			return fmt.Errorf("%s contains an invalid or duplicated value", label)
		}
		seen[value] = true
	}
	return nil
}

func validHost(value string) bool {
	return strings.TrimSpace(value) != "" && len(value) <= 253
}

func isCanonicalContractTime(value time.Time) bool {
	_, offset := value.Zone()
	return offset == 0 && value.Nanosecond() == 0
}

func LoadPublicKey(path string) (ed25519.PublicKey, error) {
	raw, err := readProtectedFile(path, "snapshot signing public key")
	if err != nil {
		return nil, fmt.Errorf("read snapshot signing public key: %w", err)
	}
	decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil || len(decoded) != ed25519.PublicKeySize {
		return nil, errors.New("snapshot signing public key must be base64-encoded Ed25519 public key")
	}
	return ed25519.PublicKey(decoded), nil
}

func rejectSecretFields(raw []byte) error {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return err
	}
	forbidden := map[string]bool{"private_key": true, "token": true, "auth_id": true, "vault_token": true, "refresh_token": true}
	var walk func(any) error
	walk = func(item any) error {
		switch typed := item.(type) {
		case map[string]any:
			for key, child := range typed {
				if forbidden[strings.ToLower(key)] {
					return errors.New("snapshot contains a forbidden secret field")
				}
				if err := walk(child); err != nil {
					return err
				}
			}
		case []any:
			for _, child := range typed {
				if err := walk(child); err != nil {
					return err
				}
			}
		}
		return nil
	}
	return walk(value)
}
