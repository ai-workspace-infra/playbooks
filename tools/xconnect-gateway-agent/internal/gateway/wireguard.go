package gateway

import (
	"context"
	"errors"
	"os/exec"
	"sort"
	"strings"
)

type CommandRunner interface {
	Run(context.Context, string, ...string) ([]byte, error)
}

type ExecRunner struct{}

func (ExecRunner) Run(ctx context.Context, name string, args ...string) ([]byte, error) {
	return exec.CommandContext(ctx, name, args...).Output()
}

type CurrentPeer struct {
	PublicKey  string
	AllowedIPs []string
}

type WireGuardReader struct {
	Runner CommandRunner
	Binary string
}

func (r WireGuardReader) Peers(ctx context.Context, interfaceName string) ([]CurrentPeer, error) {
	if r.Runner == nil {
		return nil, errors.New("WireGuard command runner is not configured")
	}
	binary := r.Binary
	if binary == "" {
		binary = "wg"
	}
	output, err := r.Runner.Run(ctx, binary, "show", interfaceName, "dump")
	if err != nil {
		return nil, errors.New("read WireGuard shadow state")
	}
	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	if len(lines) <= 1 {
		return nil, nil
	}
	peers := make([]CurrentPeer, 0, len(lines)-1)
	for _, line := range lines[1:] {
		fields := strings.Split(line, "\t")
		if len(fields) < 4 {
			return nil, errors.New("invalid WireGuard dump")
		}
		allowed := splitAndSort(fields[3])
		peers = append(peers, CurrentPeer{PublicKey: fields[0], AllowedIPs: allowed})
	}
	return peers, nil
}

type DiffSummary struct {
	Status          string `json:"status"`
	Equal           bool   `json:"equal"`
	ProjectedPeers  int    `json:"projected_peers"`
	CurrentPeers    int    `json:"current_peers"`
	MissingPeers    int    `json:"missing_peers"`
	UnexpectedPeers int    `json:"unexpected_peers"`
	RouteMismatches int    `json:"route_mismatches"`
}

func ComparePeers(projected []GatewayPeer, current []CurrentPeer) DiffSummary {
	expected := make(map[string][]string, len(projected))
	for _, peer := range projected {
		expected[peer.PublicKey] = sortedCopy(peer.AllowedIPs)
	}
	actual := make(map[string][]string, len(current))
	for _, peer := range current {
		actual[peer.PublicKey] = sortedCopy(peer.AllowedIPs)
	}
	diff := DiffSummary{Status: "available", ProjectedPeers: len(expected), CurrentPeers: len(actual)}
	for key, routes := range expected {
		currentRoutes, ok := actual[key]
		if !ok {
			diff.MissingPeers++
			continue
		}
		if strings.Join(routes, ",") != strings.Join(currentRoutes, ",") {
			diff.RouteMismatches++
		}
	}
	for key := range actual {
		if _, ok := expected[key]; !ok {
			diff.UnexpectedPeers++
		}
	}
	diff.Equal = diff.MissingPeers == 0 && diff.UnexpectedPeers == 0 && diff.RouteMismatches == 0
	return diff
}

func UnavailableDiff(projected int) DiffSummary {
	return DiffSummary{Status: "unavailable", ProjectedPeers: projected}
}

func splitAndSort(value string) []string {
	if value == "" || value == "(none)" {
		return nil
	}
	return sortedCopy(strings.Split(value, ","))
}

func sortedCopy(values []string) []string {
	copyOf := append([]string(nil), values...)
	sort.Strings(copyOf)
	return copyOf
}
