"""
title: Recherche web activée
description: Active toujours la recherche web (SearXNG local) pour le modèle auquel ce filtre est attaché.
version: 1.0
"""
# Créé le 19/10/2026 pour le modèle 📈 Trading : sur mobile, les boutons de fonctions ne sont pas toujours appliqués.


class Filter:
    def __init__(self):
        pass

    def inlet(self, body: dict, __user__: dict = None) -> dict:
        features = body.get("features") or {}
        features["web_search"] = True
        body["features"] = features
        return body
