package gateway

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"sync"
	"time"
)

type HealthState struct {
	Status              string      `json:"status"`
	Mode                string      `json:"mode"`
	ProxyCore           string      `json:"proxy_core"`
	RuntimeApplyEnabled bool        `json:"runtime_apply_enabled"`
	ProjectionSource    string      `json:"projection_source"`
	ObservedGeneration  uint64      `json:"observed_generation"`
	AppliedGeneration   uint64      `json:"applied_generation"`
	ControllerStatus    string      `json:"controller_status"`
	Diff                DiffSummary `json:"diff"`
	LastEventCode       string      `json:"last_event_code,omitempty"`
	UpdatedAt           time.Time   `json:"updated_at"`
}

type Agent struct {
	Config     Config
	Controller Controller
	Policy     PolicyProvider
	Store      *Store
	WireGuard  WireGuardReader
	Runtime    RuntimeApplier
	PublicKey  ed25519.PublicKey
	Version    string
	Logger     *slog.Logger
	Now        func() time.Time

	mu     sync.RWMutex
	health HealthState
}

func (a *Agent) initialize() {
	if a.Now == nil {
		a.Now = time.Now
	}
	if a.Logger == nil {
		a.Logger = slog.New(slog.NewJSONHandler(ioDiscard{}, nil))
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.health.Status == "" {
		a.health = HealthState{Status: "ready", Mode: a.Config.Mode, ProxyCore: "xray", RuntimeApplyEnabled: a.Config.RuntimeApplyEnabled(), ProjectionSource: a.Config.Authority.ProjectionSource, ControllerStatus: "unknown", Diff: UnavailableDiff(0), UpdatedAt: a.Now().UTC()}
	}
}

func (a *Agent) Run(ctx context.Context) error {
	a.initialize()
	listener, err := net.Listen("tcp", a.Config.HealthAddress())
	if err != nil {
		return errors.New("start loopback health listener")
	}
	mux := http.NewServeMux()
	mux.HandleFunc(a.Config.Health.Path, a.healthHandler)
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second}
	serveDone := make(chan error, 1)
	go func() {
		err := server.Serve(listener)
		if !errors.Is(err, http.ErrServerClosed) {
			serveDone <- err
			return
		}
		serveDone <- nil
	}()

	a.runCycle(ctx)
	ticker := time.NewTicker(a.Config.PollInterval())
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := server.Shutdown(shutdownCtx); err != nil {
				return errors.New("shutdown health server")
			}
			return <-serveDone
		case err := <-serveDone:
			return err
		case <-ticker.C:
			a.runCycle(ctx)
		}
	}
}

func (a *Agent) RunCycle(ctx context.Context) {
	a.initialize()
	a.runCycle(ctx)
}

