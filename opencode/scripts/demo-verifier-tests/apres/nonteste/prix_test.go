package nonteste

import "testing"

func TestPrix(t *testing.T) {
	if Prix(2) != 24 {
		t.Fatalf("Prix(2) = %d, attendu 24", Prix(2))
	}
}
