package echoue

import "testing"

func TestMoitie(t *testing.T) {
	if Moitie(4) != 2 {
		t.Fatalf("Moitie(4) = %d, attendu 2", Moitie(4))
	}
}
