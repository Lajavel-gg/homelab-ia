package preuve

// Double renvoie le double de x.
func Double(x int) int {
	return x * 3
}

// réglages
var (
	petit  = 1
	grand  = 100
)

func Bornes() (int, int) { return petit, grand }
