# OpenCode : skills

OpenCode tourne dans une VM Proxmox isolée et écrit le code des projets étape par étape (fichier `CHANTIER.md`), avec le modèle du DGX Spark. Claude relit et vérifie chaque étape ensuite.

Une *skill* est une fiche de méthode (`SKILL.md`) que l'agent lit seulement quand la tâche en a besoin. OpenCode les cherche dans `~/.config/opencode/skills/<nom>/` (toute la machine) ou `.opencode/skills/<nom>/` (un projet). Vérifier qu'elle est vue : `opencode debug skill`.

## Skills installées

| Skill | Rôle | Origine |
|---|---|---|
| `tdd` | Écrire le test qui échoue d'abord, puis le minimum de code ; tester ce que fait le code, pas comment il le fait | [mattpocock/skills](https://github.com/mattpocock/skills), licence MIT, avec une section ajoutée pour un agent qui travaille seul |

## Règles

- Une skill est lue en entier avant d'être installée : ce sont des instructions données à un agent qui peut lancer des commandes.
- Pas d'installation automatique (`npx skills add …`) : copie à la main d'une version précise (commit noté dans le fichier).
- Chaque skill est jugée sur des mesures (tests écrits avant le code, défauts trouvés à la relecture), pas sur une impression.
