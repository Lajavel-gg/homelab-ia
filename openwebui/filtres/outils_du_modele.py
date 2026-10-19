"""
title: Outils du modèle toujours actifs
description: Ajoute toujours les outils propres au modèle (meta.toolIds), même si l'appli ne les a pas cochés (page pas rechargée, mobile).
version: 1.1
"""
# Créé le 19/10/2026 : sur mobile, un outil créé après le chargement de la page n'était pas envoyé → le modèle ne le voyait pas.
# Open WebUI relit body["tool_ids"] après les filtres d'entrée : c'est donc là qu'il faut les ajouter.


class Filter:
    def __init__(self):
        pass

    def inlet(self, body: dict, __model__: dict = None) -> dict:
        propres = ((__model__ or {}).get("info") or {}).get("meta", {}).get("toolIds") or []
        if propres:
            actuels = list(body.get("tool_ids") or [])
            body["tool_ids"] = actuels + [t for t in propres if t not in actuels]
        return body
