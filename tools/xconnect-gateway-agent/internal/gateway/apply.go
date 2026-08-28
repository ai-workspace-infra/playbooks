package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
)

type RuntimeApplier interface {
	Recover(context.Context, Checkpoint) error
	Apply(context.Context, GatewaySnapshot, ACLArtifact) (DiffSummary, error)
	Commit(context.Context, uint64, string) error
	Abort(context.Context, uint64, string) error
}

var ErrRuntimeRollbackFailed = errors.New("runtime rollback failed")
var ErrRuntimeQuarantined = errors.New("runtime quarantined")
var ErrRuntimeUnsafe = errors.New("runtime safety could not be verified")

type RuntimeTransaction struct {
	Config           Config
	Runner           CommandRunner
	Reader           WireGuardReader
	DialContext      func(context.Context, string, string) (net.Conn, error)
	recoveryMu       sync.Mutex
	recoveryComplete bool
}

type runtimeJournal struct {
	SchemaVersion int    `json:"schema_version"`
	SnapshotID    string `json:"snapshot_id"`
	Generation    uint64 `json:"generation"`
	Stage         string `json:"stage"`
	Directory     string `json:"directory"`
	HadNFTTable   bool   `json:"had_nft_table"`
	HadXrayConfig bool   `json:"had_xray_config"`
	Quarantined   bool   `json:"quarantined,omitempty"`
}

func RenderWireGuardSyncConf(snapshot GatewaySnapshot) ([]byte, error) {
	var output strings.Builder
	output.WriteString("[Interface]\n")
	output.WriteString(fmt.Sprintf("ListenPort = %d\n", snapshot.WireGuard.ListenPort))
	peers := append([]GatewayPeer(nil), snapshot.WireGuard.Peers...)
	sort.Slice(peers, func(i, j int) bool { return peers[i].PublicKey < peers[j].PublicKey })
	for _, peer := range peers {
		output.WriteString("\n[Peer]\nPublicKey = ")
		output.WriteString(peer.PublicKey)
		output.WriteString("\nAllowedIPs = ")
		allowed := append([]string(nil), peer.AllowedIPs...)
		sort.Strings(allowed)
		output.WriteString(strings.Join(allowed, ", "))
		if peer.PersistentKeepaliveSeconds > 0 {
			output.WriteString(fmt.Sprintf("\nPersistentKeepalive = %d", peer.PersistentKeepaliveSeconds))
		}
		output.WriteString("\n")
	}
	raw := []byte(output.String())
	if bytes.Contains(bytes.ToLower(raw), []byte("privatekey")) || bytes.Contains(bytes.ToLower(raw), []byte("presharedkey")) {
		return nil, errors.New("rendered WireGuard syncconf contains secret key material")
	}
	return raw, nil
}

func RenderXrayRelayPlan(snapshot GatewaySnapshot) ([]byte, error) {
	// Snapshot relay credentials are references. The existing Ansible-managed
	// Xray config remains the credential owner; this plan is safe to retain and
	// is validated alongside `xray run -test` without copying raw UUIDs.
	plan := struct {
		SchemaVersion  int      `json:"schema_version"`
		ProxyCore      string   `json:"proxy_core"`
		Transport      string   `json:"transport"`
		ListenHost     string   `json:"listen_host"`
		ListenPort     int      `json:"listen_port"`
		ServerNames    []string `json:"server_names"`
		CredentialRefs []string `json:"credential_refs"`
	}{1, snapshot.ProxyCore, snapshot.Relay.Transport, snapshot.Relay.ListenHost, snapshot.Relay.ListenPort, snapshot.Relay.ServerNames, snapshot.Relay.CredentialRefs}
	return json.Marshal(plan)
}

