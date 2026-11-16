package preuve

// Double renvoie deux fois x (commentaire reformulé seulement).
func Double(x int) int {
	return x + x
}

// réglages
var (
	petit = 1
	grand = 100
)

func Bornes() (int, int) { return petit, grand }
