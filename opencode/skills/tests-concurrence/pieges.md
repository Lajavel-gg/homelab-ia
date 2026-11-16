# Pièges à couvrir, selon le type de code

Une liste générale. Pour chaque tâche, garde ceux qui la concernent et écris un test par piège retenu. Pour vérifier
qu'un test attrape bien son piège : écris à part une version qui tombe dans le piège ; le test doit échouer.

## Goroutines et canaux

- Une goroutine qui reste bloquée pour toujours après la fin (personne ne lit son canal, personne ne l'arrête).
- Un envoi sur un canal déjà fermé (le programme plante), surtout quand fermeture et envois arrivent en même temps.
- Un canal fermé deux fois.
- Un résultat envoyé sur un canal sans place, alors que celui qui attendait est déjà parti (la goroutine reste bloquée).
- Une variable partagée lue et écrite sans protection (`-race` la voit seulement si le test fait vraiment les deux en
  même temps).
- Un `WaitGroup.Add` qui peut arriver après le `Wait`, ou un compteur qui devient négatif.

## Fermeture, arrêt d'un service

- Fermer appelé deux fois, et deux fois EN MÊME TEMPS : aucun plantage, et chaque appel a le comportement promis
  (par exemple : chacun attend la vraie fin, pas seulement le premier).
- Fermer pendant qu'un travail tourne : la fermeture attend-elle, ou coupe-t-elle, comme promis ?
- Ce qui arrive après la fermeture (nouvelle demande, demande qui attendait) : refusé avec la bonne erreur, jamais
  lancé à moitié.
- Après la fermeture : plus aucune goroutine, plus aucune ressource ouverte (fichier, connexion, minuterie).

## Délais, contextes, annulation

- Un contexte avec délai jamais annulé (`cancel` oublié) : la minuterie reste jusqu'au délai.
- L'annulation de l'appelant qui ne parvient pas au travail (contexte mal dérivé).
- Le délai dépassé : la bonne erreur, au bon moment (pas seulement quand le travail finit de lui-même).
- Annulé AVANT le début / PENDANT l'attente / PENDANT le travail : trois cas différents, trois tests.
- Un travail qui ignore son contexte : que devient sa place, son résultat, sa goroutine ?

## Limites, plafonds, files d'attente

- Le plafond respecté sous forte charge (beaucoup plus de demandes que de places, en même temps), pas seulement avec
  deux appels.
- Une place rendue trop tôt (avant la vraie fin du travail) : le plafond est dépassé sans que personne le voie.
- Les valeurs limites : 0, 1, négatif, très grand ; la valeur par défaut exacte quand rien n'est donné.
- L'ordre promis (premier arrivé, premier servi) s'il y en a un ; aucun ordre supposé s'il n'y en a pas.
- Une file pleine : refus, attente ou perte, comme promis.

## Réessais, minuteries, tâches régulières

- Le nombre exact d'essais, et l'attente entre deux (avec synctest).
- Arrêter pendant une attente entre deux essais.
- Une minuterie (`time.Ticker`, `time.AfterFunc`) jamais arrêtée.

## Réseau, HTTP, flux

- Le client qui se déconnecte au milieu : le serveur arrête son travail et ne laisse rien derrière.
- Les erreurs du serveur distant (lent, coupé, réponse invalide) : une erreur claire, pas de blocage.
- Les limites de taille (corps trop gros, ligne trop longue) et les codes de réponse exacts.

## Entrées, validation, fichiers

- L'entrée vide, trop longue, avec des caractères spéciaux, avec un chemin qui remonte (`..`) ou absolu.
- Le fichier absent, illisible, ou écrit à moitié (écrire dans un fichier temporaire, puis renommer).
- Chaque erreur a un message clair et ne laisse pas un état à moitié changé.
