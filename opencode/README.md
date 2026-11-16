# OpenCode : l'équipe et les skills

## L'équipe (novembre 2026)

OpenCode passe par [LiteLLM](../litellm/) et a deux modèles : le **chef** (Qwen3.8-27B, dense, environ 21 jetons/s)
et les **ouvriers** (Qwen3.6-35B-A3B NVFP4 avec MTP 3 depuis le 15/11, à la place de Nemotron Lightning 30B-A3B :
environ 114 jetons/s seul, 173 jetons/s au total pour 16 requêtes longues).
Les agents sont dans [`agents/`](agents/) (à copier dans `~/.config/opencode/agent/`).

| Agent | Modèle | Rôle |
|---|---|---|
| `conseil` | chef | Réfléchit avec l'utilisateur : explique, pose des questions (outil `question`), propose un plan. **Ne peut rien modifier** (`edit` et `bash` refusés, seul le sous-agent `explore` est permis). |
| `architecte` | chef | **Pour la course d'ouvriers pilotée par l'application** : prépare le chantier (squelette des nouvelles fonctions avec `panic("à faire")`, tests d'acceptation verrouillés qui doivent échouer, `CHANTIER.json`), puis fait une relecture finale unique (courte si tout a gagné du premier coup). |
| `equipe` (par défaut) | chef | **Chef de chantier (essai du 15/11)** : découpe le plan en tâches indépendantes (fichiers séparés, un test par tâche), lance les codeurs en parallèle, fait contrôler par le vérificateur, relit le diff et renvoie chaque erreur à un codeur. N'a pas le droit de modifier un fichier. |
| `codeur` | ouvrier | Fait UNE tâche : écrit d'abord le test (skill `tdd`), puis le code, jusqu'à ce que son critère passe. |
| `verificateur` | ouvrier | Lance tous les tests, compare `git diff` à la demande, rend un rapport court. Ne modifie rien. |
| `relecteur` | chef | Relecture à la demande, avant un commit. |

Pourquoi le chef reçoit la demande : sur deux vraies tâches, un ouvrier seul a eu **3/5 et 1/5** (un cas oublié ;
un fichier demandé effacé, un script qui restait bloqué). Avec le chef qui confie puis vérifie : **4/5 et 4/5**.
Une règle écrite demandant à l'ouvrier d'appeler un relecteur était tout simplement ignorée.

### Mesures sur une même tâche (15/11)

Tâche : délai d'écriture glissant + ping SSE toutes les 30 s + quota d'images par compte, dans un serveur Go
(environ 100 lignes et 3 tests). Même plan validé à chaque fois.

| Version | Durée | Ce qu'on a vu |
|---|---|---|
| Chef qui confie à un codeur puis corrige lui-même | 96 min | 55 tours du chef (54 min de modèle, dont 70 % de réflexion), 1 codeur (284 petites étapes). Le chef rattrape 4 erreurs du codeur ; une course à la fermeture du flux reste, trouvée à la relecture humaine. |
| Chef de chantier (agents actuels) | 2 h | 16 tours du chef, 3 codeurs en parallèle. Les codeurs disent « fini » avec un ping à 30 ms au lieu de 30 s et une fonction jamais écrite ; le chef les renvoie. Il trouve seul la course à la fermeture. Meilleure qualité, mais plus lent. |

#### 15 et 16/11 : réglages du chef, puis course d'ouvriers

Même tâche, même plan, même commit de départ ; ouvriers Qwen3.6-35B-A3B + MTP 3. Qualité vérifiée de la même façon
sur chaque copie : `go vet`, `go test -race`, et `verifier-tests --mutation`.

| Essai | Durée | Tours du chef | Réflexion du chef | Renvois aux ouvriers | Morceaux qu'aucun test ne vérifie |
|---|---|---|---|---|---|
| A : chef de chantier, réglage par défaut | 36 min | 17 | 78 % | 0 | 3 |
| A2 : le même, refait | 44 min | 21 | 72 % | 1 | 5 |
| B : chef en réflexion « medium » | 78 min | 29 | 80 % | 1 | 2 |
| C : « medium » + script de vérification des tests | 68 min | 38 | 79 % | 1 | 4 |
| D : C refait | 69 min | 30 | 72 % | 2 | 3 |

- La durée varie d'environ 20 % d'un essai à l'autre : il faut au moins 2 essais par réglage.
- « medium » ne réduit pas la réflexion (72 à 80 %) et rend le chef 2 fois plus lent (plus de tours, résumés forcés).
  Vérifié : le réglage arrive bien au modèle (38 jetons d'instructions en moins dans chaque requête).
- Les 5 versions sont justes (`-race` vert, aucun test fragile), mais aucune ne teste l'arrêt des goroutines :
  d'où l'obligation de goleak dans les tests de l'architecte.
- Le script a été mal lancé par les ouvriers (ils cherchaient `./verifier-tests.sh`) : il est devenu une vraie
  commande, [`verifier-tests`](#la-commande-verifier-tests).

**Course d'ouvriers pilotée par l'application** (l'architecte prépare ; l'application fait courir les ouvriers dans des
copies `git worktree`, le premier qui passe ses tests et `verifier-tests` gagne ; l'architecte relit) :

| Essai | Architecte (préparer) | Course | Relecture | Total |
|---|---|---|---|---|
| 1 tâche, 1 ouvrier | 8,8 min | 24 s | non mesurée | environ 9 min sans la relecture |
| 2 tâches, 2 ouvriers chacune (n° 1) | 8,2 min (11 tours, 13 000 jetons) | 41 s | 5,6 min | 14,5 min |
| 2 tâches, 2 ouvriers chacune (n° 2) | 15,7 min (14 tours, 24 000 jetons) | 40 s | 7,7 min | 24 min |

- L'architecte prend 95 % du temps ; sa durée suit le nombre de jetons qu'il écrit (environ 25 par seconde, les trois
  quarts en réflexion). Ajouté depuis : une fiche projet préparée par l'application, et une relecture courte.
- Aucun ouvrier ne s'est trompé sur ces tâches (médiane, mention) : le renvoi d'erreur n'est testé pour l'instant
  qu'avec un faux OpenCode.

#### 16/11 : une tâche difficile, 8 essais (architecte et nombre d'ouvriers)

Tâche : un groupe de travailleurs en Go (plafond de travaux en même temps, délai par travail, fermeture propre),
choisie parce qu'un ouvrier peut s'y tromper. Ouvriers Qwen3.6-35B-A3B, 3 essais chacun, limite fixe de 45 min par
essai. Qualité mesurée par un jury de tests cachés (gardé hors de ce dépôt, jamais vu par les agents) : 5 pièges
(canal fermé, goroutine restante, contexte jamais annulé, `Fermer` qui n'attend pas, plafond dépassé) + 1 bonus,
chaque test seul, 50 fois avec `-race`. Les tests de chaque architecte ont aussi été lancés sur une version de
référence juste (chaque test seul, 20 fois) et sur 7 versions cassées exprès.

| Essai | Architecte | Ouvriers | Total | Gagnant | Jury caché (tests réussis 50/50) |
|---|---|---|---|---|---|
| 1 | Qwen3.8 (chef), 29,6 min | 2 | 118 min | oui, 2e essai | 3 / 6 |
| 2 | Qwen3.6, 1,9 min | 1 | 47 min | non | pas de code |
| 3 | Qwen3.8 (chef), 24,6 min | 2 | 52 min | oui, 1er essai | 4 / 6 |
| 4 | Qwen3.6, 1,5 min | 1 | 69 min | non | pas de code |
| 5 | préparation de l'essai 1 | 1 | 41 min | oui, 3e essai | 5 / 6 |
| 6 | préparation de l'essai 1 | 3 | 36 min | oui, 1er essai | 2 / 6 |
| 7 | préparation de l'essai 1 | 1 | 38 min | non | pas de code |
| 8 | préparation de l'essai 1 | 3 | 86 min | non | pas de code |

| Préparation | Tests faux ou fragiles sur la référence | Pièges attrapés par un test juste |
|---|---|---|
| Qwen3.8, essai 1 | 2 sur 8 (échouent 10 et 25 % du temps) | 1 sur 5 |
| Qwen3.8, essai 3 | 1 sur 8 (40 %) | 3 sur 5, plus la moitié de « Fermer qui n'attend pas » |
| Qwen3.6, essai 2 | 6 sur 11 (5 échouent toujours) | 1 sur 5, plus une moitié |
| Qwen3.6, essai 4 | 7 sur 14 (tous échouent toujours) | 1 sur 5, plus une moitié |

- Les 4 préparations passent `verifier-tests --doivent-echouer` : ce contrôle ne voit pas un test faux.
- L'architecte Qwen3.6 prépare 15 fois plus vite, mais la moitié de ses tests sont faux : aucun gagnant possible.
- Un test verrouillé fragile (25 % d'échecs sur une version juste) a fait refuser 4 essais d'ouvriers qui étaient
  « tout vert ».
- Aucun code final ne passe tout le jury. Les 4 ratent le même piège : un 2e `Fermer` appelé en même temps
  n'attend pas. Aucun test d'architecte ne le vérifiait.
- `verifier-tests --mutation` n'a rien pu muter dans les 8 essais (aucun test ne passe à chaque fois).
- 1 contre 3 ouvriers : un gagnant sur 2 essais dans les deux cas ; 3 ouvriers consomment 17 à 60 fois plus de jetons
  (cache de préfixe : 80 % avec 1 ouvrier, 94 % avec 3).
- Durée des essais réussis : médiane 17 min, maximum 30 min ; premier « tout vert » au plus tard à 36 min.
- 7 boucles de réflexion sur 9 s'arrêtent seules au plafond de 32 000 jetons (environ 5 min) ; le plus long silence
  d'un ouvrier gagnant est de 5,4 min.

Biais à garder en tête pour les mesures suivantes : la liste [`pieges.md`](skills/tests-concurrence/pieges.md) donnée
ensuite à l'architecte cite les mêmes catégories de pièges que ce jury (fermeture appelée deux fois, envoi après
fermeture, contexte jamais annulé, plafond sous charge…). Un essai sur cette tâche mesure donc si l'architecte
APPLIQUE la liste, pas s'il DÉCOUVRE les pièges seul. Pour mesurer la découverte : une autre tâche, avec un nouveau
jury écrit à l'aveugle (avant de voir la moindre préparation).

Constats : le chef passe 70 à 77 % de son temps à réfléchir (Qwen3.8-27B est réglé par défaut sur une
réflexion très élevée) ; les ouvriers ne sont pas assez fiables pour travailler sans être revérifiés.
En cours : essai d'un autre modèle d'ouvrier (Qwen3.6-35B-A3B NVFP4) et d'une réflexion plus courte pour le chef.

Ce qui a été lu avant de choisir : le mode architecte d'Aider (gros modèle qui planifie, petit modèle qui écrit),
la skill `subagent-driven-development` de Superpowers (écartée ici : beaucoup de tours de chef, fait pour un chef
rapide), et un banc de modèles locaux sur GB10 ([DG1001/local-agentic-coding-128gb](https://github.com/DG1001/local-agentic-coding-128gb)).

OpenCode tourne dans une VM Proxmox isolée et écrit le code des projets étape par étape (fichier `CHANTIER.md`), avec le modèle du DGX Spark. Le chef vérifie chaque tâche, puis Claude relit avant toute publication.

Une *skill* est une fiche de méthode (`SKILL.md`) que l'agent lit seulement quand la tâche en a besoin. OpenCode les cherche dans `~/.config/opencode/skills/<nom>/` (toute la machine) ou `.opencode/skills/<nom>/` (un projet). Vérifier qu'elle est vue : `opencode debug skill`.

## La commande `verifier-tests`

[`scripts/verifier-tests`](scripts/verifier-tests) (Python 3, sans dépendance) vérifie que les tests ajoutés ou
modifiés prouvent vraiment quelque chose. Installée comme une commande : `~/.local/bin/verifier-tests`.

```
verifier-tests [--depuis COMMIT] [--dossier CHEMIN] [--mutation] [--json] [--fois N]
```

- **Rapide (par défaut)** : les nouveaux tests sont lancés sur l'ancien code (ils doivent échouer, sinon ils ne
  prouvent rien), puis 20 fois avec `-race` dans le vrai dossier, avec des limites de temps (2 min / 5 min).
- **`--mutation` (lent)** : retire un seul morceau modifié depuis le commit de départ à la fois, sans les commentaires
  ni la mise en forme. Si ça compile et que les tests passent encore, aucun test ne vérifie ce morceau.
- **`--json`** : `statut` (`ok`, `probleme`, `erreur`) et, pour chaque problème : `type` (`echec`, `fragile`,
  `non_verifie`, `impossible`), `bloquant`, `fichier`, `ligne`, `test`, `message`. `impossible` (le test ne compile
  pas sur l'ancien code) n'est pas bloquant.
- **Codes de retour** : 0 = tout va bien, 1 = problème trouvé, 2 = la vérification n'a pas pu tourner
  (dossier introuvable, pas un dépôt git, langage pas encore géré…).

Le dossier de travail n'est jamais modifié : tout se passe dans des copies temporaires (`git worktree`), supprimées
même en cas d'erreur. Avec `--mutation`, seuls les tests qui passent à chaque fois servent, et les paquets où un test
échoue déjà sont sautés (le problème est déjà signalé). Limites de temps réglables : `VERIFIER_LIMITE_ANCIEN` (2m),
`VERIFIER_LIMITE_FRAGILE` (5m).

**Test du script.** [`scripts/demo-verifier-tests/`](scripts/demo-verifier-tests/) est un petit projet Go (`avant/` =
commit de départ, `apres/` = travail à vérifier) avec un cas par paquet : une vraie correction bien testée avec un
commentaire changé et des lignes réalignées (rien à signaler), un test qui passait déjà avant, un test qui échoue,
un test fragile, un test qui bloque, un morceau qu'aucun test ne vérifie. `python3 scripts/test_verifier_tests.py`
recrée le projet et vérifie les résultats attendus, en mode rapide et `--mutation` (environ 1 minute, il faut git et go).

**Langages.** Le langage est détecté par son fichier de projet (`go.mod`, `package.json`, `pyproject.toml`,
`Cargo.toml`). Seul Go est géré pour l'instant ; les autres affichent « langage pas encore géré ». Outils prévus pour
la mutation : [universalmutator](https://github.com/agroce/universalmutator) (tous langages),
[mutmut](https://github.com/boxed/mutmut) (Python), [Stryker](https://stryker-mutator.io/) (JavaScript/TypeScript),
[cargo-mutants](https://github.com/sourcefrog/cargo-mutants) (Rust).

## Skills installées

| Skill | Rôle | Origine |
|---|---|---|
| `tdd` | Écrire le test qui échoue d'abord, puis le minimum de code ; tester ce que fait le code, pas comment il le fait | [mattpocock/skills](https://github.com/mattpocock/skills), licence MIT, avec une section ajoutée pour un agent qui travaille seul |
| [`tests-concurrence`](skills/tests-concurrence/SKILL.md) | Pour l'architecte : tests fiables avec du temps ou des goroutines (jamais de `time.Sleep` pour synchroniser, attendre le vrai signal, `testing/synctest`, goleak, `-race`), et une [liste générale de pièges](skills/tests-concurrence/pieges.md) selon le type de code | écrite ici, après les mesures du 16/11 (tests verrouillés fragiles) |

## Règles

- Une skill est lue en entier avant d'être installée : ce sont des instructions données à un agent qui peut lancer des commandes.
- Pas d'installation automatique (`npx skills add …`) : copie à la main d'une version précise (commit noté dans le fichier).
- Chaque skill est jugée sur des mesures (tests écrits avant le code, défauts trouvés à la relecture), pas sur une impression.
