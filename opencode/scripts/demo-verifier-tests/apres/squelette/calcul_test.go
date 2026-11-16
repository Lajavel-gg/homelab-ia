package squelette

import "testing"

func TestTriple(t *testing.T) {
	if Triple(2) != 6 {
		t.Fatalf("Triple(2) = %d, attendu 6", Triple(2))
	}
}

func TestTripleNegatif(t *testing.T) {
	if Triple(-1) != -3 {
		t.Fatalf("Triple(-1) = %d, attendu -3", Triple(-1))
	}
}

// ne prouve rien : passe avant même que Triple soit écrite
func TestRien(t *testing.T) {
	if 1+1 != 2 {
		t.Fatal("calcul")
	}
}
