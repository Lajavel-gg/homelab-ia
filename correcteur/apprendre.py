#!/usr/bin/env python3
"""Apprentissage nocturne du Correcteur : extrait les fautes récurrentes du journal et les ajoute au carnet."""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime

DATA = "/var/lib/docker/volumes/ai-stack_open-webui/_data/correcteur"
CARNET = os.path.join(DATA, "carnet.json")
JOURNAL = os.path.join(DATA, "corrections.jsonl")
ETAT = os.path.join(DATA, "apprentissage-etat.json")
LLM = "http://127.0.0.1:8090/v1/chat/completions"
MAX_PAIRES = 80
MAX_AUTO = 300
MIN_MEM_GO = 15


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def mem_dispo_go():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) // 1048576
    return 0


def charger(path, defaut):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return defaut


def sauver(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    if not os.path.exists(JOURNAL):
        log("aucun journal, rien à apprendre")
        return
    with open(JOURNAL, encoding="utf-8") as f:
        lignes = f.readlines()
    etat = charger(ETAT, {"lignes_traitees": 0})
    nouvelles = lignes[etat["lignes_traitees"]:]
    paires = []
    for l in nouvelles:
        try:
            p = json.loads(l)
        except json.JSONDecodeError:
            continue
        if p.get("original") and p.get("corrige") and p["original"].strip() != p["corrige"].strip():
            paires.append(p)
    if len(paires) < 3:
        log(f"{len(paires)} nouvelle(s) correction(s), pas assez pour apprendre (min 3)")
        return
    if mem_dispo_go() < MIN_MEM_GO:
        log(f"mémoire disponible trop basse ({mem_dispo_go()} Go), on réessaiera demain")
        return

    carnet = charger(CARNET, [])
    connus = "\n".join(f"- {e['ecrit']} → {e['voulu']}" for e in carnet)
    exemples = "\n".join(f"ORIGINAL: {p['original']}\nCORRIGÉ: {p['corrige']}\n" for p in paires[-MAX_PAIRES:])
    consigne = (
        "Voici des textes écrits par une même personne (ORIGINAL) et leur version corrigée (CORRIGÉ).\n"
        "Repère les fautes PERSONNELLES et RÉCURRENTES de cette personne : mots ou tournures qu'elle écrit mal "
        "de façon caractéristique (phonétique, homophones, mots déformés, abréviations).\n"
        "Ignore les fautes banales isolées et la ponctuation. N'invente rien : chaque entrée doit venir des textes.\n"
        "Le « voulu » doit garder EXACTEMENT le sens voulu : ne remplace jamais l'argot ou les mots familiers volontaires "
        "(ex. « pulse », « gaffe », « magouiller », « tqt ») par d'autres mots, corrige seulement leur orthographe. "
        "Si tu n'es pas sûr du sens, n'ajoute pas l'entrée.\n"
        "Ne répète pas ce qui est déjà dans le carnet existant.\n\n"
        f"Carnet existant :\n{connus or '(vide)'}\n\nTextes :\n{exemples}\n"
        'Réponds UNIQUEMENT par un tableau JSON, maximum 15 entrées : [{"ecrit": "...", "voulu": "..."}]. '
        "Si rien de nouveau : []"
    )
    req = {
        "model": "qwen3.8-flash-next",
        "messages": [{"role": "user", "content": consigne}],
        "temperature": 0.1,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = urllib.request.urlopen(
        urllib.request.Request(LLM, data=json.dumps(req).encode(), headers={"content-type": "application/json"}),
        timeout=600,
    )
    texte = json.load(r)["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", texte, re.S)
    try:
        propositions = json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        log(f"réponse non exploitable : {texte[:200]!r}")
        return

    deja = {e["ecrit"].lower() for e in carnet}
    ajouts = []
    for p in propositions:
        ecrit, voulu = str(p.get("ecrit", "")).strip(), str(p.get("voulu", "")).strip()
        if not ecrit or not voulu or ecrit.lower() == voulu.lower() or len(ecrit) > 60 or len(voulu) > 60:
            continue
        if ecrit.lower() in deja:
            continue
        deja.add(ecrit.lower())
        ajouts.append({"ecrit": ecrit, "voulu": voulu, "source": "auto", "date": datetime.now().strftime("%Y-%m-%d")})

    # Recharger juste avant d'écrire (le filtre a pu ajouter une entrée « retiens » entre-temps)
    carnet = charger(CARNET, []) + ajouts
    autos = [e for e in carnet if e.get("source") == "auto"]
    if len(autos) > MAX_AUTO:
        trop = {id(e) for e in autos[: len(autos) - MAX_AUTO]}
        carnet = [e for e in carnet if id(e) not in trop]
    sauver(CARNET, carnet)
    etat["lignes_traitees"] = len(lignes)
    sauver(ETAT, etat)
    log(f"{len(paires)} corrections analysées, {len(ajouts)} nouvelle(s) entrée(s) : "
        + ", ".join(f"{a['ecrit']}→{a['voulu']}" for a in ajouts))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"erreur : {e}")
        sys.exit(1)
