"""
title: Correcteur - mémoire personnelle
description: Injecte le carnet de fautes de l'utilisateur dans le Correcteur, gère « retiens / oublie / carnet », s'appuie sur LanguageTool (pistes avant, relecture des fautes sûres après), met en forme les traductions et journalise les corrections pour l'apprentissage nocturne.
"""

import asyncio
import json
import os
import re
import time
import urllib.parse
from difflib import SequenceMatcher
import urllib.request
from datetime import datetime

from pydantic import BaseModel

DATA_DIR = "/app/backend/data/correcteur"
CARNET = os.path.join(DATA_DIR, "carnet.json")
JOURNAL = os.path.join(DATA_DIR, "corrections.jsonl")

# « retiens : payer = pied », « retiens payer -> pied », « apprends : x => y »
RE_RETIENS = re.compile(r"^\s*(?:retiens|apprends|note)\s*:?\s*(.+?)\s*(?:=>|->|→|=)\s*(.+?)\s*$", re.I | re.S)
RE_OUBLIE = re.compile(r"^\s*oublie\s*:?\s*(.+?)\s*$", re.I | re.S)
RE_CARNET = re.compile(r"^\s*(?:carnet|mon carnet|mes fautes)\s*\??\s*$", re.I)
# séparateur que le modèle écrit avant une traduction ; remplacé ici par une belle mise en forme
RE_TRADUCTION = re.compile(r"^\s*<<<\s*TRADUCTION\s*:\s*(.+?)\s*>>>\s*$", re.M)
DRAPEAUX = {"anglais": "🇬🇧", "espagnol": "🇪🇸", "allemand": "🇩🇪", "italien": "🇮🇹", "portugais": "🇵🇹",
            "néerlandais": "🇳🇱", "arabe": "🇸🇦", "chinois": "🇨🇳", "japonais": "🇯🇵", "russe": "🇷🇺", "polonais": "🇵🇱"}
# relecture après coup : seulement la typographie et les majuscules. Testé le 19/10/2026 sur 48 corrections : la
# grammaire de LanguageTool en aurait abîmé 4 (« contexte » → « contextes », « j'ai pas » → « je n'ai pas » : ton changé)
LT_SURES = {"TYPOGRAPHY", "CASING"}


