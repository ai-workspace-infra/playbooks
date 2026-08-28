package gateway

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"sort"
	"strconv"
	"strings"
)

const policyCompilerV1 = "xconnect-acl-v1alpha1.1"

var requiredProtectedFlows = []string{
	"control:controller-session",
	"control:gateway-apply-result",
	"control:gateway-heartbeat",
	"control:gateway-policy-artifact",
	"control:gateway-snapshot",
}

// ACLArtifact is the sanitized Accounts enforcement artifact. It intentionally
// contains no email, UUID, group, tag-owner, address, or secret material.
type ACLArtifact struct {
	SchemaVersion   int       `json:"schema_version"`
	CompilerVersion string    `json:"compiler_version"`
	NetworkID       string    `json:"network_id"`
	Revision        uint64    `json:"revision"`
	DefaultAction   string    `json:"default_action"`
	ProtectedFlows  []string  `json:"protected_flows"`
	Rules           []ACLRule `json:"rules"`
}

type ACLRule struct {
	ID                 string   `json:"id"`
	Action             string   `json:"action"`
	SourceDevices      []string `json:"source_devices"`
	DestinationDevices []string `json:"destination_devices"`
	Protocols          []string `json:"protocols"`
	Ports              []int    `json:"ports"`
}

type PolicyProvider interface {
	PolicyArtifact(context.Context, string, uint64, string) ([]byte, error)
}

func DecodePolicyArtifact(raw []byte) (ACLArtifact, error) {
	var artifact ACLArtifact
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&artifact); err != nil {
		return ACLArtifact{}, errors.New("decode policy artifact")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return ACLArtifact{}, errors.New("policy artifact contains multiple JSON values")
	}
	return artifact, nil
}

func ValidatePolicyArtifact(raw []byte, snapshot GatewaySnapshot) (ACLArtifact, error) {
	if len(raw) == 0 || len(raw) > 4<<20 {
		return ACLArtifact{}, errors.New("policy artifact size is invalid")
	}
	digest := sha256.Sum256(raw)
	if hex.EncodeToString(digest[:]) != snapshot.Policy.RulesetSHA256 {
		return ACLArtifact{}, errors.New("policy artifact digest mismatch")
	}
	artifact, err := DecodePolicyArtifact(raw)
	if err != nil {
		return ACLArtifact{}, err
	}
	if artifact.SchemaVersion != 1 || artifact.CompilerVersion != policyCompilerV1 || artifact.NetworkID == "" || artifact.DefaultAction != "deny" {
		return ACLArtifact{}, errors.New("unsupported policy artifact contract")
	}
	if artifact.Revision == 0 && len(artifact.Rules) != 0 {
		return ACLArtifact{}, errors.New("bootstrap policy revision zero must be empty default-deny")
	}
	canonical, err := json.Marshal(artifact)
	if err != nil || !bytes.Equal(raw, canonical) {
		return ACLArtifact{}, errors.New("policy artifact is not canonical compact JSON")
	}
	if strings.Join(artifact.ProtectedFlows, "\x00") != strings.Join(requiredProtectedFlows, "\x00") {
		return ACLArtifact{}, errors.New("policy artifact protected flow contract mismatch")
	}
	seen := map[string]bool{}
	lastAction, lastID := "deny", ""
	for index, rule := range artifact.Rules {
		if !idPattern.MatchString(rule.ID) || seen[rule.ID] || (rule.Action != "accept" && rule.Action != "deny") || len(rule.SourceDevices) == 0 || len(rule.DestinationDevices) == 0 {
			return ACLArtifact{}, errors.New("policy artifact contains an invalid rule")
		}
		if index > 0 && ((lastAction == "accept" && rule.Action == "deny") || (lastAction == rule.Action && lastID >= rule.ID)) {
			return ACLArtifact{}, errors.New("policy artifact rules are not in canonical deny-first order")
		}
		lastAction, lastID = rule.Action, rule.ID
		seen[rule.ID] = true
		if !sortedUniqueStrings(rule.SourceDevices) || !sortedUniqueStrings(rule.DestinationDevices) || !sortedUniqueStrings(rule.Protocols) || !sortedUniqueInts(rule.Ports) {
			return ACLArtifact{}, errors.New("policy artifact rule members are not canonical")
		}
		for _, protocol := range rule.Protocols {
			if protocol != "tcp" && protocol != "udp" && protocol != "icmp" {
				return ACLArtifact{}, errors.New("policy artifact protocol is invalid")
			}
		}
		hasICMP := false
		for _, protocol := range rule.Protocols {
			hasICMP = hasICMP || protocol == "icmp"
		}
		if hasICMP {
			if len(rule.Protocols) != 1 || len(rule.Ports) != 0 {
				return ACLArtifact{}, errors.New("ICMP policy rule must be isolated and contain no ports")
			}
		} else if len(rule.Ports) == 0 {
			return ACLArtifact{}, errors.New("TCP/UDP policy rule requires ports")
		}
	}
	// Artifact revision is source revision; signed snapshot policy generation is
	// activation generation. They need not be equal. The snapshot signature binds
	// generation+digest and the digest covers revision byte-for-byte.
	return artifact, nil
}

