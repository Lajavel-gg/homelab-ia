"""
title: Manga
description: Traduit en français des pages ou des chapitres de manga (japonais ou anglais) envoyés dans le chat — images, .cbz, .zip ou .pdf — en effaçant le texte d'origine et en le réécrivant dans les bulles. Garde un carnet des noms par série. Tout reste sur le Spark.
version: 1.0
"""

# Le travail est fait par le Studio (file d'attente, studio/manga.py) et manga-image-translator (conteneur « manga »).
# Cet outil récupère les fichiers du message, suit l'avancement et affiche le résultat.

import asyncio
import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi.responses import HTMLResponse

RUNNER = "http://127.0.0.1:8190/studio/api"
FILE_RE = re.compile(r"/api/v1/files/([0-9a-f-]{36})")
RE_MEDIA = re.compile(r"/studio/media/[0-9a-f]{32}\.[a-z0-9]{2,4}")
EXT_OK = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".zip", ".cbz", ".pdf")
LANGUES = ("auto", "ja", "en", "ko", "zh")


def _api(chemin, donnees=None, timeout=60):
    req = urllib.request.Request(RUNNER + chemin, data=json.dumps(donnees).encode() if donnees is not None else None,
                                 headers={"content-type": "application/json"}, method="POST" if donnees is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return {"erreur": json.loads(e.read()).get("erreur", str(e))}
        except ValueError:
            return {"erreur": str(e)}


def _nom_fichier(f):
    fichier = f.get("file") or {}
    return (f.get("name") or fichier.get("filename") or (fichier.get("meta") or {}).get("name") or "page").strip()


async def _octets_du_fichier(fid):
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    f = await Files.get_file_by_id(fid)
    if f and f.path:
        with open(Storage.get_file(f.path), "rb") as fh:
            return f.filename or "page", fh.read()
    return None


async def _fichiers_du_message(files, messages, chat_id):
    """Fichiers joints au dernier message (pièces jointes, images collées), sinon à la dernière demande de l'historique."""
    out, vus = [], set()

    async def ajouter_id(fid, nom=None):
        if fid in vus:
            return
        vus.add(fid)
        r = await _octets_du_fichier(fid)
        if r and (nom or r[0]).lower().endswith(EXT_OK):
            out.append((nom or r[0], r[1]))

    for f in files or []:
        fid = f.get("id") or (f.get("file") or {}).get("id")
        if fid:
            await ajouter_id(fid, _nom_fichier(f))
    for m in reversed(messages or []):  # images collées dans le dernier message de l'utilisateur
        if m.get("role") != "user":
            continue
        parts = m.get("content") if isinstance(m.get("content"), list) else []
        for k, p in enumerate(parts):
            url = (p.get("image_url") or {}).get("url", "") if p.get("type") == "image_url" else ""
            if url.startswith("data:image"):
                ext = "." + url.split(";")[0].split("/")[-1].replace("jpeg", "jpg")
                out.append((f"page{k + 1:03d}{ext}", base64.b64decode(url.split(",", 1)[1])))
            for fid in FILE_RE.findall(url):
                await ajouter_id(fid)
        break
    if not out and chat_id and not str(chat_id).startswith("local:"):  # fichiers rangés dans l'historique du chat
        from open_webui.models.chats import Chats

        chat = await Chats.get_chat_by_id(chat_id)
        hist = ((chat.chat if chat else {}) or {}).get("history", {}).get("messages", {})
        for msg in sorted(hist.values(), key=lambda x: x.get("timestamp", 0), reverse=True):
            if msg.get("role") != "user" or not msg.get("files"):
                continue
            for f in msg["files"]:
                fid = f.get("id") or (f.get("file") or {}).get("id")
                if not fid:
                    mo = FILE_RE.search(f.get("url", "") or "")
                    fid = mo.group(1) if mo else None
                if fid:
                    await ajouter_id(fid, _nom_fichier(f))
            break
    return out


def _vue(job, jid, nb=3):
    """Aperçu dans le chat : miniatures des premières pages, puis « Lire tout » (liseuse) et le .cbz."""
    pages = [p for p in job.get("pages") or [] if RE_MEDIA.fullmatch(str(p))]
    cbz = job.get("fichier") if RE_MEDIA.fullmatch(str(job.get("fichier") or "")) else ""
    # rangée de miniatures (compacte dans le chat) ; une seule page refaite est montrée en plus grand
    largeur = "100%" if nb == 1 else f"calc({100 // nb}% - 6px)"
    images = "".join(f'<a href="/studio/manga/{jid}" target="_blank"><img src="{p}" loading="lazy" alt="page {i}" '
                     f'style="width:{largeur};max-height:{620 if nb == 1 else 300}px;object-fit:contain;'
                     'background:#fff;border-radius:6px;margin:0 6px 6px 0;vertical-align:top"></a>'
                     for i, p in enumerate(pages[:nb], 1))
    reste = f" ({len(pages)} pages)" if len(pages) > 1 else ""
    resize = ("<script>function h(){parent.postMessage({type:'iframe:height',height:document.documentElement.scrollHeight},'*')}"
              "addEventListener('load',h);document.querySelectorAll('img').forEach(i=>i.addEventListener('load',h));"
              "new ResizeObserver(h).observe(document.body);setTimeout(h,300);setTimeout(h,1500)</script>")
    return ('<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;font-family:system-ui;color:#999">'
            f'<div style="max-width:720px;font-size:0">{images}</div><div style="padding:6px 2px;font-size:13px">'
            f'<a href="/studio/manga/{jid}" target="_blank" style="color:#4a90d9">Lire tout le chapitre{reste}</a>'
            + (f' · <a href="{cbz}?dl=1" download style="color:#999">Télécharger le .cbz</a>' if cbz else "")
            + f'</div>{resize}</body></html>')


async def _suivre(jid, status, libelle, limite_h=6):
    t0, job = time.time(), {}
    while time.time() - t0 < limite_h * 3600:
        await asyncio.sleep(5)
        job = await asyncio.to_thread(_api, f"/jobs/{jid}")
        if job.get("statut") in ("terminé", "échec"):
            return job
        if job.get("position"):
            await status(f"{libelle} : en attente ({job['position']} tâche(s) avant)")
        else:
            await status(f"{libelle} : {job.get('etape', '…')} — {int((time.time() - t0) // 60)} min")
    return job


class Tools:
    def __init__(self):
        pass

    async def traduire_manga(self, serie: str, langue: str = "auto", __files__=None, __messages__=None,
                             __metadata__=None, __event_emitter__=None) -> str:
        """
        Traduit en français les pages de manga envoyées avec le message (images, .cbz, .zip ou .pdf ; un chapitre entier
        est possible). Le texte d'origine est effacé et la traduction écrite dans les bulles. Prend environ 15 à 60 s par
        page. À utiliser dès que l'utilisateur envoie des pages ou un chapitre de manga à traduire.
        :param serie: Nom de la série (ex. « One Piece »), pour utiliser et compléter son carnet de noms. Demande-le si tu ne le sais pas.
        :param langue: Langue d'origine : "ja" (japonais), "en" (anglais), "ko", "zh" ou "auto" si tu ne sais pas.
        """
        async def status(msg, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": msg, "done": done}})

        await status("📖 Je récupère les pages…")
        fichiers = await _fichiers_du_message(__files__, __messages__, (__metadata__ or {}).get("chat_id"))
        if not fichiers:
            await status("📖 Aucune page trouvée", True)
            return ("Aucune page reçue. Demande à l'utilisateur de joindre les pages au message : des images, un .cbz, "
                    "un .zip ou un .pdf (un chapitre à la fois).")
        r = await asyncio.to_thread(_api, "/jobs", {
            "type": "manga", "serie": (serie or "Sans titre").strip(), "langue": langue if langue in LANGUES else "auto",
            "fichiers_b64": [{"nom": n, "b64": base64.b64encode(o).decode()} for n, o in fichiers]}, 300)
        if "id" not in r:
            await status("📖 Refusé", True)
            return f"Le Studio a refusé : {r.get('erreur', r)}. Explique-le simplement à l'utilisateur."
        job = await _suivre(r["id"], status, "📖 Traduction")
        if job.get("statut") != "terminé":
            await status("📖 Échec", True)
            return f"La traduction a échoué : {job.get('etape', 'délai dépassé')}. Explique-le simplement à l'utilisateur."
        n, manque = len(job.get("pages") or []), job.get("pages_non_traduites") or []
        await status(f"📖 {n} page(s) traduite(s) ✅", True)
        ctx = (f"Chapitre traduit et affiché à l'utilisateur (aperçu + lien « Lire tout » + .cbz). Identifiant du chapitre : "
               f"{r['id']}, {n} page(s), série « {job.get('serie')} »."
               + (f" Pages laissées en version originale (erreur) : {manque} — propose de les refaire." if manque else "")
               + " Réponds en une ou deux phrases. Rappelle qu'il peut demander de refaire une page avec une remarque "
               "(« page 7, c'est une blague ») et faire noter un nom dans le carnet de la série. Ne mets pas de lien.")
        return HTMLResponse(_vue(job, r["id"]), headers={"Content-Disposition": "inline"}), ctx

    async def refaire_page(self, chapitre: str, page: int, remarque: str, __event_emitter__=None) -> str:
        """
        Retraduit une seule page d'un chapitre déjà traduit, en tenant compte d'une remarque de l'utilisateur
        (sens d'une bulle, ton, nom à changer…). Le chapitre et son .cbz sont mis à jour.
        :param chapitre: Identifiant du chapitre (donné quand il a été traduit, 10 caractères).
        :param page: Numéro de la page (1 = première page).
        :param remarque: Ce que l'utilisateur veut corriger, en français, le plus précisément possible.
        """
        async def status(msg, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": msg, "done": done}})

        r = await asyncio.to_thread(_api, "/jobs", {"type": "manga_page", "parent": str(chapitre).strip(),
                                                    "page": int(page), "remarque": remarque})
        if "id" not in r:
            return f"Refusé : {r.get('erreur', r)}."
        job = await _suivre(r["id"], status, f"📖 Page {page}", limite_h=1)
        if job.get("statut") != "terminé":
            await status("📖 Échec", True)
            return f"La page n'a pas pu être refaite : {job.get('etape')}. Explique-le simplement à l'utilisateur."
        await status(f"📖 Page {page} refaite ✅", True)
        chap = await asyncio.to_thread(_api, f"/jobs/{chapitre}")
        vue = {"pages": [job.get("page_url")], "fichier": chap.get("fichier")}
        return (HTMLResponse(_vue(vue, chapitre, nb=1), headers={"Content-Disposition": "inline"}),
                f"Page {page} retraduite avec la remarque, affichée à l'utilisateur ; le chapitre et le .cbz sont à jour. "
                "Réponds en une phrase. Si la remarque concernait un nom, propose de le noter dans le carnet.")

    async def noter_dans_le_carnet(self, serie: str, original: str, traduction: str, note: str = "") -> str:
        """
        Note dans le carnet de la série comment traduire un nom, une attaque, un lieu ou une expression, pour que ce soit
        toujours pareil dans les prochains chapitres.
        :param serie: Nom de la série.
        :param original: Le mot tel qu'il apparaît (en japonais ou en anglais).
        :param traduction: La traduction à utiliser à chaque fois.
        :param note: Précision facultative (ex. « personnage féminin », « ne pas traduire »).
        """
        r = await asyncio.to_thread(_api, "/manga/carnet", {"serie": serie, "original": original,
                                                            "traduction": traduction, "note": note})
        if "erreur" in r:
            return f"Impossible de noter : {r['erreur']}"
        return f"Noté dans le carnet de « {r.get('serie')} » : {original} → {traduction}. ({len(r['entrees'])} entrée(s))"

    async def carnet_de_la_serie(self, serie: str) -> str:
        """
        Montre le carnet de traduction d'une série (noms, attaques, lieux…).
        :param serie: Nom de la série.
        """
        c = await asyncio.to_thread(_api, "/manga/carnet?serie=" + urllib.parse.quote(serie))
        if not c.get("entrees"):
            return f"Le carnet de « {serie} » est vide pour l'instant."
        return f"Carnet de « {c.get('serie')} » :\n" + "\n".join(
            f"- {e['original']} → {e['traduction']}" + (f" ({e['note']})" if e.get("note") else "") for e in c["entrees"])

    async def oublier_du_carnet(self, serie: str, original: str) -> str:
        """
        Retire une entrée du carnet d'une série.
        :param serie: Nom de la série.
        :param original: Le mot d'origine à retirer.
        """
        r = await asyncio.to_thread(_api, "/manga/carnet", {"serie": serie, "original": original, "oublier": True})
        return "Retiré du carnet." if r.get("oublie") else "Ce mot n'était pas dans le carnet."

    async def series_connues(self) -> str:
        """
        Liste les séries qui ont déjà un carnet de traduction.
        """
        s = await asyncio.to_thread(_api, "/manga/series")
        return "\n".join(f"- {x['serie']} ({x['entrees']} entrée(s))" for x in s) if s else "Aucune série pour l'instant."
