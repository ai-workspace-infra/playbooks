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
	Store      *Store
	WireGuard  WireGuardReader
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
		a.health = HealthState{Status: "ready", Mode: "shadow", ProxyCore: "xray", RuntimeApplyEnabled: false, ControllerStatus: "unknown", Diff: UnavailableDiff(0), UpdatedAt: a.Now().UTC()}
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
	a.setHealth(func(state *HealthState) {
		state.ObservedGeneration = checkpoint.ObservedGeneration
		state.AppliedGeneration = checkpoint.AppliedGeneration
	})
	heartbeat := Heartbeat{NodeID: a.Config.NodeID, AgentVersion: a.Version, Mode: "shadow", ProxyCore: "xray", ObservedGeneration: checkpoint.ObservedGeneration, AppliedGeneration: checkpoint.AppliedGeneration}
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
		result := newShadowResult(a.Config.NodeID, snapshot, "shadow_rejected", UnavailableDiff(len(snapshot.WireGuard.Peers)))
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
	return ApplyResult{NodeID: nodeID, SnapshotID: snapshot.SnapshotID, ObservedGeneration: snapshot.Generation, AppliedGeneration: 0, RuntimeApplied: false, Result: result, Diff: diff}
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
	a.Logger.Info("gateway shadow event", "event_code", code)
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
