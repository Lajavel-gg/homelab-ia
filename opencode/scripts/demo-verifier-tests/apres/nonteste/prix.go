package nonteste

func Prix(n int) int {
	prix := 12
	total := n * prix
	if total < 0 {
		total = 0
	}
	return total
}
