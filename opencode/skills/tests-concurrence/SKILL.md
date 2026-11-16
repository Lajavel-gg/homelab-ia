---
name: tests-concurrence
description: Écrire des tests fiables pour du code avec des goroutines, des canaux, des délais, des contextes ou une fermeture (Go). À lire AVANT d'écrire un test verrouillé qui touche au temps ou à la concurrence, ou quand verifier-tests signale un test fragile.
---

# Tests de concurrence fiables (Go)

Un test verrouillé doit passer 20 fois sur 20 avec `-race` sur un code juste, et échouer sur un code faux. Un test
fragile fait refuser le travail d'un ouvrier qui avait raison : c'est le pire défaut d'un test verrouillé.

## Les règles

1. **Jamais de `time.Sleep` pour synchroniser.** « J'attends 10 ms, la goroutine aura sûrement démarré » est faux
   sous `-race` ou sur une machine chargée. On attend un **signal** : un canal fermé par le code testé ou par le
   travail du test (« j'ai démarré », « je suis bloqué ici »).
2. **Attendre que la chose ait vraiment eu lieu avant d'agir.** Avant d'annuler un contexte, de fermer, ou de lâcher
   un travail retenu : le test attend le signal qui prouve que l'état voulu est atteint (le travail a démarré, la
   fermeture a commencé, l'appel est bloqué en attente). Sinon le test teste un autre cas, au hasard.
3. **Le temps et les goroutines : `testing/synctest`** (Go 1.25 et plus). Dans `synctest.Test(t, func(t *testing.T) {…})` :
   - l'horloge est simulée : `time.Sleep(time.Hour)`, un `context.WithTimeout` ou un `time.After` passent tout de
     suite, et toujours dans le même ordre ;
   - `synctest.Wait()` attend que toutes les goroutines de la bulle soient bloquées : on vérifie ensuite qu'une
     chose N'est PAS arrivée, sans fenêtre de temps au hasard ;
   - à la fin, une goroutine restée bloquée fait échouer le test (une fuite est vue).
   Il faut `go 1.25` (ou plus) dans `go.mod` : c'est un fichier réservé à l'architecte, mets-le à jour toi-même.
   Hors bulle (réseau réel, processus), synctest ne s'applique pas : garde des signaux et des limites courtes.
4. **Toujours une limite, jamais un test figé.** Hors synctest, chaque attente a une limite courte (quelques
   secondes) qui fait échouer le test avec un message clair (« bloqué : X n'arrive pas »), au lieu d'attendre le
   `-timeout` de go test.
5. **Durées réglables.** Le code reçoit ses durées (délai, intervalle, plafond) en paramètre ; le test les met à
   quelques millisecondes, ou utilise synctest. Jamais une vraie attente de plusieurs secondes.
6. **`goleak.VerifyNone(t)`** en premier `defer` de chaque test qui lance des goroutines (hors synctest, qui vérifie
   déjà sa bulle).
7. **`-race` toujours**, et `-count` pour voir la fragilité : `go test -race -count=20 -run '^TestX$'`.
8. **Un test, un comportement.** Le nom dit le cas ; le message d'échec dit ce qui manque, avec les valeurs.
9. **Vérifier ce qui NE doit PAS arriver** (un appel qui ne doit pas revenir tout de suite, un travail qui ne doit
   pas démarrer) avec `synctest.Wait()` ; hors synctest, seulement si on ne peut pas faire autrement, avec une
   fenêtre courte et en sachant que ce contrôle peut laisser passer un défaut (jamais l'inverse).
10. **Avant de verrouiller :** `verifier-tests --doivent-echouer` (chaque test échoue sur le squelette), puis
    chaque test seul 20 fois avec `-race` sur une version juste écrite à part, dans un dossier temporaire hors du
    projet (`mktemp -d`), effacée ensuite : un test qui échoue sur une version juste est faux ou fragile.

## Exemple : attendre un signal au lieu d'un Sleep

```go
// FAUX : fragile
go srv.Traiter(ctx, demande)
time.Sleep(10 * time.Millisecond) // « il aura démarré »
annuler()

// JUSTE : le travail du test dit qu'il a démarré
demarre := make(chan struct{})
go srv.Traiter(ctx, func(ctx context.Context) error { close(demarre); <-ctx.Done(); return ctx.Err() })
<-demarre // (avec une limite si on est hors synctest)
annuler()
```

## Exemple : un délai d'une minute, testé sans attendre

```go
func TestRelanceApresUneMinute(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		r := NouveauRelanceur(time.Minute)
		r.Demarrer()
		defer r.Arreter()
		synctest.Wait()
		if r.Relances() != 0 {
			t.Fatal("relancé avant la minute")
		}
		time.Sleep(time.Minute) // simulé : instantané
		synctest.Wait()
		if r.Relances() != 1 {
			t.Fatalf("relances : %d, attendu 1", r.Relances())
		}
	})
}
```

## Quels cas tester

Voir [pieges.md](pieges.md) : la liste des pièges à couvrir selon le type de code. Choisis ceux qui concernent ta
tâche ; chaque piège retenu a son test, qui échoue sur une version qui tombe dans ce piège.
