<div align="center">

# homelab-ia

**Une IA 100 % locale à la maison : une application de chat qui crée des images, se souvient, lit le code, agit avec ton accord et code en équipe. Sur un NVIDIA DGX Spark et un homelab Proxmox, sans aucun service cloud.**

*A fully local AI stack on an NVIDIA DGX Spark (GB10, 128 GB unified memory) + a Proxmox homelab: two vLLM servers, a LiteLLM gateway, a home-made chat app (Ulysse), an action agent (Hermes), coding agents (OpenCode) and a Gitea sandbox. French documentation.*

![DGX Spark](https://img.shields.io/badge/NVIDIA-DGX%20Spark%20GB10-76B900?logo=nvidia&logoColor=white)
![vLLM](https://img.shields.io/badge/vLLM-NVFP4%20%2B%20MTP-blue)
![LiteLLM](https://img.shields.io/badge/LiteLLM-passerelle-6E56CF)
![ComfyUI](https://img.shields.io/badge/ComfyUI-workflows-orange)
![Gitea](https://img.shields.io/badge/Gitea-bac%20à%20sable-609926?logo=gitea&logoColor=white)
![Proxmox](https://img.shields.io/badge/Proxmox%20VE-cluster-E57000?logo=proxmox&logoColor=white)

</div>

---

## Ce que ça fait

Tout tourne à la maison. Le cœur, c'est **Ulysse**, une application de chat faite maison (Go + React, dépôt privé),
accessible au téléphone par le VPN. Derrière, deux modèles servis par vLLM sur le DGX Spark :

- **le chef** : Qwen3.8-27B (NVFP4, décodage spéculatif MTP), pour parler, réfléchir et vérifier ;
- **les ouvriers** : Qwen3.6-35B-A3B (NVFP4, MTP), jusqu'à 32 agents en même temps, pour le code.

| Brique | Ce qu'elle fait | Code |
|---|---|---|
| **Ulysse** (chat) | Un seul chat : discussion avec **recherche web** (lectures en parallèle) et **recherche approfondie** (plusieurs chercheurs en même temps, synthèse avec sources), **images** (le chef écrit le prompt, Studio dessine, le chef vérifie), **mémoire** validée par l'utilisateur, **traduction de manga** avec liseuse, **maquettes** d'appli affichées dans la discussion, lecture des projets Gitea, et les **actions** confiées à Hermes | dépôt privé |
| **Hermes** | L'assistant qui **agit** (VM de sa zone Proxmox, fichiers, OpenCode) dans une VM fermée ; chaque action sensible attend un « Oui » | [Hermes Agent](https://github.com/NousResearch/hermes-agent) + outils MCP maison (privés) |
| **Code** | Ulysse « chef d'orchestre » : l'architecte (le chef, dans OpenCode) prépare le plan et des **tests verrouillés**, jusqu'à 8 ouvriers font la **course** en parallèle (chacun dans sa copie `git worktree`), Ulysse garde l'essai qui passe les tests, puis une pull request sur Gitea, publiée toute seule | [`opencode/`](opencode/), [`gitea/`](gitea/) |
| **Gitea** | Bac à sable des IA : copie de GitHub (branche principale protégée), les IA ne travaillent que sur leurs branches `ia/…` | [`gitea/`](gitea/) |
| **LiteLLM** | La seule porte vers les modèles : une clé par VM, en HTTPS | [`litellm/`](litellm/) |
| **Studio** | Images, vidéos et 3D par ComfyUI, avec une file d'attente et une vérification de chaque image | [`studio/`](studio/), [`openwebui/outils/studio_tool.py`](openwebui/outils/studio_tool.py) |
| **Correcteur** | Pensé pour la dyslexie et l'écriture « au son » (« sa allaire de focntionner » → « ça a l'air de fonctionner »), avec un carnet personnel qui apprend chaque nuit, un dictionnaire des sons et LanguageTool | [`correcteur/`](correcteur/), [`openwebui/filtres/correcteur_memoire.py`](openwebui/filtres/correcteur_memoire.py) |
| **Bot mail** | Lit les mails, propose des réponses ; **rien ne part** sans validation | [`mail/`](mail/) |
| **Open WebUI** | L'ancienne interface (avant Ulysse), encore en service pendant la transition ; ses outils sont dans [`openwebui/`](openwebui/) (l'admin Proxmox et le trading y sont des archives) | [`openwebui/`](openwebui/) |

## Architecture

```mermaid
flowchart LR
    U[Téléphone / PC] -->|maison ou VPN WireGuard| P[HAProxy<br/>LXC Proxmox]
    P --> UL[Ulysse<br/>VM : Go + React]
    UL -->|une clé par VM| LL[LiteLLM<br/>VM]
    UL --> H[Hermes<br/>VM fermée]
    UL --> OC[OpenCode<br/>VM]
    UL --> G[Gitea<br/>VM]
    H --> LL
    OC --> LL
    OC <--> G
    LL -->|porte réservée| C[Caddy]
    UL -->|porte réservée :<br/>images seulement| C
    subgraph Spark[NVIDIA DGX Spark]
        C --> L[vLLM chef<br/>Qwen3.8-27B NVFP4]
        C --> O[vLLM ouvriers<br/>Qwen3.6-35B-A3B NVFP4]
        C --> S[Studio] --> CF[ComfyUI]
        C --> K[Correcteur]
        X[SearXNG]
    end
    UL --> X
    subgraph Proxmox[Cluster Proxmox]
        P
        VW[Vaultwarden]
        WG[wg-easy]
        B[(Sauvegardes restic<br/>append-only)]
    end
    Spark -->|chaque nuit| B
```

## Captures

<!-- captures -->
Vraies captures d'Ulysse (compte de démonstration, base vide).

**Chat : recherche web, réponse avec ses sources**

<img src="docs/captures/pc-1-recherche.png" alt="Chat : recherche web, réponse avec ses sources" width="860">

<!-- /captures -->

## Le mode Code : un chef et des ouvriers

C'est le schéma « orchestrateur-ouvriers », avec une différence : **ce sont les tests qui jugent, pas un modèle**.

```mermaid
flowchart LR
    D[Demande] --> A[Architecte<br/>le chef]
    A -->|squelette + tests verrouillés<br/>+ CHANTIER.json| U[Ulysse]
    U --> W1[Ouvrier 1<br/>copie worktree]
    U --> W2[Ouvrier 2<br/>copie worktree]
    U --> W3[Ouvrier ...<br/>jusqu'à 8]
    W1 & W2 & W3 -->|le premier qui passe<br/>ses tests gagne| U
    U -->|gofmt + tous les tests| R[Relecture rapide<br/>un ouvrier]
    R --> PR[Pull request Gitea]
```

1. **L'architecte** (le gros modèle) découpe le travail par fichier (3 à 8 tâches), écrit les signatures et les tests d'acceptation. Les ouvriers ne peuvent pas modifier ces tests.
2. **Ulysse** (du code Go, pas un modèle) lance un ouvrier par tâche, en même temps, chacun dans sa propre copie du projet. Un ouvrier bloqué (même échec pendant 20 min) est arrêté et relancé.
3. **Le gagnant** de chaque tâche est le premier essai qui passe ses tests ; Ulysse le remet en forme (`gofmt`) et relance tous les tests du projet.
4. **Relecture rapide** par un ouvrier ; le chef ne relit que si quelque chose est signalé.

L'autre mode, **équipe**, laisse le chef décider lui-même qui fait quoi (règles : lancer les codeurs dès le début, un fichier par codeur, tout lancer d'un coup). Il sert aux petites corrections (« Refaire »).

### Mesures réelles (novembre 2026)

Qualité jugée par des **tests cachés** qu'aucune IA n'a vus, lancés 3 fois avec le détecteur de courses (`-race`).

| Essai | Durée | Tests cachés |
|---|---|---|
| Facile (4 fichiers Go), mode équipe | 21,6 min | 4/4 |
| Facile, mode course, découpé par fichier | 21,7 min | 4/4 |
| Facile, mode course + relecture rapide | **17,5 min** | 4/4 |
| Difficile (cache concurrent, file de travaux, analyseur), mode course | 53,7 min | 3/3 |
| Difficile, architecte remplacé par le petit modèle | arrêté à 75 min | raté |
| Difficile, OpenCode V2 (sous-agents en arrière-plan) | 50,9 min | 3/3 |

Ce que ces mesures montrent :

- **Le goulot, c'est la réflexion du chef** (47,8 min sur 53,7 pour l'essai difficile). Les ouvriers, eux, finissent leurs tâches en 2 à 4 min.
- **Le petit modèle ne peut pas remplacer l'architecte** : 7 fois plus rapide pour préparer, mais un test mal pensé, et un ouvrier qui a écrit dans un dossier mal tapé au lieu de sa copie.
- **OpenCode V2** fait aussi bien que la V1 sur le travail difficile, un peu plus vite. Ulysse sait parler aux deux versions (en essai).

## Points techniques intéressants

- **Deux vLLM sur un seul GPU.** Le chef et les ouvriers partagent le GB10. Quand le chef générait, les ouvriers *compilés* plantaient (Xid 31) en 15 s. Bissection complète : la cause était l'*async scheduling* combiné aux noyaux de réduction générés par Inductor ; réglé en passant à vLLM 0.34 (nightly). Tout est dans [`docs/vllm-deux-instances.md`](docs/vllm-deux-instances.md), avec les bancs de charge ([`spark/bancs/`](spark/bancs/)).
- **Une seule porte vers les IA : LiteLLM.** Les VM ne parlent jamais au Spark directement : une clé par VM, qu'on peut couper sans toucher aux autres. Chaque autre service a sa propre porte réservée (par exemple Studio : images seulement, jamais une vidéo qui arrêterait le chef).
- **Le chef d'orchestre et le jury caché.** Pour juger les ouvriers honnêtement, des tests de référence qu'aucune IA n'a vus sont lancés 50 fois chacun avec le détecteur de courses. Exemple : avec une limite de réponse de 32 768 jetons, le plan de l'architecte était parfois coupé (jury : 2 tests sur 6) ; à 65 536, le premier essai n'est pas coupé (5 sur 6). Un seul essai : à confirmer.
- **Manga traduit dans le chat.** On joint un chapitre (`.cbz`, `.zip`, `.pdf` ou des images) : le Studio détecte les bulles, le modèle ouvrier traduit (réflexion coupée, plus rapide), et le chapitre s'ouvre dans une liseuse (page par page ou défilement, original à côté, « refaire cette page » avec une remarque et un carnet des noms par série). Pour ne pas manquer de mémoire GPU à côté des deux vLLM, le cache du système est vidé juste avant (de 10 à 22 Go libres) : une page en environ 25 s.
- **Maquettes comme des « artifacts ».** Le chat peut fabriquer une page de démo (écrans, couleurs, polices) d'une idée d'appli ou de jeu, affichée dans la discussion dans une `iframe` isolée (`sandbox="allow-scripts"`, sans accès au site, avec sa propre politique de sécurité). On la retouche en parlant ; une fois validée, elle part au mode Code sous forme de fichier `DESIGN.md`, pour que les ouvriers démarrent avec le thème déjà fait.
- **Recherche approfondie.** Sur une question difficile, le chef écrit jusqu'à 5 missions, des chercheurs les font en parallèle (recherches et lectures de pages en même temps), puis le chef fait une synthèse avec les sources. Inspiré des consignes publiques d'Anthropic pour les systèmes de recherche multi-agents.
- **Rien sans « Oui ».** Les actions de Hermes, les mails, la mémoire du chat : le modèle **propose**, l'utilisateur **valide** sur une carte. Ce qui vient du web, d'un mail ou d'un dépôt est traité comme des données, jamais comme des ordres.
- **Démarrage fiable du GPU.** Après une coupure de courant, le GPU n'est pas toujours prêt : [`attendre-gpu`](spark/scripts/attendre-gpu) et [`wait-mem`](spark/scripts/wait-mem) font patienter les services au lieu de démarrer en mode CPU ou de saturer la mémoire.
- **Sauvegardes qu'un pirate ne peut pas effacer.** Le Spark envoie ses sauvegardes [restic](https://restic.net) chaque nuit à travers `rclone serve restic --append-only` : il peut **ajouter** mais pas **supprimer**. ([`sauvegardes/`](sauvegardes/))
- **Accès depuis l'extérieur sans IP fixe ni site ouvert à Internet** : un seul port UDP vers WireGuard, un DNS dynamique, un certificat Let's Encrypt *wildcard* par challenge DNS, et HAProxy qui **refuse tout ce qui ne vient pas de la maison ou du VPN**. ([`proxmox-lxc/`](proxmox-lxc/))
- **Avant novembre 2026** : un seul gros modèle (llama.cpp) servait tout, et le Studio l'arrêtait pendant les vidéos. Les fichiers de cette époque restent dans le dépôt pour l'historique (`spark/llama-swap/`, `spark/systemd/llama-flashnext.service`, la liste [`docs/archives/mise-a-jour-octobre-2026.md`](docs/archives/mise-a-jour-octobre-2026.md)).

## Contenu du dépôt

```
litellm/          passerelle LiteLLM (une clé par VM, HTTPS via Caddy) devant les deux vLLM du Spark
gitea/            bac à sable des IA : Gitea + synchro GitHub -> Gitea (main protégé, les IA sur leurs branches ia/…)
docs/             décisions techniques (deux vLLM sur un GPU)
spark/bancs/      scripts de charge et de vitesse pour vLLM
opencode/agents/  agents OpenCode : architecte, chef d'équipe, codeur, vérificateur, relecteur, conseil
spark/            docker-compose (Open WebUI, SearXNG, Whisper/TTS, Jupyter, ComfyUI, Caddy), services systemd, scripts
openwebui/outils  outils Open WebUI : Studio, Proxmox, Trading
openwebui/filtres filtres Open WebUI : Correcteur, recherche web forcée, outils du modèle toujours actifs
studio/           serveur de rendu (file d'attente, presets qualité, vidéos longues), workflows ComfyUI, vérification d'images
correcteur/       dictionnaire des sons (Lexique 3 + espeak-ng), apprentissage nocturne du carnet
mail/             serveur MCP en lecture seule pour la boîte mail (jamais d'envoi ni de suppression)
proxmox-lxc/      configs des conteneurs : HAProxy, Vaultwarden, wg-easy, DNS dynamique OVH, mise à jour de LanguageTool
sauvegardes/      sauvegarde restic append-only et ménage côté serveur
opencode/         skills (fiches de méthode) données à l'agent de code OpenCode
```

## Matériel

| Machine | Rôle |
|---|---|
| **NVIDIA DGX Spark** (GB10, 20 cœurs ARM, 128 Go de mémoire unifiée) | Toute l'IA |
| **Dell PowerEdge R630** (Proxmox `pve`) | Conteneurs : Vaultwarden, VPN, reverse proxy, LanguageTool |
| **HP ProLiant DL380** (Proxmox `pve2`, RAID 2 To) | Stockage partagé et sauvegardes |

## Utiliser ce dépôt

Ce n'est pas un installateur clé en main mais la **configuration réelle** d'un homelab, nettoyée de tout secret : adresses, domaine (`example.com`) et mots de passe sont à adapter.

1. Copier `spark/.env.example` en `spark/.env` et le remplir.
2. `docker compose -f spark/docker-compose.yml up -d`
3. Installer les services de `spark/systemd/` et les scripts de `spark/scripts/` dans `/usr/local/bin/`.
4. Dans Open WebUI : *Espace de travail → Outils / Fonctions* → importer les fichiers de `openwebui/`.

> Le code d'Ulysse est dans un dépôt privé. Les fichiers `studio/runner.py` et `openwebui/` de ce dépôt datent d'octobre 2026 ; la version en service a évolué depuis.

> **Attention :** les modèles ne sont pas inclus. Vérifiez la licence de chacun avant usage (certaines licences de modèles vidéo excluent des pays ou l'usage commercial).

## Tests automatiques

À chaque push et chaque pull request, GitHub lance `.github/workflows/tests.yml` : erreurs Python (ruff), scripts shell (shellcheck), `docker-compose.yml`, fichiers JSON, et une quarantaine de tests (`tests/`) avec un faux serveur mail, un faux modèle, une fausse API Proxmox et une fausse page Studio. **Coche verte** sur GitHub = on peut déployer ; **croix rouge** = ne pas déployer, ouvrir le détail pour voir ce qui casse.

En local : `pip install -r tests/requirements.txt && pytest -q`

## Licence

[MIT](LICENSE)
