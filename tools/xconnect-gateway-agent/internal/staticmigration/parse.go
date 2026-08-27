package staticmigration

import (
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"os"
	"regexp"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

var (
	idPattern             = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{2,127}$`)
	uuidPattern           = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	forbiddenClientFields = map[string]bool{
		"private_key": true, "wireguard_private_key": true, "wg_private_key": true,
		"preshared_key": true, "pre_shared_key": true, "auth_id": true, "uuid": true,
		"password": true, "token": true, "secret": true, "credential": true,
		"credential_value": true, "transport_credential": true, "transport_token": true,
		"transport_password": true, "transport_uuid": true, "vless_uuid": true,
	}
	allowedClientFields = map[string]bool{
		"id": true, "wg_ip": true, "public_key": true, "attach_to": true, "tags": true,
	}
)

func ParseGroupVarsFile(path string) ([]StaticClient, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, errors.New("open group_vars input")
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, DefaultMaxInputSize+1))
	if err != nil || len(raw) > DefaultMaxInputSize {
		return nil, errors.New("group_vars input is unreadable or exceeds 4 MiB")
	}
	return ParseGroupVars(raw)
}

func ParseGroupVars(raw []byte) ([]StaticClient, error) {
	decoder := yaml.NewDecoder(bytes.NewReader(raw))
	var root yaml.Node
	if err := decoder.Decode(&root); err != nil {
		return nil, errors.New("decode group_vars YAML")
	}
	var trailing yaml.Node
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, errors.New("group_vars must contain one YAML document")
	}
	if len(root.Content) != 1 || root.Content[0].Kind != yaml.MappingNode {
		return nil, errors.New("group_vars root must be a mapping")
	}
	if err := rejectAliases(root.Content[0]); err != nil {
		return nil, err
	}
	clientsNode, err := findUniqueMappingValue(root.Content[0], SourceVariable)
	if err != nil {
		return nil, err
	}
	if clientsNode.Kind != yaml.SequenceNode || len(clientsNode.Content) == 0 {
		return nil, errors.New("static client list must be a non-empty sequence")
	}
	defaultAttachments, err := gatewayNodeIdentities(root.Content[0])
	if err != nil {
		return nil, err
	}
	if len(defaultAttachments) == 0 {
		return nil, errors.New("distributed VPN node mapping is required to validate client attachments")
	}
	clients := make([]StaticClient, 0, len(clientsNode.Content))
	seenIDs := map[string]bool{}
	seenAddresses := map[string]bool{}
	seenKeys := map[string]bool{}
	for index, item := range clientsNode.Content {
		client, err := parseClient(item, defaultAttachments)
		if err != nil {
			return nil, fmt.Errorf("client %d: %w", index, err)
		}
		if seenIDs[client.DeviceID] || seenAddresses[client.Address] || seenKeys[client.PublicKey] {
			return nil, errors.New("static clients must have unique device IDs, addresses, and public keys")
		}
		seenIDs[client.DeviceID], seenAddresses[client.Address], seenKeys[client.PublicKey] = true, true, true
		clients = append(clients, client)
	}
	sort.Slice(clients, func(left, right int) bool { return clients[left].DeviceID < clients[right].DeviceID })
	return clients, nil
}

func parseClient(node *yaml.Node, defaultAttachments []string) (StaticClient, error) {
	if node.Kind != yaml.MappingNode {
		return StaticClient{}, errors.New("client must be a mapping")
	}
	values := map[string]*yaml.Node{}
	for index := 0; index < len(node.Content); index += 2 {
		key := strings.ToLower(node.Content[index].Value)
		if forbiddenClientFields[key] {
			return StaticClient{}, fmt.Errorf("forbidden private or transport credential field %q", key)
		}
		if !allowedClientFields[key] {
			return StaticClient{}, fmt.Errorf("unknown client field %q", key)
		}
		if values[key] != nil {
			return StaticClient{}, fmt.Errorf("duplicate client field %q", key)
		}
		values[key] = node.Content[index+1]
	}
	deviceID, err := scalar(values["id"], "id")
	if err != nil || !idPattern.MatchString(deviceID) {
		return StaticClient{}, errors.New("device id is invalid")
	}
	address, err := scalar(values["wg_ip"], "wg_ip")
	parsedAddress, parseErr := netip.ParseAddr(address)
	if err != nil || parseErr != nil || !parsedAddress.Is4() || parsedAddress.IsUnspecified() || parsedAddress.IsMulticast() {
		return StaticClient{}, errors.New("wg_ip must be a usable IPv4 host address")
	}
	publicKey, err := scalar(values["public_key"], "public_key")
	decodedKey, decodeErr := base64.StdEncoding.DecodeString(publicKey)
	if err != nil || decodeErr != nil || len(decodedKey) != 32 {
		return StaticClient{}, errors.New("public_key must encode exactly 32 bytes")
	}
	attachments := append([]string(nil), defaultAttachments...)
	if values["attach_to"] != nil {
		attachments, err = stringList(values["attach_to"], "attach_to", true)
		if err != nil {
			return StaticClient{}, err
		}
	} else if len(attachments) == 0 {
		return StaticClient{}, errors.New("attach_to is omitted but no distributed VPN nodes define its default")
	}
	for _, attachment := range attachments {
		if !idPattern.MatchString(attachment) || !contains(defaultAttachments, attachment) {
			return StaticClient{}, errors.New("attach_to contains an invalid gateway identity")
		}
	}
	tags, err := stringList(values["tags"], "tags", false)
	if err != nil {
		return StaticClient{}, err
	}
	for _, tag := range tags {
		if sensitiveTag(tag) {
			return StaticClient{}, errors.New("tags must not embed private or transport credentials")
		}
	}
	tags = append(tags, MigrationSourceTag)
	return StaticClient{DeviceID: deviceID, Address: parsedAddress.String(), PublicKey: publicKey, Attachments: uniqueSorted(attachments), Tags: uniqueSorted(tags)}, nil
}

func scalar(node *yaml.Node, label string) (string, error) {
	if node == nil || node.Kind != yaml.ScalarNode || node.Tag != "!!str" || strings.TrimSpace(node.Value) == "" {
		return "", fmt.Errorf("%s must be a non-empty string", label)
	}
	return strings.TrimSpace(node.Value), nil
}

func stringList(node *yaml.Node, label string, required bool) ([]string, error) {
	if node == nil {
		if required {
			return nil, fmt.Errorf("%s is required", label)
		}
		return []string{}, nil
	}
	if node.Kind != yaml.SequenceNode || (required && len(node.Content) == 0) {
		return nil, fmt.Errorf("%s must be a non-empty string sequence", label)
	}
	values := make([]string, 0, len(node.Content))
	for _, item := range node.Content {
		value, err := scalar(item, label)
		if err != nil {
			return nil, err
		}
		values = append(values, value)
	}
	if len(uniqueSorted(values)) != len(values) {
		return nil, fmt.Errorf("%s contains duplicate values", label)
	}
	return values, nil
}

func findUniqueMappingValue(mapping *yaml.Node, wanted string) (*yaml.Node, error) {
	var found *yaml.Node
	for index := 0; index < len(mapping.Content); index += 2 {
		if mapping.Content[index].Value == wanted {
			if found != nil {
				return nil, fmt.Errorf("group_vars contains duplicate %s keys", wanted)
			}
			found = mapping.Content[index+1]
		}
	}
	if found == nil {
		return nil, fmt.Errorf("group_vars does not define %s", wanted)
	}
	return found, nil
}

func gatewayNodeIdentities(mapping *yaml.Node) ([]string, error) {
	const variable = "xworkmate_bridge_distributed_vpn_nodes"
	var nodes *yaml.Node
	for index := 0; index < len(mapping.Content); index += 2 {
		if mapping.Content[index].Value == variable {
			if nodes != nil {
				return nil, errors.New("group_vars contains duplicate distributed VPN node mappings")
			}
			nodes = mapping.Content[index+1]
		}
	}
	if nodes == nil {
		return []string{}, nil
	}
	if nodes.Kind != yaml.MappingNode {
		return nil, errors.New("distributed VPN nodes must be a mapping")
	}
	identities := make([]string, 0, len(nodes.Content)/2)
	for index := 0; index < len(nodes.Content); index += 2 {
		identity := nodes.Content[index].Value
		if !idPattern.MatchString(identity) {
			return nil, errors.New("distributed VPN node identity is invalid")
		}
		identities = append(identities, identity)
	}
	if len(uniqueSorted(identities)) != len(identities) {
		return nil, errors.New("distributed VPN node identities must be unique")
	}
	return uniqueSorted(identities), nil
}

func rejectAliases(node *yaml.Node) error {
	if node.Kind == yaml.AliasNode || node.Anchor != "" {
		return errors.New("YAML aliases and anchors are not allowed in migration input")
	}
	for _, child := range node.Content {
		if err := rejectAliases(child); err != nil {
			return err
		}
	}
	return nil
}

func uniqueSorted(values []string) []string {
	seen := make(map[string]bool, len(values))
	result := make([]string, 0, len(values))
	for _, value := range values {
		if !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	sort.Strings(result)
	return result
}

func sensitiveTag(value string) bool {
	normalized := strings.ToLower(strings.TrimSpace(value))
	for _, prefix := range []string{"private_key:", "preshared_key:", "auth_id:", "password:", "token:", "secret:", "credential:", "uuid:", "vless_uuid:"} {
		if strings.HasPrefix(normalized, prefix) {
			return true
		}
	}
	return false
}
