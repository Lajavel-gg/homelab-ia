# Gitea : le bac à sable des IA

Une VM Proxmox avec [Gitea](https://about.gitea.com), un « GitHub maison ». Les IA (OpenCode, Hermes) y travaillent
librement ; **GitHub reste le chef** : rien n'y arrive sans que Gabe le décide.

```
GitHub (le chef) ──synchro toutes les 10 min──> Gitea « projets »  (main = copie exacte de GitHub, fermée aux IA)
                                                    │
                     OpenCode / Hermes (compte ia) ─┤  branches ia/…, issues, pull requests
                                                    │
                     Ulysse : Gabe voit le diff et les tests ─> « Envoyer sur GitHub » ─> relecture Claude Code ─> Merge
```

## Qui a le droit de faire quoi

Vérifié le 17/11 sur un vrai Gitea 1.27.3, en jouant le rôle de l'IA :

| Action | Compte `ia` (OpenCode, Hermes) | Compte `synchro` |
|---|---|---|
| Lire et cloner un repo de `projets` | ✅ seulement ceux de `IA_REPOS` | ✅ tous |
| Pousser une branche `ia/…` | ✅ | ✅ |
| Ouvrir une pull request, une issue | ✅ | ✅ |
| Pousser ou forcer `main` | ❌ refusé par Gitea | ✅ (seul compte permis) |
| Fusionner une pull request dans Gitea | ❌ (personne : on passe par GitHub) | ❌ |
| Enlever la protection de `main` | ❌ | ✅ |
| Créer ou supprimer un repo | ❌ | ✅ créer (pour la synchro) |
| Écrire sur GitHub | ❌ aucune clé | ❌ clé GitHub en **lecture seule** |

Donc une IA piégée peut au pire salir ses propres branches `ia/…` : `main` reste la copie exacte de GitHub, et GitHub
n'est jamais touché. Le vrai risque restant : la VM d'OpenCode a Internet, donc une IA piégée peut **envoyer dehors
le code qu'elle lit**. C'est pour ça que Gitea copie **tous** les repos (sauvegarde, scans) mais que l'équipe `ia` ne
voit **que** ceux de `IA_REPOS` (vérifié : les autres répondent 404, même pour un clone). Mettre dans la liste les
projets où les IA travaillent, pas les repos privés sensibles.

## Fichiers

| Fichier | Rôle |
|---|---|
| [`compose.yaml`](compose.yaml) | Gitea 1.27.3 (sans SSH, inscription fermée, tout privé, rien de visible sans compte) + Caddy en HTTPS sur 3443 |
| [`Caddyfile`](Caddyfile) | HTTPS avec un certificat de la CA locale du Spark, comme LiteLLM |
| [`.env.example`](.env.example) | Secrets de Gitea |
| [`preparer.py`](preparer.py) | Crée l'organisation `projets` et l'équipe `ia`, range `synchro` et `ia`, et ouvre à `ia` **seulement** les repos de `IA_REPOS` |
| [`synchro_github.py`](synchro_github.py) | Copie `main` et les tags de chaque repo GitHub vers Gitea, crée et protège les repos qui manquent |
| [`synchro.env.example`](synchro.env.example) | Réglages de la synchro (clés GitHub et Gitea) |
| [`gitea-synchro.service`](gitea-synchro.service), [`.timer`](gitea-synchro.timer) | La synchro toutes les 10 minutes |
| [`mettre-cle-github`](mettre-cle-github) | Met ou remplace la clé GitHub dans `/etc/gitea-synchro.env` **sans l'afficher** |

## Installation (dans l'ordre)

### 1. La VM

VM Debian sur Proxmox : 2 cœurs, 4 Go, 50 Go de disque. Installer `docker`, `git`, `python3` et `qemu-guest-agent`
(sans l'agent, Proxmox ne voit pas l'adresse de la VM et les sauvegardes ne peuvent pas la figer proprement).

Pare-feu Proxmox de la VM :
- **entrée** 3443 seulement depuis la VM OpenCode (115), la VM Hermes (121), la VM d'Ulysse et le VPN ;
- **sortie** seulement vers `github.com` / `api.github.com` (la synchro) et les mises à jour.

Sauvegarde : un **snapshot Proxmox chaque nuit** (si une IA a tout cassé, on revient en arrière en un clic).

### 2. Gitea

```sh
sudo mkdir -p /opt/gitea && cd /opt/gitea      # y copier compose.yaml, Caddyfile, .env.example
sudo chown root:root compose.yaml Caddyfile .env.example   # copiés par « debian » : les rendre à root
sudo cp .env.example .env && sudo chmod 600 .env   # remplir GITEA_DOMAINE et les deux secrets (commandes dans le fichier)
sudo mkdir -p data config tls && sudo chown -R 1000:1000 data config
# tls/gitea.crt et tls/gitea.key : certificat signé par la CA locale du Spark (comme pour LiteLLM)
docker compose up -d
```

### 3. Les comptes

```sh
G="docker compose exec gitea gitea"
$G admin user create --admin --username gabe --email gabe@localhost --random-password
# Les deux robots : --must-change-password=false, sinon Gitea refuse leurs clés tant que le mot de passe n'a pas changé
$G admin user create --username synchro --email synchro@localhost --random-password --must-change-password=false
$G admin user create --username ia --email ia@localhost --random-password --must-change-password=false
# Les clés (à mettre dans Vaultwarden) :
$G admin user generate-access-token --username gabe    --token-name admin   --scopes all --raw
$G admin user generate-access-token --username synchro --token-name synchro --scopes write:repository,write:organization --raw
$G admin user generate-access-token --username ia      --token-name ia      --scopes write:repository,write:issue --raw
```

Puis, avec la clé de `gabe` (relançable sans risque) :

```sh
GITEA_TOKEN=<clé de gabe> python3 preparer.py
```

L'équipe `ia` ne voit encore aucun repo : on lui ouvre les siens **après** le premier passage de la synchro
(étape 4), quand les repos existent dans Gitea.

### 4. La synchro avec GitHub

Sur GitHub : *Settings → Developer settings → Fine-grained tokens → Generate*
- *Repository access* : **All repositories** (les nouveaux repos sont pris automatiquement) ;
- *Permissions* : **Contents : Read-only** et **Metadata : Read-only**. **Rien d'autre.**
- Noter la date d'expiration dans Vaultwarden pour la renouveler.

```sh
sudo useradd --system --home /var/lib/gitea-synchro gitea-synchro
sudo install -d -m 700 -o gitea-synchro -g gitea-synchro /var/lib/gitea-synchro   # le cache (systemd le crée aussi, mais pas l'essai à la main)
sudo install -m 755 synchro_github.py mettre-cle-github /usr/local/bin/
sudo install -m 600 -o gitea-synchro synchro.env.example /etc/gitea-synchro.env   # puis remplir GITEA_TOKEN…
sudo mettre-cle-github      # colle la clé GitHub à l'aveugle : elle ne s'affiche jamais
sudo -u gitea-synchro SYNCHRO_ENV=/etc/gitea-synchro.env /usr/local/bin/synchro_github.py --essai   # ne change rien
sudo cp gitea-synchro.service gitea-synchro.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now gitea-synchro.timer
journalctl -u gitea-synchro -n 30     # « garabou : à jour », « homelab-ia : à jour »…
```

Enfin, ouvrir à l'équipe `ia` les repos où les IA travaillent (relancer avec une autre liste pour en ajouter ou en
retirer ; un repo absent de la liste lui est retiré) :