func (a *Agent) runCycle(ctx context.Context) {
	checkpoint, err := a.Store.LoadCheckpoint()
	if err != nil {
		a.recordEvent("checkpoint_read_failed", "error")
		return
	}
	if a.Config.RuntimeApplyEnabled() && !checkpoint.RuntimeFault {
		if a.Runtime == nil || a.Policy == nil {
			a.recordEvent("runtime_apply_dependency_missing", "error")
			return
		}
		if err := a.Runtime.Recover(ctx, checkpoint); err != nil {
			a.recordEvent("runtime_recovery_failed", "error")
			return
		}
	}
	reportedObserved := checkpoint.ObservedGeneration
	if checkpoint.RuntimeFaultGeneration > reportedObserved {
		reportedObserved = checkpoint.RuntimeFaultGeneration
	}
	a.setHealth(func(state *HealthState) {
		state.ObservedGeneration = reportedObserved
		state.AppliedGeneration = checkpoint.AppliedGeneration
		if checkpoint.RuntimeFault {
			state.Status = "unsafe-manual-recovery"
			if checkpoint.RuntimeQuarantined {
				state.Status = "fail-closed"
			}
			state.LastEventCode = "apply_failed_rollback_failed"
		}
	})
	heartbeat := Heartbeat{NodeID: a.Config.NodeID, AgentVersion: a.Version, Mode: a.Config.Mode, ProxyCore: "xray", ObservedGeneration: reportedObserved, AppliedGeneration: checkpoint.AppliedGeneration}
	if err := a.Controller.Heartbeat(ctx, heartbeat); err != nil {
		a.recordEvent("heartbeat_failed", "error")
	} else {
		a.recordEvent("heartbeat_ok", "ok")
	}
	if checkpoint.PendingResult != nil {
		pending := *checkpoint.PendingResult
		if err := a.Controller.ReportApplyResult(ctx, pending); err != nil {
			a.recordEvent("shadow_result_retry_failed", "error")
			return
		}
		if err := a.Store.MarkResultReported(checkpoint, pending); err != nil {
			a.recordEvent("shadow_result_checkpoint_failed", "error")
			return
		}
		checkpoint.PendingResult = nil
		checkpoint.LastReportedSnapshotID = pending.SnapshotID
		checkpoint.LastReportedObservedGeneration = pending.ObservedGeneration
		checkpoint.LastReportedResult = pending.Result
		a.recordEvent("shadow_result_retry_ok", "ok")
	}
	if checkpoint.RuntimeFault {
		a.recordEvent("runtime_manual_recovery_required", "error")
		return
	}
	raw, err := a.Controller.PlannedSnapshot(ctx, a.Config.NodeID)
	if errors.Is(err, ErrNoPlannedSnapshot) {
		a.recordEvent("no_planned_snapshot", "ok")
		return
	}
	if err != nil {
		a.recordEvent("planned_snapshot_failed", "error")
		return
	}
	snapshot, err := DecodeGatewaySnapshot(raw)
	if err != nil {
		a.recordEvent("snapshot_decode_rejected", "ok")
		return
	}
	if err := a.Store.SaveCandidate(raw); err != nil {
		a.recordEvent("candidate_write_failed", "error")
		return
	}
	previous, err := a.Store.LoadLastKnownGood()
	if err != nil {
		a.recordEvent("lkg_read_failed", "error")
		return
	}
	if err := snapshot.Validate(a.Now(), a.Config.NodeID, a.Config.ControlPlane.SnapshotSigningKeyID, a.PublicKey, previous); err != nil {
		resultName := "shadow_rejected"
		if a.Config.RuntimeApplyEnabled() {
			resultName = "apply_rejected"
		}
		result := newResult(a.Config.NodeID, snapshot, checkpoint.AppliedGeneration, false, resultName, UnavailableDiff(len(snapshot.WireGuard.Peers)))
		if checkpoint.LastReportedSnapshotID == result.SnapshotID && checkpoint.LastReportedObservedGeneration == result.ObservedGeneration && checkpoint.LastReportedResult == result.Result {
			a.recordEvent("snapshot_rejection_already_reported", "ok")
			return
		}
		if !a.reportResult(ctx, result) {
			if queueErr := a.Store.QueueResult(checkpoint, result); queueErr != nil {
				a.recordEvent("shadow_result_checkpoint_failed", "error")
			}
		} else if markErr := a.Store.MarkResultReported(checkpoint, result); markErr != nil {
			a.recordEvent("shadow_result_checkpoint_failed", "error")
		}
		a.recordEvent("snapshot_validation_rejected", "ok")
		return
	}
	if a.Config.RuntimeApplyEnabled() {
		a.runApply(ctx, raw, snapshot, checkpoint)
		return
	}
	if checkpoint.ObservedGeneration == snapshot.Generation && checkpoint.ObservedSnapshotID == snapshot.SnapshotID {
		a.recordEvent("snapshot_already_observed", "ok")
		return
	}
	current, wgErr := a.WireGuard.Peers(ctx, a.Config.Runtime.WireGuardInterface)
	diff := UnavailableDiff(len(snapshot.WireGuard.Peers))
	result := "shadow_validated"
	if wgErr == nil {
		diff = ComparePeers(snapshot.WireGuard.Peers, current)
	} else {
		result = "shadow_validated_wg_unavailable"
	}
	if err := a.Store.SaveEvidence(diff, snapshot.Generation); err != nil {
		a.recordEvent("evidence_write_failed", "error")
		return
	}
	shadowResult := newShadowResult(a.Config.NodeID, snapshot, result, diff)
	if err := a.Store.CommitObserved(raw, snapshot, shadowResult); err != nil {
		a.recordEvent("checkpoint_commit_failed", "error")
		return
	}
	checkpoint = Checkpoint{ObservedGeneration: snapshot.Generation, ObservedSnapshotID: snapshot.SnapshotID, AppliedGeneration: 0, PendingResult: &shadowResult}
	if a.reportResult(ctx, shadowResult) {
		if err := a.Store.MarkResultReported(checkpoint, shadowResult); err != nil {
			a.recordEvent("shadow_result_checkpoint_failed", "error")
		}
	}
	a.setHealth(func(state *HealthState) {
		state.ObservedGeneration = snapshot.Generation
		state.AppliedGeneration = 0
		state.Diff = diff
		state.LastEventCode = result
	})
}