func (t *RuntimeTransaction) Apply(ctx context.Context, snapshot GatewaySnapshot, artifact ACLArtifact) (DiffSummary, error) {
	if snapshot.WireGuard.InterfaceName != t.Config.Runtime.WireGuardInterface || snapshot.WireGuard.ListenPort != t.Config.Runtime.WireGuardListenPort || snapshot.Relay.ListenHost != t.Config.Runtime.RelayListenHost || snapshot.Relay.ListenPort != t.Config.Runtime.RelayListenPort {
		return DiffSummary{}, errors.New("signed snapshot runtime binding does not match this gateway node")
	}
	if !sortedUniqueStrings(snapshot.WireGuard.Addresses) || !sortedUniqueStrings(t.Config.Runtime.WireGuardAddresses) || strings.Join(snapshot.WireGuard.Addresses, "\x00") != strings.Join(t.Config.Runtime.WireGuardAddresses, "\x00") {
		return DiffSummary{}, errors.New("signed snapshot WireGuard addresses do not match the bootstrap binding")
	}
	lock, err := t.lock()
	if err != nil {
		return DiffSummary{}, err
	}
	defer unlockRuntime(lock)
	// Re-read the immutable bootstrap binding while holding the cross-process
	// transaction lock so an address change cannot race preflight and mutation.
	if err := t.verifyInterfaceAddresses(ctx); err != nil {
		return DiffSummary{}, err
	}
	if err := t.recoverLocked(ctx); err != nil {
		return DiffSummary{}, fmt.Errorf("recover previous runtime transaction: %w", err)
	}
	directory := filepath.Join(t.Config.Apply.TransactionDir, fmt.Sprintf("%020d-%s", snapshot.Generation, snapshot.SnapshotID))
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return DiffSummary{}, errors.New("create runtime transaction directory")
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		return DiffSummary{}, errors.New("secure runtime transaction directory")
	}
	wgRaw, err := RenderWireGuardSyncConf(snapshot)
	if err != nil {
		return DiffSummary{}, err
	}
	nftRaw, err := RenderNFTables(snapshot, artifact)
	if err != nil {
		return DiffSummary{}, err
	}
	xrayPlan, err := RenderXrayRelayPlan(snapshot)
	if err != nil {
		return DiffSummary{}, err
	}
	wgCandidate := filepath.Join(directory, "wireguard-syncconf.candidate")
	nftCandidate := filepath.Join(directory, "nftables.candidate")
	xrayPlanCandidate := filepath.Join(directory, "xray-relay-plan.candidate.json")
	xrayCandidate := filepath.Join(directory, "xray-runtime.candidate.json")
	xrayRuntime, err := RenderXrayRelayConfig(snapshot, t.Config.Runtime.XrayInboundTag, CredentialResolver{Directory: t.Config.Runtime.RelayCredentialDir})
	if err != nil {
		return DiffSummary{}, err
	}
	for path, raw := range map[string][]byte{wgCandidate: wgRaw, nftCandidate: nftRaw, xrayPlanCandidate: xrayPlan, xrayCandidate: xrayRuntime} {
		if err := atomicWrite(path, raw); err != nil {
			return DiffSummary{}, errors.New("write runtime candidate")
		}
	}

	hadNFT, nftBackup, err := t.captureNFT(ctx)
	if err != nil {
		return DiffSummary{}, err
	}
	wgBackupRaw, err := t.Runner.Run(ctx, t.Config.Runtime.WireGuardBinary, "showconf", t.Config.Runtime.WireGuardInterface)
	if err != nil {
		return DiffSummary{}, errors.New("capture WireGuard peer state")
	}
	wgBackup, err := sanitizeWireGuardBackup(wgBackupRaw)
	if err != nil {
		return DiffSummary{}, err
	}
	wgBackupPath := filepath.Join(directory, "wireguard.previous")
	nftBackupPath := filepath.Join(directory, "nftables.previous")
	xrayBackupPath := filepath.Join(directory, "xray-runtime.previous.json")
	xrayLKG := filepath.Join(t.Config.Apply.RuntimeSecretLKG, "xray-runtime.json")
	hadXray := false
	if _, statErr := os.Stat(xrayLKG); statErr == nil {
		hadXray = true
		if err := copyProtected(xrayLKG, xrayBackupPath); err != nil {
			return DiffSummary{}, errors.New("capture protected Xray rollback state")
		}
	} else if !errors.Is(statErr, os.ErrNotExist) {
		return DiffSummary{}, errors.New("inspect protected Xray rollback state")
	}
	if !hadXray {
		return DiffSummary{}, errors.New("runtime apply requires a protected seeded Xray baseline")
	}
	if err := atomicWrite(wgBackupPath, wgBackup); err != nil {
		return DiffSummary{}, errors.New("write WireGuard rollback state")
	}
	if hadNFT {
		if err := atomicWrite(nftBackupPath, nftBackup); err != nil {
			return DiffSummary{}, errors.New("write nftables rollback state")
		}
	}
	applyNFT := nftRaw
	if hadNFT {
		applyNFT = append([]byte("delete table inet xconnect_one\n"), nftRaw...)
	}
	if err := atomicWrite(nftCandidate, applyNFT); err != nil {
		return DiffSummary{}, errors.New("write atomic nftables candidate")
	}
	if _, err := t.Runner.Run(ctx, t.Config.Runtime.NFTablesBinary, "--check", "-f", nftCandidate); err != nil {
		return DiffSummary{}, errors.New("nftables candidate preflight failed")
	}
	if err := t.xrayPreflight(ctx, xrayCandidate); err != nil {
		return DiffSummary{}, errors.New("Xray candidate preflight failed")
	}
	journal := runtimeJournal{SchemaVersion: 1, SnapshotID: snapshot.SnapshotID, Generation: snapshot.Generation, Stage: "backed_up", Directory: directory, HadNFTTable: hadNFT, HadXrayConfig: hadXray}
	if err := t.saveJournal(journal); err != nil {
		return DiffSummary{}, err
	}
	journal.Stage = "acl_mutating"
	if err := t.saveJournal(journal); err != nil {
		return DiffSummary{}, err
	}
	if _, err := t.Runner.Run(ctx, t.Config.Runtime.NFTablesBinary, "-f", nftCandidate); err != nil {
		return DiffSummary{}, t.failWithRollback(ctx, journal, "nftables apply failed")
	}
	journal.Stage = "acl_applied"
	if err := t.saveJournal(journal); err != nil {
		return DiffSummary{}, t.failWithRollback(ctx, journal, "persist ACL transaction stage")
	}
	journal.Stage = "relay_mutating"
	if err := t.saveJournal(journal); err != nil {
		return DiffSummary{}, t.failWithRollback(ctx, journal, "persist Xray mutation stage")
	}
	if err := t.xrayApply(ctx, xrayCandidate, hadXray); err != nil {
		return DiffSummary{}, t.failWithRollback(ctx, journal, "Xray relay apply failed")
	}
	journal.Stage = "relay_applied"
	if err := t.saveJournal(journal); err != nil {
		return DiffSummary{}, t.failWithRollback(ctx, journal, "persist Xray transaction stage")
	}
	// ACL is committed before WireGuard for both additions and removals. A new
	// peer cannot transmit until its allow rule exists; a revoked peer is denied
	// before it is removed. The inverse order is used for rollback.
	journal.Stage = "wg_mutating"
	if err := t.saveJournal(journal); err != nil {
		return DiffSummary{}, t.failWithRollback(ctx, journal, "persist WireGuard mutation stage")
	}
	if _, err := t.Runner.Run(ctx, t.Config.Runtime.WireGuardBinary, "syncconf", t.Config.Runtime.WireGuardInterface, wgCandidate); err != nil {
		return DiffSummary{}, t.failWithRollback(ctx, journal, "WireGuard apply failed")
	}
	journal.Stage = "wg_applied"
	if err := t.saveJournal(journal); err != nil {
		return DiffSummary{}, t.failWithRollback(ctx, journal, "persist WireGuard transaction stage")
	}
	var diff DiffSummary
	for attempt := 0; attempt < t.Config.Apply.ReadbackRetries; attempt++ {
		current, readErr := t.Reader.Peers(ctx, t.Config.Runtime.WireGuardInterface)
		if readErr == nil {
			diff = ComparePeers(snapshot.WireGuard.Peers, current)
			if diff.Equal {
				break
			}
		}
	}
	if !diff.Equal {
		if err := t.rollbackLocked(ctx, journal); err != nil {
			return diff, t.rollbackFailure(ctx, journal, fmt.Errorf("runtime readback: %w", err))
		}
		return diff, errors.New("runtime readback failed and rollback completed")
	}
	if _, err := t.Runner.Run(ctx, t.Config.Runtime.NFTablesBinary, "list", "table", "inet", "xconnect_one"); err != nil {
		return diff, t.failWithRollback(ctx, journal, "nftables readback failed")
	}
	if err := t.xrayReadback(ctx, xrayCandidate); err != nil {
		return diff, t.failWithRollback(ctx, journal, "Xray health validation failed")
	}
	journal.Stage = "verified"
	if err := t.saveJournal(journal); err != nil {
		return diff, t.failWithRollback(ctx, journal, "persist verified runtime transaction")
	}
	return diff, nil
}

