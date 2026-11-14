# OpenCode : l'équipe et les skills

## L'équipe (novembre 2026)

OpenCode passe par [LiteLLM](../litellm/) et a deux modèles : le **chef** (Qwen3.8-27B) et les **ouvriers**
(Nemotron Lightning 30B). Les agents sont dans [`agents/`](agents/) (à copier dans `~/.config/opencode/agent/`).

| Agent | Modèle | Rôle |
|---|---|---|
| `conseil` | chef | Réfléchit avec l'utilisateur : explique, pose des questions (outil `question`), propose un plan. **Ne peut rien modifier** (`edit` et `bash` refusés). |
| `equipe` (par défaut) | chef | Confie le travail au `codeur` (4 en parallèle au plus), vérifie lui-même chaque point de la demande, corrige, puis résume. |
| `codeur` | ouvrier | Fait exactement ce que demande le chef, ni plus ni moins. |
| `relecteur` | chef | Relecture à la demande, avant un commit. |

Pourquoi le chef reçoit la demande : sur deux vraies tâches, un ouvrier seul a eu **3/5 et 1/5** (un cas oublié ;
un fichier demandé effacé, un script qui restait bloqué). Avec le chef qui confie puis vérifie : **4/5 et 4/5**.
Une règle écrite demandant à l'ouvrier d'appeler un relecteur était tout simplement ignorée.


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
