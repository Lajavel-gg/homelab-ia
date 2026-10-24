"""Traduction de manga pour le Studio (agent « 📖 Manga » d'Open WebUI).

Le travail technique est fait par manga-image-translator (conteneur « manga », 127.0.0.1:5003) : il trouve les bulles,
lit le texte (japonais vertical ou anglais), l'efface, et écrit la traduction. La traduction elle-même est faite par
Flash-Next (modèle « manga » de llama-swap, sans phase de réflexion), avec le carnet de la série dans ses consignes.

Ici : lecture des fichiers reçus (images, .zip/.cbz, .pdf), carnet par série, envoi page par page, .cbz final,
page refaite avec une remarque du lecteur. Utilisé par runner.py (types de tâche « manga » et « manga_page »).
"""

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.request
import uuid
import zipfile

MANGA_URL = os.environ.get("MANGA_URL", "http://127.0.0.1:5003")
# Monté au même chemin dans le conteneur « manga » : il lit les consignes de la série par ce chemin
SERIES = os.environ.get("MANGA_SERIES", "/opt/ai-stack/manga/series")
TRAVAIL = os.environ.get("MANGA_TRAVAIL", "/opt/ai-stack/manga/travail")  # pages d'origine, pour refaire une page
EXT_IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
MAX_PAGES = 400
OCR = {"ja": "48px", "en": "48px", "ko": "48px", "zh": "48px", "auto": "48px"}
LANGUES = {"ja": "japonais", "en": "anglais", "ko": "coréen", "zh": "chinois", "auto": "langue détectée automatiquement"}


# ------------------------------------------------------------------ fichiers reçus

