package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path"
	"strings"
	"time"
)

var ErrNoPlannedSnapshot = errors.New("no planned gateway snapshot")

type Heartbeat struct {
	NodeID             string `json:"node_id"`
	AgentVersion       string `json:"agent_version"`
	Mode               string `json:"mode"`
	ProxyCore          string `json:"proxy_core"`
	ObservedGeneration uint64 `json:"observed_generation"`
	AppliedGeneration  uint64 `json:"applied_generation"`
}

type ApplyResult struct {
	NodeID             string      `json:"node_id"`
	SnapshotID         string      `json:"snapshot_id"`
	ObservedGeneration uint64      `json:"observed_generation"`
	AppliedGeneration  uint64      `json:"applied_generation"`
	RuntimeApplied     bool        `json:"runtime_applied"`
	Result             string      `json:"result"`
	Diff               DiffSummary `json:"diff"`
}

type Controller interface {
	Heartbeat(context.Context, Heartbeat) error
	PlannedSnapshot(context.Context, string) ([]byte, error)
	ReportApplyResult(context.Context, ApplyResult) error
}

type HTTPController struct {
	baseURL        *url.URL
	credentialFile string
	client         *http.Client
}

func NewHTTPController(rawURL, credentialFile string, client *http.Client) (*HTTPController, error) {
	baseURL, err := url.Parse(rawURL)
	if err != nil || baseURL.Scheme != "https" || baseURL.Host == "" || baseURL.User != nil || baseURL.RawQuery != "" || baseURL.Fragment != "" {
		return nil, errors.New("invalid controller URL")
	}
	if _, err := readProtectedFile(credentialFile, "controller credential"); err != nil {
		return nil, errors.New("invalid controller credential file")
	}
	if client == nil {
		client = &http.Client{Timeout: 20 * time.Second}
	}
	return &HTTPController{baseURL: baseURL, credentialFile: credentialFile, client: client}, nil
}

func (c *HTTPController) Heartbeat(ctx context.Context, heartbeat Heartbeat) error {
	return c.doJSON(ctx, http.MethodPost, "/api/internal/overlay/v1/nodes/heartbeat", heartbeat, nil)
}

func (c *HTTPController) PlannedSnapshot(ctx context.Context, nodeID string) ([]byte, error) {
	var raw json.RawMessage
	endpoint := path.Join("/api/internal/overlay/v1/nodes", url.PathEscape(nodeID), "snapshot")
	err := c.doJSON(ctx, http.MethodGet, endpoint, nil, &raw)
	if err != nil {
		return nil, err
	}
	snapshot, err := DecodeGatewaySnapshot(raw)
	if err != nil {
		return nil, errors.New("decode planned snapshot response")
	}
	return json.Marshal(snapshot)
}

func (c *HTTPController) ReportApplyResult(ctx context.Context, result ApplyResult) error {
	endpoint := path.Join("/api/internal/overlay/v1/nodes", url.PathEscape(result.NodeID), "apply-result")
	return c.doJSON(ctx, http.MethodPost, endpoint, result, nil)
}

func (c *HTTPController) doJSON(ctx context.Context, method, endpoint string, requestValue any, responseValue any) error {
	var body io.Reader
	if requestValue != nil {
		raw, err := json.Marshal(requestValue)
		if err != nil {
			return err
		}
		body = bytes.NewReader(raw)
	}
	tokenRaw, err := readProtectedFile(c.credentialFile, "controller credential")
	if err != nil {
		return errors.New("read controller credential")
	}
	token := strings.TrimSpace(string(tokenRaw))
	if token == "" {
		return errors.New("controller credential is empty")
	}
	requestURL := *c.baseURL
	requestURL.Path = path.Join(c.baseURL.Path, endpoint)
	req, err := http.NewRequestWithContext(ctx, method, requestURL.String(), body)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/vnd.xconnect.gateway.v1+json")
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return errors.New("controller request failed")
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNoContent && method == http.MethodGet {
		return ErrNoPlannedSnapshot
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("controller request failed with status %d", resp.StatusCode)
	}
	if responseValue == nil {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		return nil
	}
	const maxResponseBytes = 4 << 20
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if err != nil || len(raw) > maxResponseBytes {
		return errors.New("decode controller response")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(responseValue); err != nil {
		return errors.New("decode controller response")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("decode controller response")
	}
	return nil
}
