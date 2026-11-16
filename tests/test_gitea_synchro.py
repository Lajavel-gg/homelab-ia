"""Synchro GitHub -> Gitea (gitea/synchro_github.py) : faux GitHub et faux Gitea, vrais dépôts git locaux."""
import json
import os
import subprocess
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "gitea"))
import synchro_github as sg  # noqa: E402


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                                   GIT_COMMITTER_EMAIL="t@t")).stdout.strip()


def depot_github(tmp_path, nom, branche="main", vide=False):
    """Un « repo GitHub » : dépôt nu local, avec un commit, un tag et une branche de travail à ne pas copier."""
    nu = tmp_path / "github" / f"{nom}.git"
    git("init", "-q", "--bare", "-b", branche, str(nu))
    if not vide:
        w = tmp_path / "travail" / nom
        git("init", "-q", "-b", branche, str(w))
        (w / "a.txt").write_text("v1\n")
        git("add", "-A", cwd=w)
        git("commit", "-qm", "v1", cwd=w)
        git("tag", "v1", cwd=w)
        git("push", "-q", str(nu), branche, "v1", cwd=w)
        git("push", "-q", str(nu), f"{branche}:refs/heads/autre", cwd=w)
    return nu


class Faux:
    """Faux GitHub + faux Gitea. Gitea crée un vrai dépôt nu à la création d'un repo."""

    def __init__(self, faux_http, tmp_path, repos):
        self.tmp, self.repos = tmp_path, repos
        self.crees, self.protections, self.entetes = [], {}, []
        self.gitea_git = tmp_path / "gitea"
        f = self

        def liste(corps, h):
            f.entetes.append(("github", h.headers.get("Authorization")))
            return 200, [{"name": n, "default_branch": b, "description": "d", "owner": {"login": p},
                          "clone_url": "file://" + str(tmp_path / "github" / f"{n}.git")} for n, b, p in f.repos]

        routes = {("GET", "/user/repos"): liste, ("POST", "/api/v1/orgs/projets/repos"): self.creer}
        for n, _, _ in repos:
            routes[("GET", f"/api/v1/repos/projets/{n}")] = self.lire(n)
            routes[("GET", f"/api/v1/repos/projets/{n}/branch_protections")] = self.regles(n)
            routes[("POST", f"/api/v1/repos/projets/{n}/branch_protections")] = self.proteger(n)
        self.srv = faux_http(routes)

    def lire(self, n):
        def f(corps, h):
            self.entetes.append(("gitea", h.headers.get("Authorization")))
            return (200, {"name": n}) if n in self.crees else (404, {})
        return f

    def creer(self, corps, h):
        d = json.loads(corps)
        self.crees.append(d["name"])
        git("init", "-q", "--bare", "-b", d["default_branch"], str(self.gitea_git / "projets" / f"{d['name']}.git"))
        return 201, d

    def regles(self, n):
        return lambda corps, h: (200, [self.protections[n]] if n in self.protections else [])

    def proteger(self, n):
        def f(corps, h):
            self.protections[n] = json.loads(corps)
            return 201, self.protections[n]
        return f

    def reglages(self, **plus):
        return sg.Reglages({"GITHUB_TOKEN": "gh-secret", "GITHUB_API": self.srv.url, "GITEA_URL": self.srv.url,
                            "GITEA_GIT": "file://" + str(self.gitea_git), "GITEA_TOKEN": "gt-secret",
                            "CACHE": str(self.tmp / "cache"), **plus})

    def refs_gitea(self, n):
        return git("--git-dir", str(self.gitea_git / "projets" / f"{n}.git"), "show-ref")


