package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ai-workspace-infra/playbooks/tools/xconnect-gateway-agent/internal/staticmigration"
)

func commandFixture(name string) string {
	return filepath.Join("..", "..", "..", "..", "tests", "fixtures", "xconnect-static-import", name)
}

func TestImportDefaultsToDryRun(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run([]string{"import", "--input", commandFixture("group-vars.yml"), "--network-id", "network-private", "--owner-user-id", "11111111-1111-4111-8111-111111111111"}, &stdout, &stderr)
	if code != 0 || !strings.Contains(stderr.String(), "dry-run") || !strings.Contains(stdout.String(), staticmigration.ImportDocumentKind) {
		t.Fatalf("default import was not a successful dry-run: code=%d stdout=%q stderr=%q", code, stdout.String(), stderr.String())
	}
}

func TestApplyRequiresExplicitControllerInputs(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run([]string{"import", "--input", commandFixture("group-vars.yml"), "--network-id", "network-private", "--owner-user-id", "11111111-1111-4111-8111-111111111111", "--apply"}, &stdout, &stderr)
	if code != staticmigration.InputErrorExitCode || !strings.Contains(stderr.String(), "requires") {
		t.Fatalf("apply guard missing: code=%d stderr=%q", code, stderr.String())
	}
}

func TestDiffExitCodes(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run([]string{"diff", "--input", commandFixture("group-vars.yml"), "--snapshot", commandFixture("gateway-snapshot.json"), "--attachment", "gateway-a"}, &stdout, &stderr)
	if code != 0 || !strings.Contains(stdout.String(), `"status": "equal"`) {
		t.Fatalf("equal diff failed: code=%d stderr=%q", code, stderr.String())
	}
	stdout.Reset()
	stderr.Reset()
	code = run([]string{"diff", "--input", commandFixture("group-vars.yml"), "--snapshot", commandFixture("gateway-snapshot.json"), "--attachment", "gateway-b"}, &stdout, &stderr)
	if code != staticmigration.DiffExitCode || !strings.Contains(stdout.String(), `"status": "drift"`) {
		t.Fatalf("drift exit code failed: code=%d stderr=%q", code, stderr.String())
	}
}

func TestEvidenceOutputIsAtomicAndProtected(t *testing.T) {
	path := filepath.Join(t.TempDir(), "evidence", "diff.json")
	if err := writeOutput(path, []byte("first\n"), &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if err := writeOutput(path, []byte("second\n"), &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil || string(raw) != "second\n" {
		t.Fatalf("atomic evidence output failed: raw=%q err=%v", raw, err)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("evidence mode = %o", info.Mode().Perm())
	}
	if err := writeOutput("relative.json", []byte("unsafe"), &bytes.Buffer{}); err == nil {
		t.Fatal("relative evidence path accepted")
	}
}