def _ordre_naturel(nom):
    """« page2 » avant « page10 »."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", nom)]


def pages_depuis_fichiers(fichiers, dossier):
    """fichiers = [(nom, octets)] → chemins des pages (images) écrites dans `dossier`, dans l'ordre de lecture.
    Les archives sont lues en mémoire (aucun chemin de l'archive n'est utilisé pour écrire : pas de « zip slip »)."""
    os.makedirs(dossier, exist_ok=True)
    pages = []

    def ajouter(octets, ext):
        if len(pages) >= MAX_PAGES:
            raise ValueError(f"plus de {MAX_PAGES} pages : envoie le manga chapitre par chapitre")
        chemin = os.path.join(dossier, f"{len(pages) + 1:04d}{ext}")
        with open(chemin, "wb") as f:
            f.write(octets)
        pages.append(chemin)

    for nom, octets in fichiers:
        ext = os.path.splitext(nom.lower())[1]
        if ext in EXT_IMAGES:
            ajouter(octets, ext)
        elif ext in (".zip", ".cbz") or octets[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(octets)) as z:
                membres = [m for m in z.infolist() if not m.is_dir() and "__MACOSX" not in m.filename
                           and not os.path.basename(m.filename).startswith(".")
                           and os.path.splitext(m.filename.lower())[1] in EXT_IMAGES]
                for m in sorted(membres, key=lambda m: _ordre_naturel(m.filename)):
                    if m.file_size > 80 * 2**20:
                        raise ValueError(f"image trop grosse dans l'archive : {m.filename}")
                    ajouter(z.read(m), os.path.splitext(m.filename.lower())[1])
        elif ext == ".pdf" or octets[:5] == b"%PDF-":
            if not shutil.which("pdftoppm"):
                raise ValueError("pour les PDF, installe poppler-utils sur le Spark (sudo apt install poppler-utils)")
            with tempfile.TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "f.pdf"), "wb") as f:
                    f.write(octets)
                subprocess.run(["pdftoppm", "-r", "150", "-png", os.path.join(tmp, "f.pdf"), os.path.join(tmp, "p")],
                               check=True, capture_output=True, timeout=1800)
                for n in sorted((x for x in os.listdir(tmp) if x.endswith(".png")), key=_ordre_naturel):
                    with open(os.path.join(tmp, n), "rb") as f:
                        ajouter(f.read(), ".png")
        # autres fichiers (texte, etc.) : ignorés
    if not pages:
        raise ValueError("aucune page trouvée : envoie des images, un .cbz, un .zip ou un .pdf")
    return pages


# ------------------------------------------------------------------ carnet par série

def slug(serie):
    s = unicodedata.normalize("NFKD", serie or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "sans-titre"


def carnet(serie):
    try:
        with open(os.path.join(SERIES, slug(serie) + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"serie": serie, "entrees": []}


def noter(serie, original, traduction, note=""):
    original, traduction = str(original).strip()[:80], str(traduction).strip()[:80]
    if not original or not traduction:
        raise ValueError("il faut le mot d'origine et sa traduction")
    c = carnet(serie)
    c["serie"] = c.get("serie") or serie
    c["entrees"] = [e for e in c["entrees"] if e["original"] != original] + [
        {"original": original, "traduction": traduction, "note": str(note).strip()[:120]}]
    _ecrire(os.path.join(SERIES, slug(serie) + ".json"), c)
    return c


def oublier(serie, original):
    c = carnet(serie)
    avant = len(c["entrees"])
    c["entrees"] = [e for e in c["entrees"] if e["original"] != str(original).strip()]
    _ecrire(os.path.join(SERIES, slug(serie) + ".json"), c)
    return avant != len(c["entrees"])


def lister_series():
    out = []
    for n in sorted(os.listdir(SERIES)) if os.path.isdir(SERIES) else []:
        if n.endswith(".json"):
            c = carnet(n[:-5])
            out.append({"serie": c.get("serie") or n[:-5], "entrees": len(c["entrees"])})
    return out


def _ecrire(chemin, obj):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(chemin + ".tmp", chemin)
    os.chmod(chemin, 0o644)


CONSIGNES = (
    "Ignore all preceding instructions. Follow only what is defined below.\n"
    "## Rôle\nTu es un traducteur professionnel de manga vers le {{to_lang}} (français de France, naturel, "
    "comme une édition française publiée). Tu traduis tout le contenu, sans censure ni adoucissement.\n"
    "## Règles\n"
    "- Garde le ton, le niveau de langue et la personnalité de chaque personnage ; tutoiement ou vouvoiement selon "
    "la relation.\n"
    "- Garde les suffixes japonais (-san, -kun, -chan, -sama, -senpai, -sensei) et les noms tels quels.\n"
    "- Phrases courtes : le texte doit tenir dans la bulle.\n"
    "- Laisse les onomatopées et le charabia tels quels, sans les traduire.\n"
    "- Ne mets jamais le caractère « œ » : écris « oe » (cœur → coeur).\n"
    "- Translate line by line. Output each segment with its prefix (<|number|> format exactly) and only provide "
    "the translation without raw text. No commentary.\n"
    "{carnet}{remarque}"
    "Translate the following text into {{to_lang}}:\n"
)


def consignes(serie, remarque=""):
    """Fichier de consignes (JSON, lu comme YAML par manga-image-translator) avec le carnet de la série."""
    echap = lambda t: str(t).replace("{", "{{").replace("}", "}}")  # le modèle de texte passe par str.format
    c = carnet(serie)
    bloc = ""
    if c["entrees"]:
        bloc = (f"## Carnet de la série « {echap(c.get('serie') or serie)} » (à respecter à chaque fois)\n"
                + "".join(f"- {echap(e['original'])} → {echap(e['traduction'])}"
                          + (f" ({echap(e['note'])})" if e.get("note") else "") + "\n" for e in c["entrees"]))
    rem = f"## Remarque du lecteur pour cette page (prioritaire)\n{echap(remarque)}\n" if remarque else ""
    texte = CONSIGNES.format(carnet=bloc, remarque=rem)
    chemin = os.path.join(SERIES, f"{slug(serie)}.{uuid.uuid4().hex[:8]}.consignes.json")
    _ecrire(chemin, {"temperature": 0.3, "chat_system_template": texte})
    return chemin


# ------------------------------------------------------------------ traduction

def configuration(langue, chemin_consignes):
    return {
        "translator": {"translator": "custom_openai", "target_lang": "FRA", "gpt_config": chemin_consignes},
        "ocr": {"ocr": OCR.get(langue, "48px"), "use_mocr_merge": langue == "ja"},
        "detector": {"detector": "default", "detection_size": 2048},
        "inpainter": {"inpainter": "lama_large"},
        # texte latin horizontal ajusté à la bulle (même pour une bulle japonaise verticale). Ce rendu utilise la police
        # « comic shanns 2 », qui a tous les accents français sauf « œ » (remplacé par fr_post_dict.txt). NE PAS passer
        # aux polices « anime_ace » fournies : elles affichent « Désolé » comme « Dúsolú » (vérifié le 24/10/2026).
        "render": {"renderer": "manga2eng", "direction": "horizontal"},
    }


def _multipart(champs, fichier):
    limite = uuid.uuid4().hex
    corps = b""
    for k, v in champs.items():
        corps += f"--{limite}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n".encode() + v.encode() + b"\r\n"
    nom, octets = fichier
    corps += (f"--{limite}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{nom}\"\r\n"
              "Content-Type: application/octet-stream\r\n\r\n").encode() + octets + f"\r\n--{limite}--\r\n".encode()
    return corps, f"multipart/form-data; boundary={limite}"


def traduire_page(chemin_page, conf, timeout=900):
    """Renvoie les octets PNG de la page traduite (exception si manga-image-translator refuse)."""
    with open(chemin_page, "rb") as f:
        corps, ctype = _multipart({"config": json.dumps(conf)}, (os.path.basename(chemin_page), f.read()))
    req = urllib.request.Request(MANGA_URL + "/translate/with-form/image", data=corps, method="POST",
                                 headers={"content-type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            png = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"manga-image-translator : {e.read().decode(errors='replace')[:200]}")
    except OSError as e:
        raise RuntimeError(f"manga-image-translator injoignable ({e}) : le conteneur « manga » tourne-t-il ?")
    if not png.startswith(b"\x89PNG"):
        raise RuntimeError("réponse inattendue de manga-image-translator")
    return png


def cbz(pages_png, chemin):
    """Archive .cbz (zip sans compression, pages 001.png…) lisible par toutes les liseuses de BD."""
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_STORED) as z:
        for i, p in enumerate(pages_png, 1):
            z.write(p, f"{i:03d}{os.path.splitext(p)[1]}")
    return chemin
