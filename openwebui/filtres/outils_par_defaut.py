"""
title: Outils par défaut
description: Active d'office la recherche web, la génération d'images et l'interpréteur de code (le modèle décide seul de s'en servir en mode natif), quel que soit l'état des boutons de l'interface.
"""

from pydantic import BaseModel


class Filter:
    class Valves(BaseModel):
        web_search: bool = True
        image_generation: bool = True
        code_interpreter: bool = True

    def __init__(self):
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: dict = None) -> dict:
        features = body.get("features") or {}
        for key in ("web_search", "image_generation", "code_interpreter"):
            if getattr(self.valves, key):
                features[key] = True
        body["features"] = features
        return body