func newShadowResult(nodeID string, snapshot GatewaySnapshot, result string, diff DiffSummary) ApplyResult {
	return newResult(nodeID, snapshot, 0, false, result, diff)
}

func newResult(nodeID string, snapshot GatewaySnapshot, applied uint64, runtimeApplied bool, result string, diff DiffSummary) ApplyResult {
	return ApplyResult{NodeID: nodeID, SnapshotID: snapshot.SnapshotID, ObservedGeneration: snapshot.Generation, AppliedGeneration: applied, RuntimeApplied: runtimeApplied, Result: result, Diff: diff}
}

func (a *Agent) runApply(ctx context.Context, raw []byte, snapshot GatewaySnapshot, checkpoint Checkpoint) {
	if checkpoint.AppliedGeneration == snapshot.Generation && checkpoint.ObservedSnapshotID == snapshot.SnapshotID {
		a.recordEvent("snapshot_already_applied", "ok")
		return
	}
	policyRaw, err := a.Policy.PolicyArtifact(ctx, a.Config.NodeID, snapshot.Policy.Generation, snapshot.Policy.RulesetSHA256)
	if err != nil {
		a.queueApplyFailure(ctx, checkpoint, snapshot, "apply_rejected", UnavailableDiff(len(snapshot.WireGuard.Peers)), false)
		a.recordEvent("policy_artifact_fetch_failed", "error")
		return
	}
	artifact, err := ValidatePolicyArtifact(policyRaw, snapshot)
	if err != nil {
		a.queueApplyFailure(ctx, checkpoint, snapshot, "apply_rejected", UnavailableDiff(len(snapshot.WireGuard.Peers)), false)
		a.recordEvent("policy_artifact_rejected", "error")
		return
	}
	diff, err := a.Runtime.Apply(ctx, snapshot, artifact)
	if err != nil {
		resultName := "apply_failed_rolled_back"
		if errors.Is(err, ErrRuntimeRollbackFailed) {
			resultName = "apply_failed_rollback_failed"
		}
		a.queueApplyFailure(ctx, checkpoint, snapshot, resultName, diff, errors.Is(err, ErrRuntimeQuarantined))
		return
	}
	result := newResult(a.Config.NodeID, snapshot, snapshot.Generation, true, "applied", diff)
	if err := a.Store.CommitApplied(raw, snapshot, result); err != nil {
		if abortErr := a.Runtime.Abort(ctx, snapshot.Generation, snapshot.SnapshotID); abortErr != nil {
			a.queueApplyFailure(ctx, checkpoint, snapshot, "apply_failed_rollback_failed", diff, errors.Is(abortErr, ErrRuntimeQuarantined))
		} else {
			a.queueApplyFailure(ctx, checkpoint, snapshot, "apply_failed_rolled_back", diff, false)
		}
		return
	}
	if err := a.Runtime.Commit(ctx, snapshot.Generation, snapshot.SnapshotID); err != nil {
		a.recordEvent("runtime_lkg_commit_failed", "error")
		return
	}
	committed := Checkpoint{ObservedGeneration: snapshot.Generation, ObservedSnapshotID: snapshot.SnapshotID, AppliedGeneration: snapshot.Generation, PendingResult: &result}
	if a.reportResult(ctx, result) {
		if err := a.Store.MarkResultReported(committed, result); err != nil {
			a.recordEvent("apply_result_checkpoint_failed", "error")
		}
	}
	a.setHealth(func(state *HealthState) {
		state.ObservedGeneration = snapshot.Generation
		state.AppliedGeneration = snapshot.Generation
		state.Diff = diff
		state.LastEventCode = "applied"
	})
}