```sh
GITEA_TOKEN=<clé de gabe> IA_REPOS="garabou,homelab-ia" python3 preparer.py
```

Ce que fait la synchro, à chaque passage :
- elle **crée** dans `projets` (en privé) chaque repo GitHub qui manque, avec la protection de `main` ;
- elle copie **seulement** la branche principale et les tags. Les branches `ia/…` des IA ne sont jamais touchées ;
- si l'historique a été réécrit sur GitHub, Gitea suit (copie forcée) ;
- un repo en panne n'arrête pas les autres, et l'erreur est écrite dans le journal.

## La suite du plan

1. ~~VM Gitea, comptes, `main` protégé~~ (ce dossier)
2. ~~Synchro GitHub → Gitea~~ (ce dossier)
3. **Gitea Actions** : un runner qui lance les tests à chaque push d'une IA (coche verte avant d'aller sur GitHub).
4. **Ulysse** : la liste des projets vient de Gitea, et un écran montre les pull requests, le diff et les tests.
5. **Bouton « Envoyer sur GitHub »** : pousse la branche `ia/…` sur GitHub et ouvre la pull request ; Claude Code
   relit, Gabe fusionne. Une seule clé GitHub, qui ne sait que pousser des branches.
6. **Hermes** : scan de nuit (gitleaks, osv-scanner, govulncheck) qui ouvre une issue dans Gitea pour chaque faille.
