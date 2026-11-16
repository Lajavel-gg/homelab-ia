package preuve

import "testing"

func TestDouble(t *testing.T) {
	if Double(2) != 4 {
		t.Fatalf("Double(2) = %d, attendu 4", Double(2))
	}
}