func (a *Agent) queueApplyFailure(ctx context.Context, checkpoint Checkpoint, snapshot GatewaySnapshot, resultName string, diff DiffSummary, quarantined bool) {
	result := newResult(a.Config.NodeID, snapshot, checkpoint.AppliedGeneration, false, resultName, diff)
	if resultName == "apply_failed_rollback_failed" {
		checkpoint.RuntimeFault = true
		checkpoint.RuntimeFaultSnapshotID = snapshot.SnapshotID
		checkpoint.RuntimeFaultGeneration = snapshot.Generation
		checkpoint.RuntimeQuarantined = quarantined
	}
	if checkpoint.LastReportedSnapshotID == result.SnapshotID && checkpoint.LastReportedObservedGeneration == result.ObservedGeneration && checkpoint.LastReportedResult == result.Result {
		a.recordEvent(resultName+"_already_reported", "ok")
		return
	}
	if !a.reportResult(ctx, result) {
		if err := a.Store.QueueResult(checkpoint, result); err != nil {
			a.recordEvent("apply_result_checkpoint_failed", "error")
		}
	} else if err := a.Store.MarkResultReported(checkpoint, result); err != nil {
		a.recordEvent("apply_result_checkpoint_failed", "error")
	}
	a.setHealth(func(state *HealthState) {
		if resultName == "apply_failed_rollback_failed" {
			state.Status = "unsafe-manual-recovery"
			if quarantined {
				state.Status = "fail-closed"
			}
		}
		state.ObservedGeneration = snapshot.Generation
		state.AppliedGeneration = checkpoint.AppliedGeneration
		state.Diff = diff
		state.LastEventCode = resultName
	})
	a.recordEvent(resultName, "error")
}

func (a *Agent) reportResult(ctx context.Context, result ApplyResult) bool {
	err := a.Controller.ReportApplyResult(ctx, result)
	if err != nil {
		a.recordEvent("apply_result_failed", "error")
		return false
	}
	return true
}

func (a *Agent) recordEvent(code, controllerStatus string) {
	a.Logger.Info("gateway event", "event_code", code)
	a.setHealth(func(state *HealthState) {
		state.LastEventCode = code
		state.ControllerStatus = controllerStatus
	})
}

func (a *Agent) setHealth(update func(*HealthState)) {
	a.mu.Lock()
	defer a.mu.Unlock()
	update(&a.health)
	a.health.UpdatedAt = a.Now().UTC()
}

func (a *Agent) Health() HealthState {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return a.health
}

func (a *Agent) healthHandler(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		response.Header().Set("Allow", http.MethodGet)
		http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	response.Header().Set("Content-Type", "application/json")
	response.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(response).Encode(a.Health())
}

type ioDiscard struct{}

func (ioDiscard) Write(value []byte) (int, error) { return len(value), nil }
