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
	"regexp"
	"strings"
)

var uuidPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

type relayCredential struct {
	ID              string `json:"id"`
	CertificateFile string `json:"certificate_file"`
	PrivateKeyFile  string `json:"private_key_file"`
}

type CredentialResolver struct{ Directory string }

func (r CredentialResolver) Resolve(ref string) (relayCredential, error) {
	if !idPattern.MatchString(ref) || strings.Contains(ref, "..") || strings.ContainsAny(ref, `/\\`) {
		return relayCredential{}, errors.New("relay credential reference is invalid")
	}
	base, err := filepath.Abs(r.Directory)
	if err != nil || !filepath.IsAbs(r.Directory) {
		return relayCredential{}, errors.New("relay credential directory is invalid")
	}
	path := filepath.Join(base, ref+".json")
	if filepath.Dir(path) != base {
		return relayCredential{}, errors.New("relay credential reference escaped protected directory")
	}
	raw, err := readProtectedFile(path, "relay credential")
	if err != nil {
		return relayCredential{}, err
	}
	var credential relayCredential
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&credential); err != nil {
		return relayCredential{}, errors.New("decode relay credential")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return relayCredential{}, errors.New("relay credential contains multiple JSON values")
	}
	if !uuidPattern.MatchString(strings.ToLower(credential.ID)) || !filepath.IsAbs(credential.CertificateFile) || !filepath.IsAbs(credential.PrivateKeyFile) {
		return relayCredential{}, errors.New("relay credential fields are invalid")
	}
	if _, err := readProtectedFile(credential.CertificateFile, "relay TLS certificate"); err != nil {
		return relayCredential{}, err
	}
	if _, err := readProtectedFile(credential.PrivateKeyFile, "relay TLS private key"); err != nil {
		return relayCredential{}, err
	}
	credential.ID = strings.ToLower(credential.ID)
	return credential, nil
}

func RenderXrayRelayConfig(snapshot GatewaySnapshot, inboundTag string, resolver CredentialResolver) ([]byte, error) {
	if snapshot.ProxyCore != "xray" || snapshot.Relay.Transport != "vless-tls-xudp" || net.ParseIP(snapshot.Relay.ListenHost) == nil || len(snapshot.Relay.ServerNames) == 0 || len(snapshot.Relay.CredentialRefs) == 0 {
		return nil, errors.New("snapshot relay cannot be rendered by Xray v1")
	}
	clients := make([]map[string]any, 0, len(snapshot.Relay.CredentialRefs))
	var tls relayCredential
	for i, ref := range snapshot.Relay.CredentialRefs {
		credential, err := resolver.Resolve(ref)
		if err != nil {
			return nil, fmt.Errorf("resolve relay credential %d", i)
		}
		if i == 0 {
			tls = credential
		} else if credential.CertificateFile != tls.CertificateFile || credential.PrivateKeyFile != tls.PrivateKeyFile {
			return nil, errors.New("relay credentials must share the configured TLS identity")
		}
		clients = append(clients, map[string]any{"id": credential.ID})
	}
	config := map[string]any{"inbounds": []any{map[string]any{
		"tag": inboundTag, "listen": snapshot.Relay.ListenHost, "port": snapshot.Relay.ListenPort, "protocol": "vless",
		"settings": map[string]any{"clients": clients, "decryption": "none"},
		"streamSettings": map[string]any{"network": "tcp", "security": "tls", "tlsSettings": map[string]any{
			"serverName": snapshot.Relay.ServerNames[0], "certificates": []any{map[string]any{"certificateFile": tls.CertificateFile, "keyFile": tls.PrivateKeyFile}},
		}},
	}}}
	return json.Marshal(config)
}

func (t *RuntimeTransaction) xrayPreflight(ctx context.Context, candidate string) error {
	_, err := t.Runner.Run(ctx, t.Config.Runtime.XrayBinary, "run", "-test", "-config", candidate)
	return err
}

func (t *RuntimeTransaction) xrayApply(ctx context.Context, candidate string, hadPrevious bool) error {
	var removeErr error
	if hadPrevious {
		_, removeErr = t.Runner.Run(ctx, t.Config.Runtime.XrayBinary, "api", "rmi", "--server="+t.Config.Runtime.XrayAPIEndpoint, t.Config.Runtime.XrayInboundTag)
	}
	if _, err := t.Runner.Run(ctx, t.Config.Runtime.XrayBinary, "api", "adi", "--server="+t.Config.Runtime.XrayAPIEndpoint, candidate); err != nil {
		return fmt.Errorf("add Xray inbound (remove=%v): %w", removeErr, err)
	}
	// A remove not-found is expected after a dedicated Xray restart. Successful
	// add plus the subsequent exact tag/listen readback is authoritative. A
	// transport failure cannot be hidden because add/readback use the same API.
	return nil
}

func (t *RuntimeTransaction) xrayReadback(ctx context.Context, candidate string) error {
	raw, err := readProtectedFile(candidate, "protected Xray runtime config")
	if err != nil {
		return err
	}
	var identity struct {
		Inbounds []struct {
			Tag    string `json:"tag"`
			Listen string `json:"listen"`
			Port   int    `json:"port"`
		} `json:"inbounds"`
	}
	if err := json.Unmarshal(raw, &identity); err != nil || len(identity.Inbounds) != 1 || identity.Inbounds[0].Tag != t.Config.Runtime.XrayInboundTag || identity.Inbounds[0].Port < 1 {
		return errors.New("Xray candidate identity is invalid")
	}
	pattern := "inbound>>>" + identity.Inbounds[0].Tag + ">>>traffic"
	if _, err := t.Runner.Run(ctx, t.Config.Runtime.XrayBinary, "api", "statsquery", "--server="+t.Config.Runtime.XrayAPIEndpoint, "-pattern", pattern); err != nil {
		return errors.New("Xray inbound tag readback failed")
	}
	dial := t.DialContext
	if dial == nil {
		dialer := &net.Dialer{}
		dial = dialer.DialContext
	}
	host := "127.0.0.1"
	if net.ParseIP(identity.Inbounds[0].Listen).To4() == nil {
		host = "::1"
	}
	connection, err := dial(ctx, "tcp", net.JoinHostPort(host, fmt.Sprintf("%d", identity.Inbounds[0].Port)))
	if err != nil {
		return errors.New("Xray relay listen readback failed")
	}
	_ = connection.Close()
	return nil
}

func (t *RuntimeTransaction) xrayRollback(ctx context.Context, previous string, hadPrevious bool) error {
	var removeErr error
	_, removeErr = t.Runner.Run(ctx, t.Config.Runtime.XrayBinary, "api", "rmi", "--server="+t.Config.Runtime.XrayAPIEndpoint, t.Config.Runtime.XrayInboundTag)
	if hadPrevious {
		if _, err := t.Runner.Run(ctx, t.Config.Runtime.XrayBinary, "api", "adi", "--server="+t.Config.Runtime.XrayAPIEndpoint, previous); err != nil {
			return fmt.Errorf("restore Xray baseline (remove=%v): %w", removeErr, err)
		}
		if err := t.xrayReadback(ctx, previous); err != nil {
			return fmt.Errorf("verify restored Xray baseline (remove=%v): %w", removeErr, err)
		}
		return nil
	}
	return removeErr
}

func copyProtected(source, destination string) error {
	raw, err := readProtectedFile(source, "protected Xray runtime config")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		return err
	}
	return atomicWrite(destination, raw)
}
