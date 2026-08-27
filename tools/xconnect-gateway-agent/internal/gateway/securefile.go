package gateway

import (
	"errors"
	"fmt"
	"io"
	"os"
)

// readProtectedFile rejects symlinks, non-regular files and permissions wider
// than 0640. Checking both before and after open also catches replacement
// between the path inspection and read on supported filesystems.
func readProtectedFile(path, label string) ([]byte, error) {
	before, err := os.Lstat(path)
	if err != nil {
		return nil, fmt.Errorf("read %s", label)
	}
	if before.Mode()&os.ModeSymlink != 0 || !before.Mode().IsRegular() || before.Mode().Perm()&^os.FileMode(0o640) != 0 {
		return nil, fmt.Errorf("%s must be a regular non-symlink file with permissions no wider than 0640", label)
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("read %s", label)
	}
	defer file.Close()
	after, err := file.Stat()
	if err != nil || !after.Mode().IsRegular() || !os.SameFile(before, after) {
		return nil, errors.New("protected file changed while opening")
	}
	raw, err := io.ReadAll(io.LimitReader(file, 1<<20))
	if err != nil {
		return nil, fmt.Errorf("read %s", label)
	}
	return raw, nil
}
