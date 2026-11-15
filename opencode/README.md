# OpenCode : l'équipe et les skills

## L'équipe (novembre 2026)

OpenCode passe par [LiteLLM](../litellm/) et a deux modèles : le **chef** (Qwen3.8-27B, dense, environ 21 jetons/s)
et les **ouvriers** (Nemotron Lightning 30B-A3B, environ 58 jetons/s chacun, jusqu'à 16 en parallèle).
Les agents sont dans [`agents/`](agents/) (à copier dans `~/.config/opencode/agent/`).

| Agent | Modèle | Rôle |
|---|---|---|
| `conseil` | chef | Réfléchit avec l'utilisateur : explique, pose des questions (outil `question`), propose un plan. **Ne peut rien modifier** (`edit` et `bash` refusés, seul le sous-agent `explore` est permis). |
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

Constats : le chef passe 70 à 77 % de son temps à réfléchir (Qwen3.8-27B est réglé par défaut sur une
réflexion très élevée) ; les ouvriers ne sont pas assez fiables pour travailler sans être revérifiés.
En cours : essai d'un autre modèle d'ouvrier (Qwen3.6-35B-A3B NVFP4) et d'une réflexion plus courte pour le chef.

Ce qui a été lu avant de choisir : le mode architecte d'Aider (gros modèle qui planifie, petit modèle qui écrit),
la skill `subagent-driven-development` de Superpowers (écartée ici : beaucoup de tours de chef, fait pour un chef
rapide), et un banc de modèles locaux sur GB10 ([DG1001/local-agentic-coding-128gb](https://github.com/DG1001/local-agentic-coding-128gb)).

OpenCode tourne dans une VM Proxmox isolée et écrit le code des projets étape par étape (fichier `CHANTIER.md`), avec le modèle du DGX Spark. Le chef vérifie chaque tâche, puis Claude relit avant toute publication.

Une *skill* est une fiche de méthode (`SKILL.md`) que l'agent lit seulement quand la tâche en a besoin. OpenCode les cherche dans `~/.config/opencode/skills/<nom>/` (toute la machine) ou `.opencode/skills/<nom>/` (un projet). Vérifier qu'elle est vue : `opencode debug skill`.

## Skills installées

| Skill | Rôle | Origine |
|---|---|---|
| `tdd` | Écrire le test qui échoue d'abord, puis le minimum de code ; tester ce que fait le code, pas comment il le fait | [mattpocock/skills](https://github.com/mattpocock/skills), licence MIT, avec une section ajoutée pour un agent qui travaille seul |

## Règles

- Une skill est lue en entier avant d'être installée : ce sont des instructions données à un agent qui peut lancer des commandes.
- Pas d'installation automatique (`npx skills add …`) : copie à la main d'une version précise (commit noté dans le fichier).
- Chaque skill est jugée sur des mesures (tests écrits avant le code, défauts trouvés à la relecture), pas sur une impression.
