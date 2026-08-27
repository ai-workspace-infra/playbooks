package staticmigration

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"net/netip"
	"sort"
	"strings"

	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/gateway"
)

func CompareSnapshot(clients []StaticClient, attachment string, snapshot gateway.GatewaySnapshot) (DiffEvidence, error) {
	if !idPattern.MatchString(attachment) {
		return DiffEvidence{}, errors.New("attachment identity is invalid")
	}
	if !idPattern.MatchString(snapshot.NodeID) || !idPattern.MatchString(snapshot.SnapshotID) || snapshot.Generation == 0 || snapshot.Generation <= snapshot.ExpectedPreviousGeneration || snapshot.ProxyCore != "xray" {
		return DiffEvidence{}, errors.New("GatewaySnapshot identity, generation, or proxy core is invalid")
	}
	static := map[string]StaticClient{}
	for _, client := range clients {
		if contains(client.Attachments, attachment) {
			static[client.DeviceID] = client
		}
	}
	projected := map[string]gateway.GatewayPeer{}
	for _, peer := range snapshot.WireGuard.Peers {
		if !idPattern.MatchString(peer.DeviceID) || projected[peer.DeviceID].DeviceID != "" {
			return DiffEvidence{}, errors.New("snapshot peers contain an invalid or duplicate device identity")
		}
		key, err := base64.StdEncoding.DecodeString(peer.PublicKey)
		if err != nil || len(key) != 32 {
			return DiffEvidence{}, errors.New("snapshot peer contains an invalid public key")
		}
		if _, err := canonicalAllowedIPs(peer.AllowedIPs); err != nil {
			return DiffEvidence{}, err
		}
		projected[peer.DeviceID] = peer
	}
	evidence := DiffEvidence{
		SchemaVersion:      SchemaVersion,
		Kind:               "xconnect.gateway.static-snapshot-diff",
		Status:             "equal",
		NodeID:             snapshot.NodeID,
		Attachment:         attachment,
		SnapshotID:         snapshot.SnapshotID,
		ObservedGeneration: snapshot.Generation,
		StaticDevices:      len(static),
		ProjectedPeers:     len(projected),
		MissingDevices:     []string{}, UnexpectedDevices: []string{},
		PublicKeyMismatches: []PublicKeyMismatch{}, AllowedIPMismatches: []AllowedIPMismatch{},
	}
	for deviceID, client := range static {
		peer, exists := projected[deviceID]
		if !exists {
			evidence.MissingDevices = append(evidence.MissingDevices, deviceID)
			continue
		}
		if client.PublicKey != peer.PublicKey {
			evidence.PublicKeyMismatches = append(evidence.PublicKeyMismatches, PublicKeyMismatch{
				DeviceID: deviceID, StaticFingerprint: keyFingerprint(client.PublicKey), ProjectedFingerprint: keyFingerprint(peer.PublicKey),
			})
		}
		staticAllowed := []string{client.Address + "/32"}
		projectedAllowed, _ := canonicalAllowedIPs(peer.AllowedIPs)
		if strings.Join(staticAllowed, ",") != strings.Join(projectedAllowed, ",") {
			evidence.AllowedIPMismatches = append(evidence.AllowedIPMismatches, AllowedIPMismatch{DeviceID: deviceID, StaticAllowedIPs: staticAllowed, ProjectedAllowedIPs: projectedAllowed})
		}
	}
	for deviceID := range projected {
		if _, exists := static[deviceID]; !exists {
			evidence.UnexpectedDevices = append(evidence.UnexpectedDevices, deviceID)
		}
	}
	sort.Strings(evidence.MissingDevices)
	sort.Strings(evidence.UnexpectedDevices)
	sort.Slice(evidence.PublicKeyMismatches, func(left, right int) bool {
		return evidence.PublicKeyMismatches[left].DeviceID < evidence.PublicKeyMismatches[right].DeviceID
	})
	sort.Slice(evidence.AllowedIPMismatches, func(left, right int) bool {
		return evidence.AllowedIPMismatches[left].DeviceID < evidence.AllowedIPMismatches[right].DeviceID
	})
	if len(evidence.MissingDevices)+len(evidence.UnexpectedDevices)+len(evidence.PublicKeyMismatches)+len(evidence.AllowedIPMismatches) > 0 {
		evidence.Status = "drift"
	}
	return evidence, nil
}

func canonicalAllowedIPs(values []string) ([]string, error) {
	if len(values) == 0 {
		return nil, errors.New("snapshot peer allowed IPs must not be empty")
	}
	result := make([]string, 0, len(values))
	seen := map[string]bool{}
	for _, value := range values {
		prefix, err := netip.ParsePrefix(value)
		if err != nil || !prefix.Addr().Is4() || seen[prefix.String()] {
			return nil, errors.New("snapshot peer allowed IPs contain an invalid or duplicate IPv4 prefix")
		}
		seen[prefix.String()] = true
		result = append(result, prefix.String())
	}
	sort.Strings(result)
	return result, nil
}

func keyFingerprint(value string) string {
	digest := sha256.Sum256([]byte(value))
	return "sha256:" + hex.EncodeToString(digest[:8])
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
