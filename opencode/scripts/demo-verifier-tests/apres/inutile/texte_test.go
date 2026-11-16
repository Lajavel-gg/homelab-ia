package inutile

import "testing"

func TestMajuscule(t *testing.T) {
	if Majuscule("abc") != "ABC" {
		t.Fatal("Majuscule(abc)")
	}
}

func TestVide(t *testing.T) {
	if Majuscule("") != "" {
		t.Fatal("Majuscule(vide)")
	}
}
