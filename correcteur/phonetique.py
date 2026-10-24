#!/usr/bin/env python3
"""Dictionnaire des sons pour le Correcteur.

Pour un texte écrit « au son », trouve les vrais mots français qui se prononcent pareil :
  - mots inconnus (« caude » → code) ;
  - groupes de deux mots qui forment un seul mot (« chaque une » → chacune).
Prononciation : espeak-ng (n'importe quelle graphie) ; vocabulaire : Lexique 3.83 (lexique.org).

GET /indices?texte=...   → {"indices": [{"ecrit": "caude", "son": "kod", "mots": ["code", ...]}, ...]}
GET /sons?texte=...      → mots ayant la même prononciation que le texte entier
"""

import json
import re
import subprocess
import unicodedata
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LEXIQUE = "/opt/ai-stack/phonetique/data/Lexique383.tsv"
PORT = 8191

# Code phonétique de Lexique → clé simplifiée (on fusionne les nuances que l'orthographe phonétique ne distingue pas)
LEX2KEY = {"O": "o", "E": "e", "9": "2", "°": "", "1": "5", "A": "a"}
# API d'espeak-ng → même clé
IPA2KEY = [
    ("ɔ̃", "§"), ("ɑ̃", "@"), ("ɛ̃", "5"), ("œ̃", "5"), ("ɔ", "o"), ("ɛ", "e"), ("ø", "2"), ("œ", "2"),
    ("ə", ""), ("ʁ", "R"), ("ɹ", "R"), ("ʃ", "S"), ("ʒ", "Z"), ("ɲ", "N"), ("ŋ", "G"), ("ɥ", "8"),
    ("ɑ", "a"), ("ɡ", "g"), ("ʊ", "u"), ("ɪ", "i"), ("ʌ", "2"), ("æ", "a"),
]
KEEP = set("pbtdkgfvszmnlRSZNG8jwiyueo2a§@5")


def lex_key(phon):
    return "".join(LEX2KEY.get(c, c) for c in phon)


def ipa_key(ipa):
    s = re.sub(r"\([a-z]+\)", "", ipa)  # marqueurs de langue d'espeak, ex. (en)…(fr)
    s = unicodedata.normalize("NFC", s)
    for a, b in IPA2KEY:
        s = s.replace(a, b)
    return "".join(c for c in s if c in KEEP)


print("Chargement de Lexique…", flush=True)
PAR_SON = defaultdict(dict)  # clé → {mot: fréquence}
MOTS = set()
with open(LEXIQUE, encoding="utf-8") as f:
    entete = f.readline().rstrip("\n").split("\t")
    i_o, i_p, i_f = entete.index("ortho"), entete.index("phon"), entete.index("freqfilms2")
    for ligne in f:
        c = ligne.rstrip("\n").split("\t")
        mot, freq = c[i_o], float(c[i_f] or 0)
        MOTS.add(mot)
        k = lex_key(c[i_p])
        PAR_SON[k][mot] = max(PAR_SON[k].get(mot, 0), freq)
CLES = list(PAR_SON)
CLES_PAR_LONGUEUR = defaultdict(list)  # longueur → clés : on ne compare qu'aux clés de longueur ±1
for _k in CLES:
    CLES_PAR_LONGUEUR[len(_k)].append(_k)


def sans_accents(m):
    return "".join(c for c in unicodedata.normalize("NFD", m.lower()) if unicodedata.category(c) != "Mn")


FREQ = defaultdict(float)  # mot → fréquence max
for _k, _d in PAR_SON.items():
    for _m, _f in _d.items():
        FREQ[_m] = max(FREQ[_m], _f)
PAR_LONGUEUR = defaultdict(list)  # longueur → [(mot sans accents, mot)] pour les mots assez courants
for _m, _f in FREQ.items():
    if _f >= 0.3 and " " not in _m:
        PAR_LONGUEUR[len(_m)].append((sans_accents(_m), _m))


def levenshtein(a, b, maxd):
    if abs(len(a) - len(b)) > maxd:
        return maxd + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        mini = i
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if j > 1 and i > 1 and ca == b[j - 2] and a[i - 2] == cb:  # lettres inversées
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            mini = min(mini, cur[j])
        if mini > maxd:
            return maxd + 1
        prev2, prev = prev, cur
    return prev[-1]


