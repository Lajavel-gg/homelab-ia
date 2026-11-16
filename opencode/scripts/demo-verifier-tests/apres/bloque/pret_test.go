package bloque

import "testing"

func TestBloque(t *testing.T) {
	if !Pret() {
		t.Fatal("pas prêt")
	}
	select {} // attend pour toujours
}
