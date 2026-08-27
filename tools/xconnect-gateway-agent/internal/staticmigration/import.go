package staticmigration

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"path"
	"sort"
	"strings"
	"time"
)

func BuildImportDocument(networkID, ownerUserID string, clients []StaticClient) (ImportDocument, error) {
	if !idPattern.MatchString(networkID) {
		return ImportDocument{}, errors.New("network ID is invalid")
	}
	if !uuidPattern.MatchString(ownerUserID) {
		return ImportDocument{}, errors.New("owner user ID must be a canonical lowercase UUID")
	}
	if len(clients) == 0 {
		return ImportDocument{}, errors.New("at least one static client is required")
	}
	devices := make([]ImportDevice, 0, len(clients))
	for _, client := range clients {
		devices = append(devices, ImportDevice{
			DeviceID:           client.DeviceID,
			WireGuardPublicKey: client.PublicKey,
			Addresses:          []string{client.Address + "/32"},
			Tags:               append([]string(nil), client.Tags...),
			Attachments:        append([]string(nil), client.Attachments...),
		})
	}
	normalizedDevices, err := normalizeImportDevices(devices)
	if err != nil {
		return ImportDocument{}, err
	}
	baseline, err := json.Marshal(normalizedDevices)
	if err != nil {
		return ImportDocument{}, err
	}
	digest := sha256.Sum256(baseline)
	return ImportDocument{
		SchemaVersion: SchemaVersion,
		Kind:          ImportDocumentKind,
		NetworkID:     networkID,
		OwnerUserID:   ownerUserID,
		Source: ImportSource{
			Kind:           "ansible-group-vars",
			Variable:       SourceVariable,
			BaselineSHA256: hex.EncodeToString(digest[:]),
		},
		Devices: normalizedDevices,
	}, nil
}

func MarshalDocument(document ImportDocument) ([]byte, error) {
	normalized, err := ValidateImportDocument(document)
	if err != nil {
		return nil, err
	}
	raw, err := json.MarshalIndent(normalized, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(raw, '\n'), nil
}

func IdempotencyKey(document []byte) string {
	digest := sha256.Sum256(document)
	return "sha256-" + hex.EncodeToString(digest[:])
}

// CanonicalDocumentBytes is the byte-for-byte v1 request and idempotency
// boundary shared with accounts: strict decode, then compact JSON marshal using
// ImportDocument field order and no trailing newline.
func CanonicalDocumentBytes(raw []byte) ([]byte, error) {
	var document ImportDocument
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&document); err != nil {
		return nil, errors.New("decode import document")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("decode import document")
	}
	normalized, err := ValidateImportDocument(document)
	if err != nil {
		return nil, err
	}
	return json.Marshal(normalized)
}

func ValidateImportDocument(document ImportDocument) (ImportDocument, error) {
	if document.SchemaVersion != SchemaVersion || document.Kind != ImportDocumentKind || !idPattern.MatchString(document.NetworkID) || !uuidPattern.MatchString(document.OwnerUserID) || document.Source.Kind != "ansible-group-vars" || document.Source.Variable != SourceVariable {
		return ImportDocument{}, errors.New("import document contract is invalid")
	}
	normalizedDevices, err := normalizeImportDevices(document.Devices)
	if err != nil {
		return ImportDocument{}, err
	}
	baselineBytes, err := json.Marshal(normalizedDevices)
	if err != nil {
		return ImportDocument{}, err
	}
	baselineDigest := sha256.Sum256(baselineBytes)
	expectedBaseline := hex.EncodeToString(baselineDigest[:])
	if document.Source.BaselineSHA256 != expectedBaseline {
		return ImportDocument{}, errors.New("import baseline digest does not match normalized devices")
	}
	document.Devices = normalizedDevices
	return document, nil
}