def _load() -> list:
    try:
        with open(CARNET, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(carnet: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CARNET + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(carnet, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CARNET)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _reply_exactly(body: dict, text: str) -> dict:
    """Remplace le dernier message utilisateur par une consigne de réponse fixe."""
    body["messages"][-1]["content"] = (
        "Ne corrige rien. Réponds exactement et uniquement par le texte suivant, sans rien ajouter :\n\n" + text
    )
    return body


def _indices_phonetiques(texte: str, url: str) -> str:
    """Interroge le dictionnaire des sons (Lexique + espeak-ng) : mots réels qui sonnent comme ce qui est écrit."""
    if not url or not texte:
        return ""
    try:
        q = urllib.parse.urlencode({"texte": texte[:4000]})
        with urllib.request.urlopen(f"{url}/indices?{q}", timeout=4) as r:
            indices = json.load(r).get("indices", [])
    except Exception:  # noqa: BLE001 — service absent : on corrige sans indices
        return ""
    if not indices:
        return ""
    lignes = [f"- « {i['ecrit']} » [{i['son']}] ({i['type']}) : {', '.join(i['mots'])}" for i in indices[:25]]
    return (
        "\n\nDictionnaire des sons (calculé automatiquement sur le message : vrais mots français qui se prononcent "
        "comme ce que l'utilisateur a écrit). Ce sont des PISTES : utilise-les seulement si elles donnent une phrase "
        "logique dans le contexte, sinon ignore-les :\n" + "\n".join(lignes)
    )


def _languagetool(texte: str, url: str) -> list:
    """Fautes trouvées par LanguageTool (conteneur 113 sur le Proxmox) ; liste vide si le service ne répond pas."""
    if not url or not texte.strip():
        return []
    try:
        donnees = urllib.parse.urlencode({"text": texte[:15000], "language": "fr"}).encode()
        with urllib.request.urlopen(f"{url}/v2/check", data=donnees, timeout=6) as r:
            return json.load(r).get("matches", [])
    except Exception:  # noqa: BLE001 — service absent : on corrige sans lui
        return []


def _pistes_languagetool(texte: str, url: str) -> str:
    fautes = [f for f in _languagetool(texte, url) if f.get("replacements")]
    if not fautes:
        return ""
    lignes = []
    for f in fautes[:25]:
        ecrit = texte[f["offset"]:f["offset"] + f["length"]]
        propositions = ", ".join(r["value"] for r in f["replacements"][:3])
        lignes.append(f"- « {ecrit} » → {propositions} ({f.get('message', '')[:90]})")
    return (
        "\n\nRelecture LanguageTool du message brut (correcteur de grammaire classique : fiable pour les accords, "
        "conjugaisons et la ponctuation, MAUVAIS pour deviner les mots écrits au son et l'argot). Ce sont des PISTES :\n"
        + "\n".join(lignes)
    )


def _relire(texte: str, url: str) -> str:
    """Applique seulement les corrections sûres de LanguageTool : une seule proposition, typographie/majuscules,
    et un mot presque identique (on ne change jamais un mot pour un autre, donc jamais le sens)."""
    fautes = _languagetool(texte, url)
    for f in sorted(fautes, key=lambda x: x["offset"], reverse=True):
        reps = f.get("replacements") or []
        if len(reps) != 1 or f.get("rule", {}).get("category", {}).get("id") not in LT_SURES:
            continue
        ecrit, nouveau = texte[f["offset"]:f["offset"] + f["length"]], reps[0]["value"]
        if ecrit.strip() and SequenceMatcher(None, ecrit.lower(), nouveau.lower()).ratio() < 0.6:
            continue
        texte = texte[:f["offset"]] + nouveau + texte[f["offset"] + f["length"]:]
    return texte


def _mettre_en_forme(reponse: str, url: str) -> tuple[str, str]:
    """(texte affiché, partie française pour le journal). Relit le français ; met en forme une éventuelle traduction."""
    m = RE_TRADUCTION.search(reponse)
    if not m:
        fr = _relire(reponse.strip(), url)
        return fr, fr
    fr = _relire(reponse[:m.start()].strip(), url)
    langue, traduction = m.group(1).strip(), reponse[m.end():].strip()
    drapeau = DRAPEAUX.get(langue.lower(), "🌍")
    return f"🇫🇷 **Ce que j'ai compris :**\n\n{fr}\n\n{drapeau} **{langue.capitalize()} :**\n\n{traduction}", fr


class Filter:
    class Valves(BaseModel):
        max_entrees_injectees: int = 200
        url_phonetique: str = "http://127.0.0.1:8191"
        url_languagetool: str = "http://192.168.0.213:8081"
        messages_contexte: int = 3

    def __init__(self):
        self.valves = self.Valves()

    # Open WebUI appelle un filtre « def » directement dans sa boucle principale : pendant les appels réseau
    # (dictionnaire des sons, LanguageTool, jusqu'à 10 s), tout le serveur était bloqué, pour tous les chats.
    # En « async » + thread, il reste disponible.
    async def inlet(self, body: dict, __user__: dict = None) -> dict:
        return await asyncio.to_thread(self._inlet, body, __user__)

    async def outlet(self, body: dict, __user__: dict = None) -> dict:
        return await asyncio.to_thread(self._outlet, body, __user__)

    def _inlet(self, body: dict, __user__: dict = None) -> dict:
        messages = body.get("messages") or []
        if not messages or messages[-1].get("role") != "user":
            return body
        last = _text(messages[-1].get("content")).strip()
        carnet = _load()

        m = RE_RETIENS.match(last)
        if m:
            ecrit, voulu = m.group(1).strip(" «»\"'"), m.group(2).strip(" «»\"'")
            carnet = [e for e in carnet if e["ecrit"].lower() != ecrit.lower()]
            carnet.append({"ecrit": ecrit, "voulu": voulu, "source": "manuel", "date": datetime.now().strftime("%Y-%m-%d")})
            _save(carnet)
            return _reply_exactly(body, f"✅ Noté pour toujours : « {ecrit} » → « {voulu} »")

        m = RE_OUBLIE.match(last)
        if m:
            cible = m.group(1).strip(" «»\"'").lower()
            reste = [e for e in carnet if e["ecrit"].lower() != cible]
            if len(reste) == len(carnet):
                return _reply_exactly(body, f"Je n'avais rien noté pour « {m.group(1).strip()} ».")
            _save(reste)
            return _reply_exactly(body, f"🗑️ Oublié : « {m.group(1).strip()} »")

        if RE_CARNET.match(last):
            if not carnet:
                return _reply_exactly(body, "Ton carnet est vide pour l'instant.")
            lignes = [f"- « {e['ecrit']} » → « {e['voulu']} » ({e.get('source', '?')})" for e in carnet]
            return _reply_exactly(body, f"📒 Ton carnet ({len(carnet)} entrées) :\n" + "\n".join(lignes))

        bloc = ""
        if carnet:
            entrees = carnet[-self.valves.max_entrees_injectees:]
            bloc += (
                "\n\nCarnet personnel de cet utilisateur (fautes qu'il fait souvent, à appliquer en priorité, "
                "en généralisant aux formes proches) :\n"
                + "\n".join(f"- « {e['ecrit']} » → « {e['voulu']} »" for e in entrees)
            )
        bloc += _indices_phonetiques(last, self.valves.url_phonetique)
        bloc += _pistes_languagetool(last, self.valves.url_languagetool)
        if bloc:
            sys_msg = next((x for x in messages if x.get("role") == "system"), None)
            if sys_msg is not None and isinstance(sys_msg.get("content"), str):
                sys_msg["content"] += bloc
            else:
                messages.insert(0, {"role": "system", "content": bloc.strip()})

        # Le modèle ne voit QUE le texte à corriger : les anciennes corrections de la conversation lui faisaient
        # recopier des phrases d'avant. Les messages précédents de l'utilisateur restent comme simple contexte.
        precedents = [
            t for t in (_text(x.get("content")).strip() for x in messages[:-1] if x.get("role") == "user")
            if t and not (RE_RETIENS.match(t) or RE_OUBLIE.match(t) or RE_CARNET.match(t))
        ][-self.valves.messages_contexte:]
        contexte = ""
        if precedents:
            contexte = (
                "Messages précédents de l'utilisateur (CONTEXTE uniquement, pour comprendre de quoi il parle : "
                "ne les corrige pas, ne les recopie pas, n'en reprends aucune phrase) :\n"
                + "\n".join(f"- {t}" for t in precedents) + "\n\n"
            )
        body["messages"] = [x for x in messages if x.get("role") == "system"] + [
            {"role": "user", "content": contexte + "TEXTE À CORRIGER (renvoie uniquement sa version corrigée) :\n" + last}
        ]
        return body

    def _outlet(self, body: dict, __user__: dict = None) -> dict:
        messages = body.get("messages") or []
        if len(messages) < 2 or messages[-1].get("role") != "assistant":
            return body
        user_msg = next((x for x in reversed(messages[:-1]) if x.get("role") == "user"), None)
        if not user_msg:
            return body
        original = _text(user_msg.get("content")).strip()
        corrige = _text(messages[-1].get("content")).strip()
        if not original or not corrige or RE_RETIENS.match(original) or RE_OUBLIE.match(original) or RE_CARNET.match(original):
            return body
        if "**Ce que j'ai compris :**" not in corrige:  # pas déjà mis en forme
            affiche, corrige = _mettre_en_forme(corrige, self.valves.url_languagetool)
            if isinstance(messages[-1].get("content"), str):
                messages[-1]["content"] = affiche
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": int(time.time()), "user": (__user__ or {}).get("id"), "original": original, "corrige": corrige}, ensure_ascii=False) + "\n")
        return body