def test_nouveau_repo_cree_protege_et_copie(faux_http, tmp_path):
    nu = depot_github(tmp_path, "garabou")
    f = Faux(faux_http, tmp_path, [("garabou", "main", "Lajavel-gg")])
    lignes = []
    assert sg.synchroniser(f.reglages(), sortie=lignes.append) == 0
    assert lignes == ["garabou : créé"]
    assert f.crees == ["garabou"]
    # main et le tag sont copiés, pas la branche « autre » de GitHub
    refs = f.refs_gitea("garabou")
    assert git("--git-dir", str(nu), "rev-parse", "main") in refs
    assert "refs/heads/main" in refs and "refs/tags/v1" in refs and "refs/heads/autre" not in refs
    # seule « synchro » écrit sur main, personne ne fusionne dans Gitea
    p = f.protections["garabou"]
    assert p["rule_name"] == "main" and p["push_whitelist_usernames"] == ["synchro"]
    assert p["enable_push_whitelist"] and p["enable_merge_whitelist"] and p["merge_whitelist_usernames"] == []
    assert p["force_push_allowlist_usernames"] == ["synchro"] and p["block_admin_merge_override"]
    # les clés passent en en-tête HTTP, chacune vers son serveur
    assert ("github", "Bearer gh-secret") in f.entetes and ("gitea", "token gt-secret") in f.entetes


def test_deuxieme_passage_a_jour_puis_reecriture_suivie(faux_http, tmp_path):
    nu = depot_github(tmp_path, "space", branche="master")
    f = Faux(faux_http, tmp_path, [("space", "master", "Lajavel-gg")])
    sg.synchroniser(f.reglages(), sortie=lambda _: None)
    lignes = []
    assert sg.synchroniser(f.reglages(), sortie=lignes.append) == 0
    assert lignes == ["space : à jour"] and f.crees == ["space"]
    # historique réécrit sur GitHub (commit remplacé) : Gitea suit
    w = tmp_path / "reecrit"
    git("init", "-q", "-b", "master", str(w))
    (w / "b.txt").write_text("autre\n")
    git("add", "-A", cwd=w)
    git("commit", "-qm", "réécrit", cwd=w)
    git("push", "-q", "--force", str(nu), "master", cwd=w)
    assert sg.synchroniser(f.reglages(), sortie=lambda _: None) == 0
    assert git("--git-dir", str(nu), "rev-parse", "master") in f.refs_gitea("space")


def test_exclu_vide_autre_proprio_et_essai(faux_http, tmp_path):
    depot_github(tmp_path, "vide", vide=True)
    depot_github(tmp_path, "Lajavel-gg")
    f = Faux(faux_http, tmp_path, [("vide", "main", "Lajavel-gg"), ("Lajavel-gg", "main", "Lajavel-gg"),
                                   ("fork-dun-autre", "main", "quelquun")])
    lignes = []
    assert sg.synchroniser(f.reglages(EXCLURE="lajavel-gg"), essai=True, sortie=lignes.append) == 0
    assert lignes == ["Lajavel-gg : exclu", "vide : à créer puis copier (main)"] and f.crees == []
    lignes = []
    assert sg.synchroniser(f.reglages(EXCLURE="lajavel-gg"), sortie=lignes.append) == 0
    assert lignes == ["Lajavel-gg : exclu", "vide : créé (vide sur GitHub, rien à copier)"]


def test_une_erreur_n_arrete_pas_les_autres(faux_http, tmp_path):
    depot_github(tmp_path, "bon")
    f = Faux(faux_http, tmp_path, [("absent", "main", "Lajavel-gg"), ("bon", "main", "Lajavel-gg")])
    lignes = []
    assert sg.synchroniser(f.reglages(), sortie=lignes.append) == 1  # « absent » n'existe pas sur GitHub
    assert lignes[0].startswith("absent : ERREUR") and lignes[1] == "bon : créé"


def test_cles_jamais_dans_la_ligne_de_commande(tmp_path):
    r = sg.Reglages({"GITHUB_TOKEN": "gh-secret", "GITEA_TOKEN": "gt-secret", "CACHE": str(tmp_path)})
    env = sg.env_git(r)
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert env["GIT_CONFIG_KEY_1"] == "http.http://127.0.0.1:3000/.extraHeader"
    # x-access-token:gh-secret et synchro:gt-secret en Basic (base64)
    assert env["GIT_CONFIG_VALUE_0"].endswith("eC1hY2Nlc3MtdG9rZW46Z2gtc2VjcmV0")
    assert env["GIT_CONFIG_VALUE_1"].endswith("c3luY2hybzpndC1zZWNyZXQ=")
