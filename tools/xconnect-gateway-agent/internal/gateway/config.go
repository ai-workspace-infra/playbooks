package gateway

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type Config struct {
	SchemaVersion    int                `json:"schema_version"`
	NodeID           string             `json:"node_id"`
	Mode             string             `json:"mode"`
	ControlPlane     ControlPlaneConfig `json:"control_plane"`
	Identity         IdentityConfig     `json:"identity"`
	ProviderManifest string             `json:"provider_manifest"`
	Runtime          RuntimeConfig      `json:"runtime"`
	Snapshots        SnapshotConfig     `json:"snapshots"`
	Apply            ApplyConfig        `json:"apply"`
	Health           HealthConfig       `json:"health"`
	Logging          LoggingConfig      `json:"logging"`
}

type ControlPlaneConfig struct {
	URL                          string `json:"url"`
	PollIntervalSeconds          int    `json:"poll_interval_seconds"`
	CredentialsFile              string `json:"credentials_file"`
	SnapshotSigningPublicKeyFile string `json:"snapshot_signing_public_key_file"`
	SnapshotSigningKeyID         string `json:"snapshot_signing_key_id"`
	APIVersion                   string `json:"api_version"`
}

type IdentityConfig struct {
	NodeID         string `json:"node_id"`
	CredentialType string `json:"credential_type"`
	CredentialFile string `json:"credential_file"`
}

type RuntimeConfig struct {
	ProxyCore          string `json:"proxy_core"`
	ProxyCoreVersion   string `json:"proxy_core_version"`
	XrayBinary         string `json:"xray_binary"`
	XrayConfig         string `json:"xray_config"`
	XrayService        string `json:"xray_service"`
	WireGuardInterface string `json:"wireguard_interface"`
	WireGuardConfig    string `json:"wireguard_config"`
	WireGuardService   string `json:"wireguard_service"`
}

type SnapshotConfig struct {
	CandidateDir      string `json:"candidate_dir"`
	LastKnownGoodDir  string `json:"last_known_good_dir"`
	EvidenceDir       string `json:"evidence_dir"`
	MinimumSchema     int    `json:"minimum_schema"`
	MaximumSchema     int    `json:"maximum_schema"`
	EmptyPeerSnapshot string `json:"empty_peer_snapshot"`
}

type ApplyConfig struct {
	Enabled bool `json:"enabled"`
}

type HealthConfig struct {
	ListenHost string `json:"listen_host"`
	ListenPort int    `json:"listen_port"`
	Path       string `json:"path"`
}

type LoggingConfig struct {
	Format       string   `json:"format"`
	RedactFields []string `json:"redact_fields"`
}

func LoadConfig(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("read config: %w", err)
	}
	var cfg Config
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&cfg); err != nil {
		return Config{}, fmt.Errorf("decode config: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return Config{}, errors.New("decode config: multiple JSON values")
	}
	if err := cfg.Validate(); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

func (c Config) Validate() error {
	if c.SchemaVersion != 1 || c.Mode != "shadow" || c.Apply.Enabled {
		return errors.New("config must be schema v1 shadow mode with runtime apply disabled")
	}
	if !idPattern.MatchString(c.NodeID) || c.Identity.NodeID != c.NodeID {
		return errors.New("node identity is missing or inconsistent")
	}
	if c.Identity.CredentialType != "short-lived-bearer-file" || c.Identity.CredentialFile != c.ControlPlane.CredentialsFile {
		return errors.New("short-lived bearer credential file contract is invalid")
	}
	controllerURL, err := url.Parse(c.ControlPlane.URL)
	if err != nil || controllerURL.Scheme != "https" || controllerURL.Host == "" || controllerURL.User != nil || controllerURL.RawQuery != "" || controllerURL.Fragment != "" {
		return errors.New("control-plane URL must be HTTPS without user information")
	}
	if c.ControlPlane.APIVersion != "v1" || !idPattern.MatchString(c.ControlPlane.SnapshotSigningKeyID) {
		return errors.New("control-plane API version and signing key ID are required")
	}
	if c.ControlPlane.PollIntervalSeconds < 1 {
		return errors.New("poll interval must be positive")
	}
	if c.Runtime.ProxyCore != "xray" || c.Runtime.ProxyCoreVersion == "" || !interfacePattern.MatchString(c.Runtime.WireGuardInterface) || c.Runtime.XrayService == "" || c.Runtime.WireGuardService == "" {
		return errors.New("v1 runtime requires Xray and a WireGuard interface")
	}
	if c.Snapshots.MinimumSchema != 1 || c.Snapshots.MaximumSchema != 1 || c.Snapshots.EmptyPeerSnapshot != "require-explicit-override" {
		return errors.New("snapshot schema or empty-peer policy is unsupported")
	}
	for label, path := range map[string]string{
		"credential": c.ControlPlane.CredentialsFile, "signing key": c.ControlPlane.SnapshotSigningPublicKeyFile,
		"candidate": c.Snapshots.CandidateDir, "last-known-good": c.Snapshots.LastKnownGoodDir, "evidence": c.Snapshots.EvidenceDir,
		"provider manifest": c.ProviderManifest, "Xray binary": c.Runtime.XrayBinary, "Xray config": c.Runtime.XrayConfig, "WireGuard config": c.Runtime.WireGuardConfig,
	} {
		if !filepath.IsAbs(path) {
			return fmt.Errorf("%s path must be absolute", label)
		}
	}
	if net.ParseIP(c.Health.ListenHost) == nil || !net.ParseIP(c.Health.ListenHost).IsLoopback() {
		return errors.New("health listener must use a loopback IP")
	}
	if c.Health.ListenPort < 1 || c.Health.ListenPort > 65535 || !strings.HasPrefix(c.Health.Path, "/") {
		return errors.New("health listener port or path is invalid")
	}
	redactions := make(map[string]bool, len(c.Logging.RedactFields))
	for _, field := range c.Logging.RedactFields {
		redactions[field] = true
	}
	for _, required := range []string{"authorization", "credential", "token", "signature.value"} {
		if !redactions[required] {
			return errors.New("logging redaction contract is incomplete")
		}
	}
	if c.Logging.Format != "json" {
		return errors.New("logging format must be json")
	}
	return nil
}

func (c Config) PollInterval() time.Duration {
	return time.Duration(c.ControlPlane.PollIntervalSeconds) * time.Second
}

func (c Config) HealthAddress() string {
	return net.JoinHostPort(c.Health.ListenHost, fmt.Sprintf("%d", c.Health.ListenPort))
}