def ecrits_proches(mot, n=5):
    """Mots qui s'écrivent presque pareil (fautes de frappe : lettre oubliée, en trop, inversée)."""
    m = sans_accents(mot)
    maxd = 1 if len(m) <= 4 else 2
    cands = []
    for L in range(len(m) - maxd, len(m) + maxd + 1):
        for sa, vrai in PAR_LONGUEUR.get(L, ()):
            d = levenshtein(m, sa, maxd)
            if d <= maxd:
                cands.append((d, -FREQ[vrai], vrai))
    cands.sort()
    return [v for _, _, v in cands[:n]]
print(f"{len(MOTS)} mots, {len(PAR_SON)} prononciations", flush=True)


def prononcer(textes):
    """Une prononciation (clé) par texte, en un seul appel à espeak-ng."""
    if not textes:
        return []
    out = subprocess.run(["espeak-ng", "-v", "fr", "-q", "--ipa"], input="\n".join(textes) + "\n",
                         capture_output=True, text=True, timeout=20).stdout.strip().split("\n")
    out = [ipa_key(o) for o in out]
    return out if len(out) == len(textes) else [ipa_key(subprocess.run(
        ["espeak-ng", "-v", "fr", "-q", "--ipa", t], capture_output=True, text=True, timeout=10).stdout) for t in textes]


def proches(cle, n=6):
    """Mots qui se prononcent exactement pareil, sinon à un son près."""
    exact = sorted(PAR_SON.get(cle, {}).items(), key=lambda x: -x[1])
    if exact:
        return [m for m, _ in exact[:n]], "même son"
    if len(cle) < 3:
        return [], ""
    cands = {}
    for k in (k for L in (len(cle) - 1, len(cle), len(cle) + 1) for k in CLES_PAR_LONGUEUR.get(L, ())):
        if distance1(k, cle):
            for m, fq in PAR_SON[k].items():
                cands[m] = max(cands.get(m, 0), fq)
    best = sorted(cands.items(), key=lambda x: -x[1])
    return [m for m, f in best[:n] if f > 0.5], "son proche"


def distance1(a, b):
    """Vrai si a et b diffèrent d'au plus une substitution, insertion ou suppression."""
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):
        if b[:i] + b[i + 1:] == a:
            return True
    return False


MOT_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿœŒ]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿœŒ]+)?")


def indices(texte):
    mots = MOT_RE.findall(texte)
    res, vus = [], set()
    # 1) mots inconnus de Lexique
    def connu(m):
        m = m.lower().replace("’", "'")
        return m in MOTS or ("'" in m and m.split("'", 1)[1] in MOTS)

    inconnus = [m for m in mots if not connu(m) and len(m) > 2]
    inconnus_set = {m.lower() for m in inconnus}
    # 2) paires de mots qui, collées, sonnent comme un seul mot courant (« chaque une » → chacune)
    paires = [f"{a} {b}" for a, b in zip(mots, mots[1:]) if a.lower() not in inconnus_set and b.lower() not in inconnus_set]
    cles = prononcer(inconnus + paires)
    for m, k in zip(inconnus, cles[: len(inconnus)]):
        if m.lower() in vus:
            continue
        vus.add(m.lower())
        cand, type_ = proches(k)
        if cand:
            res.append({"ecrit": m, "son": k, "type": type_, "mots": cand})
        frappe = [w for w in ecrits_proches(m) if w not in cand]
        if frappe:
            res.append({"ecrit": m, "son": k, "type": "écriture proche (faute de frappe ?)", "mots": frappe})
    for p, k in zip(paires, cles[len(inconnus):]):
        uns = [(w, f) for w, f in PAR_SON.get(k, {}).items() if f >= 3 and len(w) >= 5 and " " not in w]
        if uns and p.lower() not in vus:
            vus.add(p.lower())
            res.append({"ecrit": p, "son": k, "type": "un seul mot ?", "mots": [w for w, _ in sorted(uns, key=lambda x: -x[1])[:3]]})
    return res


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        texte = (parse_qs(u.query).get("texte") or [""])[0][:4000]
        if u.path == "/indices":
            r = {"indices": indices(texte)}
        elif u.path == "/sons":
            k = prononcer([texte])[0] if texte else ""
            cand, type_ = proches(k, 12)
            r = {"texte": texte, "son": k, "type": type_, "mots": cand}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(r, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
