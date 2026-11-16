#!/usr/bin/env python3
"""Prépare Gitea une fois installé (à relancer sans risque, ne refait pas ce qui existe déjà) :
- organisation privée « projets » (les copies des repos GitHub) ;
- « synchro » dans l'équipe Owners : crée les repos et écrit la branche principale (synchro_github.py) ;
- équipe « ia » (compte « ia », utilisé par OpenCode et Hermes) : lire et écrire du code, des issues et des pull
  requests dans TOUS les repos de « projets », mais ni créer, ni supprimer un repo, ni toucher à l'organisation.
  La branche principale lui est fermée par la protection posée par synchro_github.py.

Usage, sur la VM Gitea : GITEA_TOKEN=<clé du compte admin gabe> ./preparer.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("GITEA_URL", "http://127.0.0.1:3000").rstrip("/") + "/api/v1"
ORG = os.environ.get("GITEA_ORG", "projets")


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


def main():
    code, _ = api("GET", f"/orgs/{ORG}", ok=(200, 404))
    if code == 404:
        api("POST", "/orgs", {"username": ORG, "visibility": "private",
                              "repo_admin_change_team_access": False,
                              "description": "Copies des repos GitHub (synchro) + branches de travail des IA"})
        print(f"organisation {ORG} : créée")
    _, equipes = api("GET", f"/orgs/{ORG}/teams")
    par_nom = {e["name"]: e for e in equipes}
    if "ia" not in par_nom:
        _, par_nom["ia"] = api("POST", f"/orgs/{ORG}/teams", {
            "name": "ia", "description": "OpenCode et Hermes : branches ia/…, issues, pull requests",
            "permission": "write", "includes_all_repositories": True, "can_create_org_repo": False,
            "units_map": {"repo.code": "write", "repo.issues": "write", "repo.pulls": "write",
                          "repo.actions": "read", "repo.releases": "read", "repo.wiki": "read"}})
        print("équipe ia : créée")
    for equipe, compte in (("Owners", "synchro"), ("ia", "ia")):
        api("PUT", f"/teams/{par_nom[equipe]['id']}/members/{compte}")
        print(f"{compte} -> équipe {equipe}")


if __name__ == "__main__":
    sys.exit(main())
