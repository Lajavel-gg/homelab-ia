#!/usr/bin/env python3
"""Synchro GitHub -> Gitea : la branche principale et les tags de chaque repo GitHub de Gabe sont recopiés dans
l'organisation Gitea « projets ». GitHub reste le chef : sur Gitea, la branche principale n'est écrite que par ce
script (compte « synchro », branche protégée) ; les IA travaillent sur leurs branches ia/… sans jamais la toucher.

- Le repo Gitea est créé s'il manque (privé), avec la protection de sa branche principale.
- Seules la branche principale et les tags sont copiés (pas les pull requests ni les autres branches de GitHub).
- Copie forcée : si l'historique a été réécrit sur GitHub, Gitea suit.
- Les clés ne passent jamais dans la ligne de commande (visible par `ps`) : elles vont à git par l'environnement.

Lancé toutes les 10 min par gitea-synchro.timer. Réglages : /etc/gitea-synchro.env (voir synchro.env.example).
Essai sans rien changer : synchro_github.py --essai
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

GITHUB_API = "https://api.github.com"


class Reglages:
    def __init__(self, env):
        self.github_token = env["GITHUB_TOKEN"]
        self.proprio = env.get("GITHUB_PROPRIO", "Lajavel-gg")
        self.github_api = env.get("GITHUB_API", GITHUB_API).rstrip("/")
        self.gitea_url = env.get("GITEA_URL", "http://127.0.0.1:3000").rstrip("/")
        self.gitea_git = env.get("GITEA_GIT", self.gitea_url).rstrip("/")  # adresse git (les tests mettent file://)
        self.gitea_token = env["GITEA_TOKEN"]
        self.gitea_user = env.get("GITEA_USER", "synchro")
        self.org = env.get("GITEA_ORG", "projets")
        self.cache = env.get("CACHE", "/var/lib/gitea-synchro")
        self.exclure = {n.strip().lower() for n in env.get("EXCLURE", "").split(",") if n.strip()}


def _appel(methode, url, entetes, corps=None):
    """Requête JSON. Renvoie (code, objet) ; un code d'erreur HTTP n'est pas une exception."""
    data = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(url, data=data, method=methode, headers=dict(entetes))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            brut = r.read()
            return r.status, (json.loads(brut) if brut else None)
    except urllib.error.HTTPError as e:
        brut = e.read()
        try:
            return e.code, json.loads(brut) if brut else None
        except ValueError:
            return e.code, brut.decode(errors="replace")[:300]


class GitHub:
    def __init__(self, r):
        self.r = r
        self.entetes = {"Authorization": "Bearer " + r.github_token, "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "gitea-synchro"}

    def repos(self):
        """Les repos dont Gabe est le propriétaire (publics et privés)."""
        out, page = [], 1
        while True:
            q = urllib.parse.urlencode({"affiliation": "owner", "per_page": 100, "page": page})
            code, l = _appel("GET", f"{self.r.github_api}/user/repos?{q}", self.entetes)
            if code != 200 or not isinstance(l, list):
                raise RuntimeError(f"GitHub : liste des repos impossible (code {code}) : {l}")
            out += [x for x in l if x.get("owner", {}).get("login", "").lower() == self.r.proprio.lower()]
            if len(l) < 100:
                return out
            page += 1


class Gitea:
    def __init__(self, r):
        self.r = r
        self.entetes = {"Authorization": "token " + r.gitea_token, "Accept": "application/json"}

    def _api(self, methode, chemin, corps=None):
        return _appel(methode, f"{self.r.gitea_url}/api/v1{chemin}", self.entetes, corps)

    def existe(self, nom):
        code, rep = self._api("GET", f"/repos/{self.r.org}/{nom}")
        if code not in (200, 404):
            raise RuntimeError(f"Gitea : lecture de {nom} impossible (code {code}) : {rep}")
        return code == 200

    def creer(self, nom, description, branche):
        code, rep = self._api("POST", f"/orgs/{self.r.org}/repos", {
            "name": nom, "private": True, "description": (description or "")[:2000], "default_branch": branche})
        if code != 201:
            raise RuntimeError(f"Gitea : création de {nom} refusée (code {code}) : {rep}")

    def proteger(self, nom, branche):
        """Branche principale : seul « synchro » écrit (et force) ; personne ne fusionne dans Gitea, on passe par
        GitHub. Ne fait rien si une règle existe déjà pour cette branche."""
        code, regles = self._api("GET", f"/repos/{self.r.org}/{nom}/branch_protections")
        if code != 200:
            raise RuntimeError(f"Gitea : protections de {nom} illisibles (code {code})")
        if any((x.get("rule_name") or x.get("branch_name")) == branche for x in regles or []):
            return False
        u = [self.r.gitea_user]
        code, rep = self._api("POST", f"/repos/{self.r.org}/{nom}/branch_protections", {
            "rule_name": branche,
            "enable_push": True, "enable_push_whitelist": True, "push_whitelist_usernames": u,
            "enable_force_push": True, "enable_force_push_allowlist": True, "force_push_allowlist_usernames": u,
            "enable_merge_whitelist": True, "merge_whitelist_usernames": [],
            "block_admin_merge_override": True,
        })
        if code != 201:
            raise RuntimeError(f"Gitea : protection de {nom} refusée (code {code}) : {rep}")
        return True


def _basic(user, token):
    return "Authorization: Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()


def env_git(r):
    """Environnement de git : clés en en-têtes HTTP par adresse, jamais de question, jamais de config globale."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    entetes = [("https://github.com/", _basic("x-access-token", r.github_token))]
    if r.gitea_git.startswith("http"):
        entetes.append((r.gitea_git + "/", _basic(r.gitea_user, r.gitea_token)))
    env["GIT_CONFIG_COUNT"] = str(len(entetes))
    for i, (url, valeur) in enumerate(entetes):
        env[f"GIT_CONFIG_KEY_{i}"] = f"http.{url}.extraHeader"
        env[f"GIT_CONFIG_VALUE_{i}"] = valeur
    return env


