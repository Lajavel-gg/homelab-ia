#!/usr/bin/env python3
"""Prépare Gitea une fois installé (à relancer sans risque, ne refait pas ce qui existe déjà) :
- organisation privée « projets » (les copies de TOUS les repos GitHub : sauvegarde et scans) ;
- « synchro » dans l'équipe Owners : crée les repos et écrit la branche principale (synchro_github.py) ;
- équipe « ia » (compte « ia », utilisé par OpenCode) : lire et écrire du code, des issues et des pull requests,
  mais SEULEMENT dans les repos de IA_REPOS ; ni créer, ni supprimer un repo, ni toucher à l'organisation.
  La branche principale lui est fermée par la protection posée par synchro_github.py.

Pourquoi une liste : la VM d'OpenCode a Internet. Une IA piégée pourrait envoyer dehors tout ce qu'elle peut lire ;
elle ne lit donc que les projets où elle travaille. Un repo retiré de la liste lui est retiré au passage suivant.

Usage, sur la VM Gitea, APRÈS le premier passage de la synchro (les repos doivent exister) :
  GITEA_TOKEN=<clé du compte admin gabe> IA_REPOS="garabou,homelab-ia" ./preparer.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("GITEA_URL", "http://127.0.0.1:3000").rstrip("/") + "/api/v1"
ORG = os.environ.get("GITEA_ORG", "projets")
UNITES = {"repo.code": "write", "repo.issues": "write", "repo.pulls": "write",
          "repo.actions": "read", "repo.releases": "read", "repo.wiki": "read"}


def api(methode, chemin, corps=None, ok=(200, 201, 204)):
    data = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(URL + chemin, data=data, method=methode, headers={
        "Authorization": "token " + os.environ["GITEA_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            brut = r.read()
            return r.status, json.loads(brut) if brut else None
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return e.code, None
        raise SystemExit(f"{methode} {chemin} : code {e.code} {e.read()[:300]!r}")


def tout(chemin):
    """Toutes les pages d'une liste de l'API."""
    out, page = [], 1
    while True:
        _, l = api("GET", f"{chemin}?limit=50&page={page}")
        out += l or []
        if not l or len(l) < 50:
            return out
        page += 1


def main():
    voulus = sorted({n.strip() for n in os.environ.get("IA_REPOS", "").split(",") if n.strip()})
    code, _ = api("GET", f"/orgs/{ORG}", ok=(200, 404))
    if code == 404:
        api("POST", "/orgs", {"username": ORG, "visibility": "private",
                              "repo_admin_change_team_access": False,
                              "description": "Copies des repos GitHub (synchro) + branches de travail des IA"})
        print(f"organisation {ORG} : créée")
    par_nom = {e["name"]: e for e in tout(f"/orgs/{ORG}/teams")}
    reglage = {"description": "OpenCode : branches ia/…, issues, pull requests, seulement dans IA_REPOS",
               "permission": "write", "includes_all_repositories": False, "can_create_org_repo": False,
               "units_map": UNITES}
    if "ia" not in par_nom:
        _, par_nom["ia"] = api("POST", f"/orgs/{ORG}/teams", {"name": "ia", **reglage})
        print("équipe ia : créée")
    elif par_nom["ia"].get("includes_all_repositories"):
        api("PATCH", f"/teams/{par_nom['ia']['id']}", {"name": "ia", **reglage})
        print("équipe ia : ne voit plus tous les repos, seulement IA_REPOS")
    for equipe, compte in (("Owners", "synchro"), ("ia", "ia")):
        api("PUT", f"/teams/{par_nom[equipe]['id']}/members/{compte}")
        print(f"{compte} -> équipe {equipe}")

    ia = par_nom["ia"]["id"]
    existants = {r["name"] for r in tout(f"/orgs/{ORG}/repos")}
    actuels = {r["name"] for r in tout(f"/teams/{ia}/repos")}
    for nom in voulus:
        if nom not in existants:
            print(f"{nom} : absent de Gitea (lancer la synchro d'abord, ou nom mal écrit)")
        elif nom not in actuels:
            api("PUT", f"/teams/{ia}/repos/{ORG}/{nom}")
            print(f"{nom} : ouvert à l'équipe ia")
    for nom in sorted(actuels - set(voulus)):
        api("DELETE", f"/teams/{ia}/repos/{ORG}/{nom}")
        print(f"{nom} : retiré à l'équipe ia")
    print("l'équipe ia voit : " + (", ".join(n for n in voulus if n in existants) or "aucun repo"))


if __name__ == "__main__":
    sys.exit(main())