func (t *RuntimeTransaction) verifyInterfaceAddresses(ctx context.Context) error {
	raw, err := t.Runner.Run(ctx, t.Config.Runtime.IPBinary, "-json", "addr", "show", "dev", t.Config.Runtime.WireGuardInterface)
	if err != nil {
		return errors.New("read WireGuard interface addresses")
	}
	var links []struct {
		AddressInfo []struct {
			Local        string `json:"local"`
			PrefixLength int    `json:"prefixlen"`
		} `json:"addr_info"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if err := decoder.Decode(&links); err != nil || len(links) != 1 {
		return errors.New("decode WireGuard interface addresses")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("decode WireGuard interface addresses")
	}
	observed := make([]string, 0, len(links[0].AddressInfo))
	for _, address := range links[0].AddressInfo {
		observed = append(observed, fmt.Sprintf("%s/%d", address.Local, address.PrefixLength))
	}
	sort.Strings(observed)
	if strings.Join(observed, "\x00") != strings.Join(t.Config.Runtime.WireGuardAddresses, "\x00") {
		return errors.New("live WireGuard interface addresses differ from bootstrap binding")
	}
	return nil
}

func (t *RuntimeTransaction) failWithRollback(ctx context.Context, journal runtimeJournal, cause string) error {
	if err := t.rollbackLocked(ctx, journal); err != nil {
		return t.rollbackFailure(ctx, journal, fmt.Errorf("%s: %w", cause, err))
	}
	return fmt.Errorf("%s; rollback completed", cause)
}

func (t *RuntimeTransaction) rollbackFailure(ctx context.Context, journal runtimeJournal, cause error) error {
	if err := t.quarantine(ctx); err != nil {
		return errors.Join(ErrRuntimeRollbackFailed, ErrRuntimeUnsafe, cause, err)
	}
	journal.Quarantined = true
	_ = t.saveJournal(journal)
	return errors.Join(ErrRuntimeRollbackFailed, ErrRuntimeQuarantined, cause)
}

func (t *RuntimeTransaction) quarantine(ctx context.Context) error {
	if _, err := t.Runner.Run(ctx, t.Config.Runtime.IPBinary, "link", "set", "dev", t.Config.Runtime.WireGuardInterface, "down"); err != nil {
		return errors.New("emergency WireGuard isolation failed")
	}
	if err := VerifyInterfaceState(ctx, t.Runner, t.Config.Runtime.IPBinary, t.Config.Runtime.WireGuardInterface, "DOWN"); err != nil {
		return errors.New("emergency WireGuard isolation was not verified")
	}
	return nil
}

func VerifyInterfaceState(ctx context.Context, runner CommandRunner, binary, interfaceName, expected string) error {
	raw, err := runner.Run(ctx, binary, "-json", "link", "show", "dev", interfaceName)
	if err != nil {
		return err
	}
	var links []struct {
		OperState string   `json:"operstate"`
		Flags     []string `json:"flags"`
	}
	if err := json.Unmarshal(raw, &links); err != nil || len(links) != 1 {
		return errors.New("interface state readback mismatch")
	}
	adminUp := false
	for _, flag := range links[0].Flags {
		adminUp = adminUp || strings.EqualFold(flag, "UP")
	}
	if strings.EqualFold(expected, "UP") && !adminUp {
		return errors.New("interface is not administratively up")
	}
	if strings.EqualFold(expected, "DOWN") && (adminUp || !strings.EqualFold(links[0].OperState, "DOWN")) {
		return errors.New("interface is not down")
	}
	return nil
}

func (t *RuntimeTransaction) Recover(ctx context.Context, checkpoint Checkpoint) error {
	t.recoveryMu.Lock()
	defer t.recoveryMu.Unlock()
	if t.recoveryComplete {
		return nil
	}
	lock, err := t.lock()
	if err != nil {
		return err
	}
	defer unlockRuntime(lock)
	journal, err := t.loadJournal()
	if errors.Is(err, os.ErrNotExist) {
		baseline := filepath.Join(t.Config.Apply.RuntimeSecretLKG, "xray-runtime.json")
		// Deterministically reconcile the protected inbound rather than trusting
		// process liveness or a stale stats counter. Remove-not-found is harmless
		// only when the subsequent add and exact tag/listen checks both succeed.
		if err := t.xrayApply(ctx, baseline, true); err != nil {
			return errors.New("reconcile protected Xray inbound after dedicated runtime restart")
		}
		if err := t.xrayReadback(ctx, baseline); err != nil {
			return errors.New("verify restored Xray inbound after dedicated runtime restart")
		}
		t.recoveryComplete = true
		return nil
	}
	if err != nil {
		return err
	}
	if journal.Stage == "verified" && checkpoint.AppliedGeneration == journal.Generation && checkpoint.ObservedSnapshotID == journal.SnapshotID {
		err = t.commitLocked(journal)
	} else {
		err = t.rollbackLocked(ctx, journal)
		if err != nil {
			return t.rollbackFailure(ctx, journal, err)
		}
	}
	if err == nil {
		t.recoveryComplete = true
	}
	return err
}

func (t *RuntimeTransaction) Commit(_ context.Context, generation uint64, snapshotID string) error {
	lock, err := t.lock()
	if err != nil {
		return err
	}
	defer unlockRuntime(lock)
	journal, err := t.loadJournal()
	if err != nil {
		return err
	}
	if journal.Stage != "verified" || journal.Generation != generation || journal.SnapshotID != snapshotID {
		return errors.New("runtime commit does not match verified transaction")
	}
	return t.commitLocked(journal)
}

func (t *RuntimeTransaction) Abort(ctx context.Context, generation uint64, snapshotID string) error {
	lock, err := t.lock()
	if err != nil {
		return err
	}
	defer unlockRuntime(lock)
	journal, err := t.loadJournal()
	if err != nil {
		return err
	}
	if journal.Generation != generation || journal.SnapshotID != snapshotID {
		return errors.New("runtime abort does not match active transaction")
	}
	if err := t.rollbackLocked(ctx, journal); err != nil {
		return t.rollbackFailure(ctx, journal, err)
	}
	return nil
}

func (t *RuntimeTransaction) commitLocked(journal runtimeJournal) error {
	if err := os.MkdirAll(t.Config.Apply.RuntimeLastKnownGood, 0o700); err != nil {
		return errors.New("create runtime LKG directory")
	}
	if err := os.Chmod(t.Config.Apply.RuntimeLastKnownGood, 0o700); err != nil {
		return errors.New("secure runtime LKG directory")
	}
	for name, source := range map[string]string{
		"wireguard-syncconf":   "wireguard-syncconf.candidate",
		"nftables-ruleset":     "nftables.candidate",
		"xray-relay-plan.json": "xray-relay-plan.candidate.json",
	} {
		raw, err := os.ReadFile(filepath.Join(journal.Directory, source))
		if err != nil {
			return errors.New("read verified runtime candidate")
		}
		if name == "nftables-ruleset" {
			raw = bytes.TrimPrefix(raw, []byte("delete table inet xconnect_one\n"))
		}
		if err := atomicWrite(filepath.Join(t.Config.Apply.RuntimeLastKnownGood, name), raw); err != nil {
			return errors.New("commit runtime LKG")
		}
	}
	if err := copyProtected(filepath.Join(journal.Directory, "xray-runtime.candidate.json"), filepath.Join(t.Config.Apply.RuntimeSecretLKG, "xray-runtime.json")); err != nil {
		return errors.New("commit protected Xray runtime LKG")
	}
	if err := os.Remove(t.journalPath()); err != nil && !errors.Is(err, os.ErrNotExist) {
		return errors.New("clear committed runtime transaction journal")
	}
	return nil
}

func (t *RuntimeTransaction) recoverLocked(ctx context.Context) error {
	journal, err := t.loadJournal()
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	return t.rollbackLocked(ctx, journal)
}

func (t *RuntimeTransaction) rollbackLocked(ctx context.Context, journal runtimeJournal) error {
	var failures []string
	if journal.Stage == "wg_applied" || journal.Stage == "verified" || journal.Stage == "wg_mutating" {
		if _, err := t.Runner.Run(ctx, t.Config.Runtime.WireGuardBinary, "syncconf", t.Config.Runtime.WireGuardInterface, filepath.Join(journal.Directory, "wireguard.previous")); err != nil {
			failures = append(failures, "wireguard")
		}
	}
	if journal.Stage == "wg_applied" || journal.Stage == "verified" || journal.Stage == "wg_mutating" || journal.Stage == "relay_applied" || journal.Stage == "relay_mutating" {
		if err := t.xrayRollback(ctx, filepath.Join(journal.Directory, "xray-runtime.previous.json"), journal.HadXrayConfig); err != nil {
			failures = append(failures, "xray")
		}
	}
	if journal.Stage == "wg_applied" || journal.Stage == "verified" || journal.Stage == "wg_mutating" || journal.Stage == "relay_applied" || journal.Stage == "relay_mutating" || journal.Stage == "acl_applied" || journal.Stage == "acl_mutating" {
		if journal.HadNFTTable {
			restore := filepath.Join(journal.Directory, "nftables.restore")
			previous, err := os.ReadFile(filepath.Join(journal.Directory, "nftables.previous"))
			if err == nil {
				err = atomicWrite(restore, append([]byte("delete table inet xconnect_one\n"), previous...))
			}
			if err == nil {
				_, err = t.Runner.Run(ctx, t.Config.Runtime.NFTablesBinary, "-f", restore)
			}
			if err != nil {
				failures = append(failures, "nftables")
			}
		} else {
			restore := filepath.Join(journal.Directory, "nftables.restore")
			err := atomicWrite(restore, []byte("delete table inet xconnect_one\n"))
			if err == nil {
				_, err = t.Runner.Run(ctx, t.Config.Runtime.NFTablesBinary, "-f", restore)
			}
			if err != nil {
				failures = append(failures, "nftables")
			}
		}
	}
	if len(failures) > 0 {
		return fmt.Errorf("runtime rollback failed for %s", strings.Join(failures, ","))
	}
	if err := os.Remove(t.journalPath()); err != nil && !errors.Is(err, os.ErrNotExist) {
		return errors.New("clear rolled-back transaction journal")
	}
	return nil
}

func (t *RuntimeTransaction) captureNFT(ctx context.Context) (bool, []byte, error) {
	tables, err := t.Runner.Run(ctx, t.Config.Runtime.NFTablesBinary, "list", "tables")
	if err != nil {
		return false, nil, errors.New("inspect nftables tables")
	}
	found := false
	for _, line := range strings.Split(string(tables), "\n") {
		found = found || strings.TrimSpace(line) == "table inet xconnect_one"
	}
	if !found {
		return false, nil, nil
	}
	previous, err := t.Runner.Run(ctx, t.Config.Runtime.NFTablesBinary, "list", "table", "inet", "xconnect_one")
	if err != nil {
		return false, nil, errors.New("capture nftables state")
	}
	return true, previous, nil
}

func sanitizeWireGuardBackup(raw []byte) ([]byte, error) {
	var output strings.Builder
	for _, line := range strings.Split(string(raw), "\n") {
		trimmed := strings.TrimSpace(line)
		lower := strings.ToLower(trimmed)
		if strings.HasPrefix(lower, "privatekey") {
			continue
		}
		if strings.HasPrefix(lower, "presharedkey") {
			parts := strings.SplitN(trimmed, "=", 2)
			if len(parts) != 2 || (strings.TrimSpace(parts[1]) != "" && strings.TrimSpace(parts[1]) != "(none)") {
				return nil, errors.New("runtime apply refuses to persist or replace a WireGuard preshared key")
			}
			continue
		}
		output.WriteString(line)
		output.WriteByte('\n')
	}
	return []byte(output.String()), nil
}

func (t *RuntimeTransaction) journalPath() string {
	return filepath.Join(t.Config.Apply.RuntimeLastKnownGood, "runtime-transaction.json")
}

func (t *RuntimeTransaction) saveJournal(journal runtimeJournal) error {
	if err := os.MkdirAll(t.Config.Apply.RuntimeLastKnownGood, 0o700); err != nil {
		return errors.New("create runtime LKG directory")
	}
	raw, err := json.Marshal(journal)
	if err != nil {
		return err
	}
	return atomicWrite(t.journalPath(), raw)
}

func (t *RuntimeTransaction) loadJournal() (runtimeJournal, error) {
	raw, err := os.ReadFile(t.journalPath())
	if err != nil {
		return runtimeJournal{}, err
	}
	var journal runtimeJournal
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&journal); err != nil {
		return runtimeJournal{}, errors.New("decode runtime transaction journal")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) || journal.SchemaVersion != 1 || !idPattern.MatchString(journal.SnapshotID) || journal.Generation == 0 || !filepath.IsAbs(journal.Directory) {
		return runtimeJournal{}, errors.New("invalid runtime transaction journal")
	}
	base, _ := filepath.Abs(t.Config.Apply.TransactionDir)
	candidate, _ := filepath.Abs(journal.Directory)
	if candidate != base && !strings.HasPrefix(candidate, base+string(os.PathSeparator)) {
		return runtimeJournal{}, errors.New("runtime transaction journal escaped configured directory")
	}
	return journal, nil
}

func (t *RuntimeTransaction) lock() (*os.File, error) {
	if err := os.MkdirAll(filepath.Dir(t.Config.Apply.LockFile), 0o750); err != nil {
		return nil, errors.New("create runtime lock directory")
	}
	file, err := os.OpenFile(t.Config.Apply.LockFile, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, errors.New("open runtime apply lock")
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		file.Close()
		return nil, errors.New("runtime apply transaction is already active")
	}
	return file, nil
}

func unlockRuntime(file *os.File) {
	_ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
	_ = file.Close()
}