func sortedUniqueStrings(values []string) bool {
	for index, value := range values {
		if strings.TrimSpace(value) != value || value == "" || (index > 0 && values[index-1] >= value) {
			return false
		}
	}
	return true
}

func sortedUniqueInts(values []int) bool {
	for index, value := range values {
		if value < 1 || value > 65535 || (index > 0 && values[index-1] >= value) {
			return false
		}
	}
	return true
}

func RenderNFTables(snapshot GatewaySnapshot, artifact ACLArtifact) ([]byte, error) {
	if !interfacePattern.MatchString(snapshot.WireGuard.InterfaceName) {
		return nil, errors.New("nftables renderer rejected unsafe overlay interface")
	}
	devicePrefixes := make(map[string][]string, len(snapshot.WireGuard.Peers))
	for _, peer := range snapshot.WireGuard.Peers {
		for _, raw := range peer.AllowedIPs {
			prefix, err := netip.ParsePrefix(raw)
			if err != nil || !prefix.Addr().Is4() {
				return nil, errors.New("nftables v1 requires canonical IPv4 peer prefixes")
			}
			devicePrefixes[peer.DeviceID] = append(devicePrefixes[peer.DeviceID], prefix.String())
		}
	}
	resolve := func(deviceIDs []string) ([]string, error) {
		set := map[string]bool{}
		for _, deviceID := range deviceIDs {
			prefixes, ok := devicePrefixes[deviceID]
			if !ok || len(prefixes) == 0 {
				return nil, errors.New("policy artifact references a device absent from signed snapshot")
			}
			for _, prefix := range prefixes {
				set[prefix] = true
			}
		}
		values := make([]string, 0, len(set))
		for value := range set {
			values = append(values, value)
		}
		sort.Strings(values)
		return values, nil
	}

	var rules strings.Builder
	// The base chain accepts unrelated host forwarding and jumps into the
	// default-deny overlay chain only when ingress or egress is this signed,
	// node-bound WireGuard interface. Local controller traffic uses host
	// output/input and never traverses this hook.
	rules.WriteString("table inet xconnect_one {\n")
	rules.WriteString("  chain forward { type filter hook forward priority filter; policy accept;\n")
	rules.WriteString("    iifname \"")
	rules.WriteString(snapshot.WireGuard.InterfaceName)
	rules.WriteString("\" jump overlay\n    oifname \"")
	rules.WriteString(snapshot.WireGuard.InterfaceName)
	rules.WriteString("\" jump overlay\n  }\n")
	rules.WriteString("  chain overlay {\n")
	for _, rule := range artifact.Rules {
		if !idPattern.MatchString(rule.ID) || (rule.Action != "accept" && rule.Action != "deny") {
			return nil, errors.New("nftables renderer rejected unsafe rule identity or verdict")
		}
		sources, err := resolve(rule.SourceDevices)
		if err != nil {
			return nil, fmt.Errorf("render rule %s source: %w", rule.ID, err)
		}
		destinations, err := resolve(rule.DestinationDevices)
		if err != nil {
			return nil, fmt.Errorf("render rule %s destination: %w", rule.ID, err)
		}
		for _, protocol := range rule.Protocols {
			rules.WriteString("    ip saddr { ")
			rules.WriteString(strings.Join(sources, ", "))
			rules.WriteString(" } ip daddr { ")
			rules.WriteString(strings.Join(destinations, ", "))
			rules.WriteString(" } ")
			if protocol == "icmp" {
				rules.WriteString("ip protocol icmp")
			} else {
				rules.WriteString(protocol)
				rules.WriteString(" dport { ")
				for index, port := range rule.Ports {
					if index > 0 {
						rules.WriteString(", ")
					}
					rules.WriteString(strconv.Itoa(port))
				}
				rules.WriteString(" }")
			}
			verdict := "accept"
			if rule.Action == "deny" {
				verdict = "drop"
			}
			rules.WriteString(" ")
			rules.WriteString(verdict)
			rules.WriteString(" comment \"")
			rules.WriteString(rule.ID)
			rules.WriteString("\"\n")
			if rule.Action == "accept" {
				// The response path is scoped to the exact inverse principals and
				// service source port. Removing the policy rule removes this stateful
				// allowance, so revoked established flows cannot bypass the next
				// generation's default deny.
				rules.WriteString("    ip saddr { ")
				rules.WriteString(strings.Join(destinations, ", "))
				rules.WriteString(" } ip daddr { ")
				rules.WriteString(strings.Join(sources, ", "))
				rules.WriteString(" } ")
				if protocol == "icmp" {
					rules.WriteString("ip protocol icmp")
				} else {
					rules.WriteString(protocol)
					rules.WriteString(" sport { ")
					for index, port := range rule.Ports {
						if index > 0 {
							rules.WriteString(", ")
						}
						rules.WriteString(strconv.Itoa(port))
					}
					rules.WriteString(" }")
				}
				rules.WriteString(" ct state established,related accept comment \"")
				rules.WriteString(rule.ID)
				rules.WriteString("-return\"\n")
			}
		}
	}
	rules.WriteString("    drop\n  }\n}\n")
	return []byte(rules.String()), nil
}
