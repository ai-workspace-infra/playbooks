package staticmigration

const (
	SchemaVersion       = 1
	SourceVariable      = "xworkmate_bridge_distributed_vpn_clients"
	ImportDocumentKind  = "xconnect.accounts.static-client-import"
	ImportMediaType     = "application/vnd.xconnect.static-client-import.v1+json"
	ImportEndpoint      = "/api/internal/overlay/v1/imports/static-clients"
	MigrationSourceTag  = "migration:static-group-vars"
	DiffExitCode        = 3
	InputErrorExitCode  = 2
	DefaultMaxInputSize = 4 << 20
)

type StaticClient struct {
	DeviceID    string
	Address     string
	PublicKey   string
	Attachments []string
	Tags        []string
}

type ImportDocument struct {
	SchemaVersion int            `json:"schema_version"`
	Kind          string         `json:"kind"`
	NetworkID     string         `json:"network_id"`
	OwnerUserID   string         `json:"owner_user_id"`
	Source        ImportSource   `json:"source"`
	Devices       []ImportDevice `json:"devices"`
}

type ImportSource struct {
	Kind           string `json:"kind"`
	Variable       string `json:"variable"`
	BaselineSHA256 string `json:"baseline_sha256"`
}

type ImportDevice struct {
	DeviceID           string   `json:"device_id"`
	WireGuardPublicKey string   `json:"wireguard_public_key"`
	Addresses          []string `json:"addresses"`
	Tags               []string `json:"tags"`
	Attachments        []string `json:"attachments"`
}

type DiffEvidence struct {
	SchemaVersion       int                 `json:"schema_version"`
	Kind                string              `json:"kind"`
	Status              string              `json:"status"`
	NodeID              string              `json:"node_id"`
	Attachment          string              `json:"attachment"`
	SnapshotID          string              `json:"snapshot_id"`
	ObservedGeneration  uint64              `json:"observed_generation"`
	StaticDevices       int                 `json:"static_devices"`
	ProjectedPeers      int                 `json:"projected_peers"`
	MissingDevices      []string            `json:"missing_devices"`
	UnexpectedDevices   []string            `json:"unexpected_devices"`
	PublicKeyMismatches []PublicKeyMismatch `json:"public_key_mismatches"`
	AllowedIPMismatches []AllowedIPMismatch `json:"allowed_ip_mismatches"`
}

type PublicKeyMismatch struct {
	DeviceID             string `json:"device_id"`
	StaticFingerprint    string `json:"static_fingerprint"`
	ProjectedFingerprint string `json:"projected_fingerprint"`
}

type AllowedIPMismatch struct {
	DeviceID            string   `json:"device_id"`
	StaticAllowedIPs    []string `json:"static_allowed_ips"`
	ProjectedAllowedIPs []string `json:"projected_allowed_ips"`
}

type ApplyReceipt struct {
	Applied        bool
	IdempotencyKey string
	StatusCode     int
}