def _git(env, *args):
    p = subprocess.run(["git", *args], env=env, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(f"git {args[0]} : {(p.stderr or p.stdout).strip()[-400:]}")
    return p.stdout


def copier(r, env, nom, source, branche):
    """Récupère la branche principale et les tags de GitHub dans un dépôt nu local, puis les pousse vers Gitea.
    Renvoie False si le repo GitHub est vide (rien à copier)."""
    depot = os.path.join(r.cache, nom + ".git")
    if not os.path.isdir(depot):
        _git(env, "init", "--quiet", "--bare", depot)
    if not _git(env, "ls-remote", "--heads", source, branche).strip():
        return False
    _git(env, "-C", depot, "fetch", "--quiet", "--prune", "--force", "--no-tags", source,
         f"+refs/heads/{branche}:refs/heads/{branche}", "+refs/tags/*:refs/tags/*")
    cible = f"{r.gitea_git}/{r.org}/{nom}.git"
    _git(env, "-C", depot, "push", "--quiet", "--force", cible,
         f"+refs/heads/{branche}:refs/heads/{branche}", "+refs/tags/*:refs/tags/*")
    return True


def synchroniser(r, essai=False, sortie=print):
    """Renvoie le nombre de repos en erreur (0 = tout va bien)."""
    gh, gt = GitHub(r), Gitea(r)
    env = env_git(r)
    os.makedirs(r.cache, exist_ok=True)
    erreurs = 0
    for repo in sorted(gh.repos(), key=lambda x: x["name"].lower()):
        nom, branche = repo["name"], repo.get("default_branch") or "main"
        if nom.lower() in r.exclure:
            sortie(f"{nom} : exclu")
            continue
        try:
            nouveau = not gt.existe(nom)
            if essai:
                sortie(f"{nom} : {'à créer puis copier' if nouveau else 'à copier'} ({branche})")
                continue
            if nouveau:
                gt.creer(nom, repo.get("description"), branche)
            protege = gt.proteger(nom, branche)
            copie = copier(r, env, nom, repo["clone_url"], branche)
            etat = "créé" if nouveau else ("protégé" if protege else "à jour")
            sortie(f"{nom} : {etat}" + ("" if copie else " (vide sur GitHub, rien à copier)"))
        except Exception as e:  # un repo en panne n'arrête pas les autres
            erreurs += 1
            sortie(f"{nom} : ERREUR {e}")
    return erreurs


def lire_env(chemin):
    env = {}
    with open(chemin) as f:
        for ligne in f:
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                k, v = ligne.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main(argv):
    chemin = os.environ.get("SYNCHRO_ENV", "/etc/gitea-synchro.env")
    r = Reglages({**lire_env(chemin), **{k: v for k, v in os.environ.items() if k in (
        "GITHUB_TOKEN", "GITEA_TOKEN", "GITEA_URL", "GITEA_GIT", "GITEA_ORG", "CACHE", "EXCLURE")}})
    erreurs = synchroniser(r, essai="--essai" in argv)
    return 1 if erreurs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
