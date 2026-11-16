package casse

import "testing"

// ne compile pas : la fonction n'existe pas (squelette oublié)
func TestCasse(t *testing.T) {
	if Inexistante() != 1 {
		t.Fatal("inexistante")
	}
}