func normalizeImportDevices(devices []ImportDevice) ([]ImportDevice, error) {
	if len(devices) == 0 || len(devices) > 10000 {
		return nil, errors.New("import devices must contain between 1 and 10000 entries")
	}
	normalized := make([]ImportDevice, 0, len(devices))
	seenIDs, seenKeys, seenAddresses := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, device := range devices {
		if !idPattern.MatchString(device.DeviceID) || seenIDs[device.DeviceID] {
			return nil, errors.New("import device identity is invalid or duplicated")
		}
		key, err := base64.StdEncoding.DecodeString(device.WireGuardPublicKey)
		if err != nil || len(key) != 32 || seenKeys[device.WireGuardPublicKey] {
			return nil, errors.New("import WireGuard public key is invalid or duplicated")
		}
		if len(device.Addresses) != 1 {
			return nil, errors.New("import device must contain exactly one IPv4 /32 address")
		}
		prefix, err := netip.ParsePrefix(device.Addresses[0])
		if err != nil || !prefix.Addr().Is4() || prefix.Bits() != 32 || prefix.String() != device.Addresses[0] || seenAddresses[device.Addresses[0]] {
			return nil, errors.New("import device address is invalid or duplicated")
		}
		if len(device.Attachments) == 0 {
			return nil, errors.New("import device attachments must not be empty")
		}
		attachments, err := normalizeUniqueStrings(device.Attachments, func(value string) bool { return idPattern.MatchString(value) })
		if err != nil {
			return nil, errors.New("import device attachments are invalid or duplicated")
		}
		tags, err := normalizeUniqueStrings(device.Tags, func(value string) bool {
			return strings.TrimSpace(value) == value && value != "" && len(value) <= 128 && !sensitiveTag(value)
		})
		if err != nil || !contains(tags, MigrationSourceTag) {
			return nil, errors.New("import device tags are invalid, duplicated, or missing migration source")
		}
		seenIDs[device.DeviceID], seenKeys[device.WireGuardPublicKey], seenAddresses[device.Addresses[0]] = true, true, true
		normalized = append(normalized, ImportDevice{DeviceID: device.DeviceID, WireGuardPublicKey: device.WireGuardPublicKey, Addresses: []string{prefix.String()}, Tags: tags, Attachments: attachments})
	}
	sort.Slice(normalized, func(left, right int) bool { return normalized[left].DeviceID < normalized[right].DeviceID })
	return normalized, nil
}

func normalizeUniqueStrings(values []string, validate func(string) bool) ([]string, error) {
	seen := map[string]bool{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		if seen[value] || !validate(value) {
			return nil, errors.New("invalid or duplicate string")
		}
		seen[value] = true
		result = append(result, value)
	}
	sort.Strings(result)
	return result, nil
}

type ImportClient struct {
	baseURL          *url.URL
	serviceTokenFile string
	httpClient       *http.Client
}

func NewImportClient(rawURL, serviceTokenFile string, httpClient *http.Client) (*ImportClient, error) {
	baseURL, err := url.Parse(rawURL)
	if err != nil || baseURL.Scheme != "https" || baseURL.Host == "" || baseURL.User != nil || baseURL.RawQuery != "" || baseURL.Fragment != "" {
		return nil, errors.New("Controller URL must be HTTPS without credentials, query, or fragment")
	}
	if _, err := readServiceToken(serviceTokenFile); err != nil {
		return nil, err
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 20 * time.Second}
	}
	return &ImportClient{baseURL: baseURL, serviceTokenFile: serviceTokenFile, httpClient: httpClient}, nil
}

func (c *ImportClient) Apply(ctx context.Context, document []byte) (ApplyReceipt, error) {
	serviceToken, err := readServiceToken(c.serviceTokenFile)
	if err != nil {
		return ApplyReceipt{}, err
	}
	canonicalDocument, err := CanonicalDocumentBytes(document)
	if err != nil {
		return ApplyReceipt{}, err
	}
	requestURL := *c.baseURL
	requestURL.Path = path.Join(c.baseURL.Path, ImportEndpoint)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, requestURL.String(), bytes.NewReader(canonicalDocument))
	if err != nil {
		return ApplyReceipt{}, errors.New("create import request")
	}
	idempotencyKey := IdempotencyKey(canonicalDocument)
	request.Header.Set("X-Service-Token", serviceToken)
	request.Header.Set("Content-Type", ImportMediaType)
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Idempotency-Key", idempotencyKey)
	response, err := c.httpClient.Do(request)
	if err != nil {
		return ApplyReceipt{}, errors.New("Controller import request failed")
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return ApplyReceipt{}, fmt.Errorf("Controller import request failed with status %d", response.StatusCode)
	}
	return ApplyReceipt{Applied: true, IdempotencyKey: idempotencyKey, StatusCode: response.StatusCode}, nil
}

func readServiceToken(filePath string) (string, error) {
	before, err := os.Lstat(filePath)
	if err != nil || before.Mode()&os.ModeSymlink != 0 || !before.Mode().IsRegular() || before.Mode().Perm()&^os.FileMode(0o640) != 0 {
		return "", errors.New("service token file must be a regular non-symlink file with permissions no wider than 0640")
	}
	file, err := os.Open(filePath)
	if err != nil {
		return "", errors.New("read service token file")
	}
	defer file.Close()
	after, err := file.Stat()
	if err != nil || !os.SameFile(before, after) {
		return "", errors.New("service token file changed while opening")
	}
	raw, err := io.ReadAll(io.LimitReader(file, (1<<20)+1))
	if err != nil || len(raw) > 1<<20 {
		return "", errors.New("service token file is unreadable or oversized")
	}
	token := strings.TrimSpace(string(raw))
	if token == "" || strings.ContainsAny(token, "\r\n") {
		return "", errors.New("service token file must contain one non-empty value")
	}
	return token, nil
}
