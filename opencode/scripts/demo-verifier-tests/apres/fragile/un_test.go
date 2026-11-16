package fragile

import "testing"

var passage int

func TestUnFragile(t *testing.T) {
	passage++
	if Un() != 1 || passage%3 == 0 {
		t.Fatal("échoue un passage sur trois")
	}
}
