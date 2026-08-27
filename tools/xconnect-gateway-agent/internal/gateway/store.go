package gateway

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

type Checkpoint struct {
	ObservedGeneration             uint64       `json:"observed_generation"`
	ObservedSnapshotID             string       `json:"observed_snapshot_id"`
	AppliedGeneration              uint64       `json:"applied_generation"`
	LastReportedSnapshotID         string       `json:"last_reported_snapshot_id,omitempty"`
	LastReportedObservedGeneration uint64       `json:"last_reported_observed_generation,omitempty"`
	LastReportedResult             string       `json:"last_reported_result,omitempty"`
	PendingResult                  *ApplyResult `json:"pending_result,omitempty"`
}

type Store struct {
	candidateDir string
	lkgDir       string
	evidenceDir  string
}

func NewStore(cfg SnapshotConfig) (*Store, error) {
	store := &Store{candidateDir: cfg.CandidateDir, lkgDir: cfg.LastKnownGoodDir, evidenceDir: cfg.EvidenceDir}
	for _, dir := range []string{store.candidateDir, store.lkgDir, store.evidenceDir} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return nil, fmt.Errorf("create state directory: %w", err)
		}
		if err := os.Chmod(dir, 0o700); err != nil {
			return nil, fmt.Errorf("secure state directory: %w", err)
		}
	}
	return store, nil
}

func (s *Store) LoadCheckpoint() (Checkpoint, error) {
	raw, err := os.ReadFile(filepath.Join(s.lkgDir, "checkpoint.json"))
	if errors.Is(err, os.ErrNotExist) {
		return Checkpoint{}, nil
	}
	if err != nil {
		return Checkpoint{}, fmt.Errorf("read checkpoint: %w", err)
	}
	var checkpoint Checkpoint
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&checkpoint); err != nil {
		return Checkpoint{}, fmt.Errorf("decode checkpoint: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return Checkpoint{}, errors.New("decode checkpoint: multiple JSON values")
	}
	return checkpoint, nil
}

func (s *Store) LoadLastKnownGood() (*GatewaySnapshot, error) {
	raw, err := os.ReadFile(filepath.Join(s.lkgDir, "gateway-snapshot.json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read last-known-good: %w", err)
	}
	snapshot, err := DecodeGatewaySnapshot(raw)
	if err != nil {
		return nil, fmt.Errorf("decode last-known-good: %w", err)
	}
	return &snapshot, nil
}

func (s *Store) SaveCandidate(raw []byte) error {
	return atomicWrite(filepath.Join(s.candidateDir, "gateway-snapshot.json"), raw)
}

func (s *Store) CommitObserved(raw []byte, snapshot GatewaySnapshot, pending ApplyResult) error {
	if err := atomicWrite(filepath.Join(s.lkgDir, "gateway-snapshot.json"), raw); err != nil {
		return err
	}
	checkpoint, err := json.Marshal(Checkpoint{
		ObservedGeneration: snapshot.Generation,
		ObservedSnapshotID: snapshot.SnapshotID,
		AppliedGeneration:  0,
		PendingResult:      &pending,
	})
	if err != nil {
		return err
	}
	return atomicWrite(filepath.Join(s.lkgDir, "checkpoint.json"), checkpoint)
}

func (s *Store) MarkResultReported(checkpoint Checkpoint, result ApplyResult) error {
	checkpoint.PendingResult = nil
	checkpoint.LastReportedSnapshotID = result.SnapshotID
	checkpoint.LastReportedObservedGeneration = result.ObservedGeneration
	checkpoint.LastReportedResult = result.Result
	return s.saveCheckpoint(checkpoint)
}

func (s *Store) QueueResult(checkpoint Checkpoint, pending ApplyResult) error {
	checkpoint.PendingResult = &pending
	return s.saveCheckpoint(checkpoint)
}

func (s *Store) saveCheckpoint(checkpoint Checkpoint) error {
	raw, err := json.Marshal(checkpoint)
	if err != nil {
		return err
	}
	return atomicWrite(filepath.Join(s.lkgDir, "checkpoint.json"), raw)
}

func (s *Store) SaveEvidence(diff DiffSummary, generation uint64) error {
	payload := struct {
		ObservedGeneration uint64      `json:"observed_generation"`
		Diff               DiffSummary `json:"diff"`
	}{generation, diff}
	raw, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	return atomicWrite(filepath.Join(s.evidenceDir, "shadow-diff.json"), raw)
}

func atomicWrite(path string, content []byte) error {
	dir := filepath.Dir(path)
	temp, err := os.CreateTemp(dir, ".xconnect-*.")
	if err != nil {
		return fmt.Errorf("create atomic state file: %w", err)
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if err := temp.Chmod(0o600); err != nil {
		temp.Close()
		return err
	}
	if _, err := temp.Write(content); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tempName, path); err != nil {
		return fmt.Errorf("promote atomic state file: %w", err)
	}
	directory, err := os.Open(dir)
	if err == nil {
		err = directory.Sync()
		directory.Close()
	}
	return err
}
