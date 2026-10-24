"""
title: Studio vidéo & 3D
description: Crée des images (Z-Image / Krea 2, vérifiées automatiquement), des vidéos (MiniMax H3, avec son) et des objets 3D (Pixal3D / TRELLIS.2) via ComfyUI. Le chat est mis en pause pendant les vidéos et la 3D (pas pour les images).
version: 1.0
"""

import asyncio
import base64
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request

from fastapi.responses import HTMLResponse

RUNNER = "http://127.0.0.1:8190/studio/api/jobs"
COMFY = "http://127.0.0.1:8188"
LLAMA = "http://127.0.0.1:8090"
FILE_RE = re.compile(r"/api/v1/files/([0-9a-f-]{36})")


def _post(data):
    req = urllib.request.Request(RUNNER, data=json.dumps(data).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _get(jid):
    with urllib.request.urlopen(f"{RUNNER}/{jid}", timeout=30) as r:
        return json.load(r)


def _demande_utilisateur(messages, n=3):
    """Les derniers messages de l'utilisateur (la demande + son contexte)."""
    out = []
    for m in reversed(messages or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
        if c and c.strip():
            out.append(c.strip()[:1500])
        if len(out) >= n:
            break
    out.reverse()
    if not out:
        return ""
    return "\n".join(f"(avant) {t}" for t in out[:-1]) + ("\n" if len(out) > 1 else "") + f"DEMANDE ACTUELLE : {out[-1]}"


def _image_job(prompt, modele, fmt):
    r = _post({"type": "image", "prompt": prompt, "modele": modele, "format": fmt})
    if "id" not in r:
        raise RuntimeError(r)
    t0 = time.time()
    while time.time() - t0 < 600:
        time.sleep(2)
        job = _get(r["id"])
        if job.get("statut") == "terminé":
            return job
        if job.get("statut") == "échec":
            raise RuntimeError(job.get("etape"))
    raise RuntimeError("trop long")


def _comfy_file(rel):
    sub, name = os.path.split(rel)
    q = urllib.parse.urlencode({"filename": name, "subfolder": sub, "type": "output"})
    with urllib.request.urlopen(f"{COMFY}/view?{q}", timeout=60) as r:
        return r.read()


OBS = """Describe this image objectively. Look carefully at each limb, from the shoulder to the hand and from the hip to the foot.
Answer ONLY with JSON:
{"style": "photo" or "anime/illustration",
 "apparent_age": number (age the main person LOOKS),
 "youthful_face": true if the face looks like a teenager or younger than 20,
 "left_arm": what the PERSON's own left arm looks like (whole / ends at shoulder / ends above elbow / ends below elbow / prosthesis / hidden / not in frame),
 "right_arm": same for the person's right arm,
 "left_leg": same for the left leg (whole / ends above knee / ends below knee / prosthesis / hidden / not in frame),
 "right_leg": same for the right leg,
 "eyes": "normal / closed / white or clouded / covered by blindfold / hidden",
 "neck": "everything on or around the neck (collar, cervical brace, tube, choker, nothing)",
 "medical_or_restraint_items": ["every wheelchair, crutch, brace, tube, strap, straitjacket, harness, cuff, chain… with its exact look"],
 "clothing": "short description",
 "framing": "full body / three-quarter / waist-up / close-up"}
The person's left side is on the RIGHT of the image when they face the camera."""

CMP = """You check a generated image against what the user asked.
USER REQUEST (French, phonetic spelling, may contain typos):
{demande}

PROMPT SENT TO THE GENERATOR:
{prompt}

OBJECTIVE DESCRIPTION OF THE IMAGE (made without knowing the request — trust it, and look at the image yourself too):
{obs}

List every element the user REALLY asked for (body, missing or prosthetic limbs with side, eyes, medical items, restraints, clothing, pose, framing, style).
For each element, copy the words of the DESCRIPTION that prove it is there ("evidence"). If the description does not mention it, or describes
something different (for example a harness instead of a straitjacket, a collar but no tracheostomy tube), then visible = false.
Small unrequested details do not matter. A missing limb counts as present if the limb visibly ends early, even if the side or exact level differs slightly.
If the scene is sexy or suggestive and the person looks younger than 20 (youthful_face true), that is a serious problem.
Answer ONLY with JSON:
{{"elements": [{{"element": "short", "evidence": "words from the description or empty", "visible": true/false}}],
 "ok": true if all important elements are visible and nothing important contradicts the request,
 "problemes": ["important problem, in French"],
 "corrections": ["short English phrase describing what must be visible"]}}"""


def _appel(png, texte, max_tokens=900):
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB"); im.thumbnail((1024, 1024))
    b = io.BytesIO(); im.save(b, "JPEG", quality=90)
    body = {"model": "verif", "temperature": 0.1, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()}},
                {"type": "text", "text": texte}]}]}
    req = urllib.request.Request(f"{LLAMA}/v1/chat/completions", data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        txt = json.load(r)["choices"][0]["message"]["content"]
    return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))


# Mots qui rendent une scène suggestive, en français (y compris écrit au son) et en anglais (le prompt est en anglais).
SUGGESTIF = re.compile(
    r"sex|sensu|erot|érot|seduc|séduc|lingerie|bikini|swimsuit|maillot|sous[- ]?v[eê]t|underwear|panties|bra\b|"
    r"nude|naked|\bnue?s?\b|topless|cleavage|d[ée]collet|seins?\b|breast|soumi|esclave|slave|bondage|fetish|f[ée]tich|"
    r"provocant|provocative|lascive|aguicheu|pin[- ]?up")


def _suggestif(*textes):
    return bool(SUGGESTIF.search(" ".join(t or "" for t in textes).lower()))


def _verifier(png, demande, prompt):
    """Vérificateur v2 : description à l'aveugle puis comparaison élément par élément (banc d'essai : 11/12 contre 7/12)."""
    try:
        obs = _appel(png, OBS, 700)
        v = _appel(png, CMP.format(demande=demande or "(inconnue)", prompt=prompt[:3000], obs=json.dumps(obs, ensure_ascii=False)))
        v["observation"] = obs
        try:
            age = float(obs.get("apparent_age") or 99)
        except (TypeError, ValueError):
            age = 99
        if (obs.get("youthful_face") or age <= 24) and _suggestif(demande, prompt):
            v["ok"] = False
            v["trop_jeune"] = True  # cette image ne sera jamais affichée (voir creer_image)
            v.setdefault("problemes", []).append("Le visage paraît trop jeune pour une scène sexy.")
            v.setdefault("corrections", []).append("clearly adult woman in her thirties with a mature adult face")
        v["corrections"] = [str(x) for x in (v.get("corrections") or [])][:8]
        v["problemes"] = [str(x) for x in (v.get("problemes") or [])][:8]
        return v
    except Exception as e:  # noqa: BLE001
        return {"ok": None, "erreur": str(e)[:200]}


async def _publier_image(png, request, user, chat_id, message_id, emitter):
    """Range l'image comme une image générée d'Open WebUI (visible dans le chat, réutilisable pour animer / 3D)."""
    from open_webui.models.chats import Chats
    from open_webui.models.users import UserModel
    from open_webui.routers.images import upload_image

    u = UserModel(**user) if user else None
    meta = {"chat_id": chat_id, "message_id": message_id} if chat_id and message_id else {}
    _, img = await upload_image(request, png, "image/png", meta, u)
    files = [{"type": "image", **img}]
    if chat_id and message_id and not str(chat_id).startswith("local:"):
        try:
            db_files = await Chats.add_message_files_by_id_and_message_id(chat_id, message_id, files)
            if db_files is not None:
                files = db_files
        except Exception:  # noqa: BLE001
            pass
    if emitter:
        await emitter({"type": "chat:message:files", "data": {"files": files}})
    return img


async def _last_image(messages, chat_id):
    """Dernière image de la conversation (envoyée par l'utilisateur ou générée), en base64."""
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    async def from_file_id(fid):
        f = await Files.get_file_by_id(fid)
        if f and f.path:
            with open(Storage.get_file(f.path), "rb") as fh:
                return base64.b64encode(fh.read()).decode()
        return None

    # 1) messages envoyés au modèle (images jointes en data: URL ou liens de fichiers)
    for m in reversed(messages or []):
        content = m.get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        for p in reversed(parts):
            url = (p.get("image_url") or {}).get("url", "") if p.get("type") == "image_url" else ""
            if url.startswith("data:image"):
                return url.split(",", 1)[1]
            for fid in reversed(FILE_RE.findall(url or p.get("text", "") or "")):
                b = await from_file_id(fid)
                if b:
                    return b
    # 2) historique de la conversation (images générées, rangées dans message.files)
    if chat_id:
        from open_webui.models.chats import Chats

        chat = await Chats.get_chat_by_id(chat_id)
        hist = ((chat.chat if chat else {}) or {}).get("history", {}).get("messages", {})
        for msg in sorted(hist.values(), key=lambda x: x.get("timestamp", 0), reverse=True):
            for f in reversed(msg.get("files") or []):
                if f.get("type") == "image" or str(f.get("content_type", "")).startswith("image"):
                    m = FILE_RE.search(f.get("url", "") or "")
                    fid = m.group(1) if m else f.get("id")
                    if fid:
                        b = await from_file_id(fid)
                        if b:
                            return b
    return None


def _base_url(request):
    if request is None:
        return ""
    h = request.headers
    host = h.get("x-forwarded-host") or h.get("host") or ""
    proto = h.get("x-forwarded-proto") or "https"
    return f"{proto}://{host}" if host else ""


def _embed(job, base=""):
    # Liens relatifs : dans l'iframe srcdoc, ils pointent vers l'adresse utilisée par le navigateur
    url = job["fichier"]
    if job.get("format") == "image":
        body = f'<img src="{url}" style="display:block;width:100%;max-height:720px;object-fit:contain;border-radius:10px">'
    elif job.get("format") == "glb":
        body = (
            '<script type="module" src="/studio/media/model-viewer-4.0.0.min.js"></script>'
            f'<model-viewer src="{url}" camera-controls auto-rotate shadow-intensity="1" exposure="1.1" '
            'style="display:block;width:100%;height:420px;background:#1b1b1b;border-radius:10px"></model-viewer>'
        )
    else:
        body = (
            f'<video src="{url}" controls loop playsinline preload="metadata" '
            'style="display:block;width:100%;aspect-ratio:16/9;max-height:520px;border-radius:10px;background:#000"></video>'
        )
    # L'iframe d'Open WebUI est isolée : on lui envoie notre hauteur, sinon elle reste écrasée
    resize = (
        "<script>function h(){parent.postMessage({type:'iframe:height',height:document.documentElement.scrollHeight},'*')}"
        "addEventListener('load',h);addEventListener('resize',h);"
        "document.querySelectorAll('img').forEach(i=>i.addEventListener('load',h));"
        "document.querySelectorAll('video').forEach(v=>v.addEventListener('loadedmetadata',h));"
        "new ResizeObserver(h).observe(document.body);setTimeout(h,300);setTimeout(h,1500)</script>"
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;font-family:system-ui;color:#ccc">'
        f'{body}<div style="padding:6px 2px;font-size:13px"><a href="{url}?dl=1" download style="color:#8cf">⬇️ Télécharger</a>'
        ' · <a href="/studio/" target="_blank" style="color:#8cf">🎬 Studio</a></div>'
        f'{resize}</body></html>'
    )



async def _rafraichir_modeles(request, user):
    """Le chat a été arrêté pendant la tâche : si Open WebUI a relu la liste des modèles à ce moment-là,
    Flash-Next en a disparu et la réponse après l'outil échoue (« Model not found »). On la relit (19/10/2026)."""
    if request is None:
        return
    try:
        from open_webui.utils.models import get_all_models
        u = None
        try:
            from open_webui.models.users import Users
            u = await Users.get_user_by_id(user["id"]) if user else None
        except Exception:  # noqa: BLE001
            u = None
        for _ in range(6):
            await get_all_models(request, refresh=True, user=u)
            if any(str(k).startswith("qwen3.8-flash-next") for k in dict(request.app.state.MODELS.items()).keys()):
                return
            await asyncio.sleep(5)
    except Exception:  # noqa: BLE001
        pass


# ---- Fenêtre « durée + qualité » avant chaque vidéo (18/10/2026) ----
# Minutes ≈ 1 + B[q] × secondes^1,4 (mesuré : 10 s → rapide 12 min, bonne 10, meilleure 19 ;
# 5 s rapide ≈ 5 min), corrigé ensuite par les vrais temps des dernières vidéos.
QUALITES_VIDEO = {
    "rapide": ("Rapide", "480p", 0.44),
    "bonne": ("Bonne", "768p Turbo 4", 0.36),
    "meilleure": ("Meilleure", "768p Turbo 8", 0.72),
}


def _minutes_prevues(q, secondes=5, scenes=0):
    b = QUALITES_VIDEO[q][2]
    if scenes:
        return scenes * (b * 9 ** 1.4 + 1) + 1
    return 1 + b * max(2, min(int(secondes), 10)) ** 1.4


def _corrections():
    """Rapport temps réel / temps prévu (médiane des 5 dernières vidéos) pour chaque qualité."""
    out = {q: 1.0 for q in QUALITES_VIDEO}
    try:
        with urllib.request.urlopen(RUNNER, timeout=10) as r:
            jobs = json.load(r)
    except Exception:  # noqa: BLE001
        return out
    ratios = {q: [] for q in QUALITES_VIDEO}
    for j in sorted(jobs.values(), key=lambda j: j.get("cree", 0)):
        if j.get("statut") != "terminé" or not j.get("debut") or not j.get("fin"):
            continue
        q = j.get("qualite") or "rapide"
        if q not in ratios:
            continue
        if j.get("type") in ("video", "video_image"):
            prevu = _minutes_prevues(q, j.get("secondes") or 5)
        elif j.get("type") == "video_longue" and j.get("scenes"):
            prevu = _minutes_prevues(q, scenes=len(j["scenes"]))
        else:
            continue
        ratios[q].append((j["fin"] - j["debut"]) / 60 / prevu)
    for q, r in ratios.items():
        r = sorted(r[-5:])
        if r:
            out[q] = max(0.5, min(2.0, r[len(r) // 2]))
    return out


FENETRE_JS = r"""
const P = __PARAMS__;
return await new Promise((resolve) => {
  let fini = false;
  const ov = document.createElement('div');
  const fin = (v) => { if (fini) return; fini = true; ov.remove(); resolve(v); };
  ov.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;padding:16px;font-family:system-ui,sans-serif';
  const box = document.createElement('div');
  box.style.cssText = 'background:#1e1e22;color:#eee;border-radius:16px;padding:20px;width:100%;max-width:420px;box-shadow:0 10px 40px rgba(0,0,0,.5)';
  const h = (t, css, txt) => { const e = document.createElement(t); if (css) e.style.cssText = css; if (txt != null) e.textContent = txt; return e; };
  box.append(h('div', 'font-size:18px;font-weight:600;margin-bottom:14px', P.titre));
  let sec = P.secondes, q = P.qualite;
  if (!P.scenes) {
    const lab = h('div', 'margin:6px 0', '');
    const r = document.createElement('input');
    r.type = 'range'; r.min = 2; r.max = 10; r.step = 1; r.value = sec;
    r.style.cssText = 'width:100%;accent-color:#7c6cf0';
    r.oninput = () => { sec = +r.value; maj(); };
    box.append(lab, r); P._lab = lab;
  } else {
    box.append(h('div', 'margin:6px 0;opacity:.85', 'Durée : ~' + P.duree_longue + ' s (' + P.scenes + ' scènes)'));
  }
  box.append(h('div', 'margin:14px 0 6px', 'Qualité :'));
  const btns = {};
  for (const [k, nom, det] of P.choix) {
    const b = h('button', '', '');
    b.onclick = () => { q = k; maj(); };
    b.append(h('span', 'font-weight:600', nom), h('span', 'opacity:.7;margin-left:8px;font-size:13px', det));
    btns[k] = b; box.append(b);
  }
  const est = h('div', 'margin:16px 0 4px;font-size:17px;font-weight:600', '');
  box.append(est, h('div', 'font-size:13px;opacity:.8', '⚠️ Le chat sera en pause pendant ce temps. Tu peux fermer la page, la vidéo arrivera quand même.'));
  const bar = h('div', 'display:flex;gap:10px;justify-content:flex-end;margin-top:18px');
  const an = h('button', 'padding:9px 16px;border-radius:10px;border:1px solid #555;background:transparent;color:#eee;cursor:pointer', 'Annuler');
  const go = h('button', 'padding:9px 18px;border-radius:10px;border:0;background:#7c6cf0;color:#fff;font-weight:600;cursor:pointer', 'Lancer');
  an.onclick = () => fin({ ok: false });
  go.onclick = () => fin({ ok: true, secondes: sec, qualite: q });
  bar.append(an, go); box.append(bar);
  const fmt = (m) => { m = Math.max(1, Math.round(m)); return m < 60 ? '~' + m + ' min' : '~' + Math.floor(m / 60) + ' h ' + String(m % 60).padStart(2, '0'); };
  const maj = () => {
    if (P._lab) P._lab.textContent = 'Durée : ' + sec + ' s';
    for (const k in btns) btns[k].style.cssText = 'display:block;width:100%;text-align:left;margin:6px 0;padding:10px 12px;border-radius:10px;cursor:pointer;color:#eee;' +
      (k === q ? 'border:2px solid #7c6cf0;background:#2d2a45' : 'border:1px solid #444;background:#26262b');
    const [, , , b] = P.choix.find((c) => c[0] === q);
    const m = P.scenes ? P.scenes * (b * Math.pow(9, 1.4) + 1) + 1 : 1 + b * Math.pow(sec, 1.4);
    est.textContent = '⏱️ Temps estimé : ' + fmt(m * P.corr[q]);
  };
  ov.append(box); document.body.append(ov); maj();
  setTimeout(() => fin(null), 15 * 60 * 1000);
});
"""


async def _choisir_video(__event_call__, titre, secondes=5, qualite="", scenes=0):
    """Ouvre la fenêtre de choix. Renvoie (lancer, secondes, qualite) ; None si la fenêtre n'a pas pu s'ouvrir."""
    q = qualite if qualite in QUALITES_VIDEO else "rapide"
    secondes = max(2, min(int(secondes or 5), 10))
    if not __event_call__:
        return None
    corr = await asyncio.to_thread(_corrections)
    params = {
        "titre": titre, "secondes": secondes, "qualite": q, "scenes": scenes, "duree_longue": round(scenes * 9.4),
        "corr": corr, "choix": [[k, v[0], v[1], v[2]] for k, v in QUALITES_VIDEO.items()],
    }
    code = "try {" + FENETRE_JS.replace("__PARAMS__", json.dumps(params, ensure_ascii=False)) + "} catch (e) { return null; }"
    try:
        r = await __event_call__({"type": "execute", "data": {"code": code}})
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(r, dict) or "ok" not in r:
        return None
    if not r.get("ok"):
        return (False, secondes, q)
    q2 = r.get("qualite") if r.get("qualite") in QUALITES_VIDEO else q
    try:
        s2 = max(2, min(int(r.get("secondes") or secondes), 10))
    except (TypeError, ValueError):
        s2 = secondes
    return (True, s2, q2)


ANNULE = ("Rien n'a été lancé : l'utilisateur a annulé dans la fenêtre de choix (durée/qualité). "
          "Dis-le-lui en une phrase et propose de relancer quand il veut.")


class Tools:
    def __init__(self):
        pass

    async def _run(self, payload, label, __event_emitter__, __request__, max_h=3, __user__=None):
        async def status(msg, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": msg, "done": done}})

        try:
            r = await asyncio.to_thread(_post, payload)
        except Exception as e:  # noqa: BLE001
            return f"Le Studio ne répond pas ({e}). Dis à l'utilisateur de vérifier le service studio-runner."
        if "id" not in r:
            return f"Refusé par le Studio : {r}"
        jid, t0 = r["id"], time.time()
        pause = "" if payload.get("type") == "image" else " (le chat est en pause pendant la création)"
        await status(f"{label} : démarrage{pause}…")
        job = {}
        while time.time() - t0 < max_h * 3600:
            await asyncio.sleep(5)
            try:
                job = await asyncio.to_thread(_get, jid)
            except Exception:  # noqa: BLE001
                continue
            if job.get("statut") in ("terminé", "échec"):
                # attendre que le chat soit relancé pour pouvoir répondre
                while job.get("relance_chat") in (None, "en cours") and time.time() - t0 < max_h * 3600 + 400:
                    await status(f"{label} : terminé, je relance le chat…")
                    await asyncio.sleep(4)
                    job = await asyncio.to_thread(_get, jid)
                    if job.get("position", 0) and job.get("relance_chat") is None:
                        break  # d'autres tâches en file : le chat reste en pause
                break
            mins = int((time.time() - t0) // 60)
            await status(f"{label} : {job.get('etape', '…')} — {mins} min écoulées")

        if payload.get("type") != "image":
            await _rafraichir_modeles(__request__, __user__)
        if job.get("statut") != "terminé":
            await status(f"{label} : échec", True)
            return f"La création a échoué : {job.get('etape', 'délai dépassé')}. Explique-le simplement à l'utilisateur."
        mins = round((time.time() - t0) / 60, 1)
        await status(f"{label} prêt en {mins} min ✅", True)
        ctx = (
            f"{label} créé(e) en {mins} min et affiché(e) à l'utilisateur juste au-dessus (lecteur intégré + lien de "
            "téléchargement). Ne mets pas de lien ni d'image markdown. Réponds en une ou deux phrases en français, "
            "et propose une variante éventuelle."
        )
        return HTMLResponse(_embed(job), headers={"Content-Disposition": "inline"}), ctx

    async def creer_image(
        self,
        prompt: str,
        style: str = "realiste",
        format: str = "portrait",
        personnages: str = "",
        __messages__=None,
        __user__=None,
        __chat_id__=None,
        __message_id__=None,
        __event_emitter__=None,
        __request__=None,
    ) -> str:
        """
        Crée une image (le chat reste disponible, ~20 s à 1 min 30). C'est le SEUL outil pour créer une image.
        L'image est vérifiée automatiquement par rapport à la demande et refaite une fois si elle ne correspond pas.
        Le générateur n'a AUCUNE mémoire : tout ce qui doit apparaître doit être écrit dans prompt et personnages, à chaque fois.
        :param prompt: Description EN ANGLAIS de 60 à 130 mots, en phrases courtes et CONCRÈTES (ce qu'on voit, avec couleur et position), chaque élément une seule fois, les éléments demandés par l'utilisateur EN PREMIER. Inclut l'apparence du personnage (reprise de la fiche), la tenue, la pose, le cadrage, le décor, le style. Pas de mots abstraits (dignified, gentle, caring, respectful…). Rien d'inventé, rien d'adouci. Texte à écrire dans l'image entre guillemets.
        :param style: "realiste" (photo, réaliste, par défaut), "anime" (anime, manga, dessin animé, cel-shading), "illustration" (art dessiné, peinture, fantasy, jeu vidéo) ou "affiche" (affiche de film, poster).
        :param format: "portrait" (personnage, affiche — par défaut), "paysage" (décor, scène large, plusieurs personnages côte à côte) ou "carre".
        :param personnages: EN ANGLAIS, la fiche d'apparence COMPLÈTE (sert à la vérification et à la mémoire, mets aussi ses éléments visibles dans prompt) de chaque personnage qui apparaît (nom, âge, corps, peau, cheveux, yeux, visage, signes distinctifs, membres manquants ou prothèses avec le côté exact). Recopie la même fiche mot pour mot à chaque nouvelle image du même personnage, en ne changeant que ce que l'utilisateur demande de changer. Vide s'il n'y a pas de personnage.
        """
        if len((prompt or "").split()) < 12:
            return ("REFUSÉ, aucune image créée : le prompt est vide ou trop court. Rappelle creer_image UNE fois avec le "
                    "prompt complet en anglais (60 à 130 mots), la fiche personnages remplie et le bon style.")
        demande = _demande_utilisateur(__messages__)
        style = (style or "").lower().strip()
        if style not in ("realiste", "anime", "illustration", "affiche", "manga"):
            style = ""
        if not style or style == "realiste":
            # filet de sécurité : le modèle oublie parfois le style → on regarde le prompt et ce que l'utilisateur a écrit
            indices = (prompt + " " + personnages).lower()
            if re.search(r"\b(anime|manga|cel[- ]shad|my hero academia art style)", indices) and not re.search(r"photoreal|realistic photo", indices):
                style = "anime"
        modele = "krea" if style in ("anime", "illustration", "affiche", "manga") else "zimage"
        # Handicap / matériel médical en réaliste : Z-Image remplace les moignons par des prothèses et rate le matériel,
        # Qwen turbo les réussit (test du 18/10 : jambe amputée 0/2 vs 2/2, fauteuil tétra moyen vs 2/2).
        if modele == "zimage" and re.search(
                r"amput|stump|residual limb|prosthe|wheelchair|tetrapleg|quadripleg|paraplegi|tracheo|cervical collar|neck brace"
                r"|brace|orthosis|splint|sling|crutch|blind|white cane|cast\b|bandage|running blade", (prompt + " " + demande).lower()):
            modele = "qwen"
        # La fiche n'est plus collée devant le prompt (un prompt trop long fait oublier des détails au générateur) :
        # elle sert seulement à la vérification.
        texte = prompt.strip()
        if style in ("anime", "manga") and "cinematic" not in texte.lower():
            # style « ciné » choisi par l'utilisateur (comparaison du 18/10) ; mis APRÈS le contenu pour ne pas faire oublier les détails
            texte = (texte.rstrip(".") + ". Detailed cinematic anime illustration, semi-realistic anime style, painterly shading, "
                     "dramatic lighting with warm rim light and soft light rays, rich detailed background, vibrant moody colors.")
        reference = (f"Fiche personnage : {personnages.strip()}\n\nPrompt : {texte}" if personnages.strip() else texte)

        async def status(msg, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": msg, "done": done}})

        noms = {"krea": "Krea 2", "zimage": "Z-Image", "qwen": "Qwen turbo"}
        essais = []
        for essai in (1, 2):
            if essai == 2 and style not in ("anime", "manga"):
                modele = "qwen"  # 2e essai : Qwen turbo, le plus fidèle aux détails (en anime on reste sur Krea : Qwen fait plat)
            nom_modele = noms[modele]
            await status(f"🎨 Image ({nom_modele}) : je dessine{' la version corrigée' if essai == 2 else ''}…")
            try:
                job = await asyncio.to_thread(_image_job, texte, modele, format)
            except Exception as e:  # noqa: BLE001
                await status("🎨 Image : échec", True)
                return f"La création d'image a échoué ({e}). Explique-le simplement à l'utilisateur."
            png = await asyncio.to_thread(_comfy_file, job["comfy"])
            await status("🔎 Je vérifie que l'image correspond à la demande…")
            verdict = await asyncio.to_thread(_verifier, png, demande, reference)
            essais.append((png, verdict, texte, nom_modele))
            if verdict.get("ok") or essai == 2 or not verdict.get("corrections"):
                break
            corr = "; ".join(verdict["corrections"])
            texte = f"{corr}. {texte}"  # les éléments manquants en premier, en phrases simples

        # Garde-fou : une personne qui paraît mineure dans une scène suggestive n'est JAMAIS affichée,
        # même si c'est le « meilleur » essai. Idem si la vérification n'a pas pu se faire sur une demande suggestive.
        essais = [e for e in essais if not e[1].get("trop_jeune")]
        if _suggestif(demande, texte, personnages):
            essais = [e for e in essais if e[1].get("erreur") is None]
        if not essais:
            await status("🎨 Image refusée", True)
            return ("REFUSÉ, aucune image affichée : la personne paraissait mineure dans une scène suggestive, ou la "
                    "vérification de l'âge n'a pas pu se faire. Dis-le simplement à l'utilisateur. Ne relance pas creer_image "
                    "pour cette demande.")
        # garde le meilleur essai (le moins de problèmes)
        png, verdict, texte_final, nom_modele = min(essais, key=lambda e: (not e[1].get("ok"), len(e[1].get("problemes") or [])))
        fichier = await _publier_image(png, __request__, __user__, __chat_id__, __message_id__, __event_emitter__)
        await status(f"🎨 Image prête ({nom_modele}) ✅", True)
        if verdict.get("ok"):
            bilan = "La vérification automatique confirme que l'image correspond à la demande."
        elif verdict.get("erreur"):
            bilan = "La vérification automatique n'a pas pu se faire."
        else:
            bilan = ("Malgré une seconde tentative, il reste ces écarts avec la demande : "
                     + "; ".join(verdict.get("problemes") or []) + ". Dis-le HONNÊTEMENT à l'utilisateur et propose de réessayer.")
        return (
            "N'appelle PAS creer_image à nouveau pour cette demande (l'outil a déjà réessayé tout seul) : réponds à l'utilisateur. "
            f"Image gardée : celle de {nom_modele} ({len(essais)} essai(s)) et déjà affichée à l'utilisateur dans le chat "
            f"(ne remets ni lien ni image). {bilan} Ne prétends pas que des détails sont présents si tu ne les as pas "
            "vérifiés. Réponds brièvement en français. Prompt utilisé : " + texte_final[:1500]
        )

    async def creer_video(
        self,
        description: str,
        secondes: int = 5,
        qualite: str = "",
        __event_emitter__=None,
        __event_call__=None,
        __request__=None,
        __user__=None,
    ) -> str:
        """
        Crée une courte vidéo AVEC SON (10 s maximum) à partir d'une description (texte → vidéo). Prend 5 à 20 minutes selon la durée et la qualité (une fenêtre affiche le temps estimé à l'utilisateur, qui choisit avant le lancement).
        C'est l'outil PAR DÉFAUT pour toute demande de vidéo, de clip ou d'animation sans image de départ,
        y compris quand aucune durée n'est précisée.
        :param description: Description détaillée EN ANGLAIS de la scène : sujet, action, mouvement de caméra, ambiance, lumière, sons/musique/dialogues. Une longue description donne un meilleur résultat.
        :param secondes: Durée en secondes (2 à 10, 5 par défaut).
        :param qualite: Laisse VIDE sauf si l'utilisateur précise la qualité : "rapide" (480p), "bonne" (768p) ou "meilleure" (768p, la plus belle et la plus lente). Une fenêtre lui permet de toute façon de choisir durée et qualité avant le lancement.
        """
        choix = await _choisir_video(__event_call__, "🎬 Ta vidéo", secondes, qualite)
        if choix and not choix[0]:
            return ANNULE
        if choix:
            secondes, qualite = choix[1], choix[2]
        return await self._run({"type": "video", "prompt": description, "secondes": secondes, "qualite": qualite or "rapide"},
                               "🎬 Vidéo", __event_emitter__, __request__, __user__=__user__)

    async def animer_image(
        self,
        description: str,
        secondes: int = 5,
        qualite: str = "",
        __messages__=None,
        __metadata__=None,
        __event_emitter__=None,
        __event_call__=None,
        __request__=None,
        __user__=None,
    ) -> str:
        """
        Anime la dernière image de la conversation (image envoyée par l'utilisateur ou générée) en vidéo avec son.
        À utiliser quand l'utilisateur dit « anime cette image », « fais une vidéo de cette image », etc.
        :param description: Description EN ANGLAIS du mouvement et de l'action à partir de l'image, de la caméra et des sons.
        :param secondes: Durée en secondes (2 à 10, 5 par défaut).
        :param qualite: Laisse VIDE sauf si l'utilisateur précise la qualité : "rapide" (480p), "bonne" (768p) ou "meilleure" (768p, la plus belle et la plus lente). Une fenêtre lui permet de toute façon de choisir durée et qualité avant le lancement.
        """
        img = await _last_image(__messages__, (__metadata__ or {}).get("chat_id"))
        if not img:
            return "Aucune image trouvée dans la conversation. Demande à l'utilisateur d'en envoyer une ou d'en générer une d'abord."
        choix = await _choisir_video(__event_call__, "🎬 Animer l'image", secondes, qualite)
        if choix and not choix[0]:
            return ANNULE
        if choix:
            secondes, qualite = choix[1], choix[2]
        return await self._run({"type": "video_image", "prompt": description, "secondes": secondes, "image_b64": img,
                                "qualite": qualite or "rapide"}, "🎬 Animation", __event_emitter__, __request__, __user__=__user__)

    async def creer_3d(
        self,
        description: str = "",
        depuis_image: bool = True,
        __messages__=None,
        __metadata__=None,
        __event_emitter__=None,
        __request__=None,
        __user__=None,
    ) -> str:
        """
        Crée un objet 3D texturé (fichier GLB, visible et téléchargeable) — pour Blender, impression 3D, jeux…
        Si depuis_image est vrai, utilise la dernière image de la conversation ; sinon, dessine d'abord l'objet à partir de la description.
        :param description: Description EN ANGLAIS de l'objet (obligatoire si depuis_image est faux).
        :param depuis_image: true pour transformer la dernière image de la conversation, false pour partir uniquement de la description.
        """
        if depuis_image:
            img = await _last_image(__messages__, (__metadata__ or {}).get("chat_id"))
            if img:
                return await self._run({"type": "3d", "prompt": description, "image_b64": img}, "🧊 Objet 3D", __event_emitter__, __request__, __user__=__user__)
            if not description:
                return "Aucune image dans la conversation et pas de description : demande à l'utilisateur ce qu'il veut en 3D."
        return await self._run({"type": "3d_texte", "prompt": description}, "🧊 Objet 3D", __event_emitter__, __request__, __user__=__user__)

    async def creer_video_longue(
        self,
        style: str,
        scenes: list[str],
        qualite: str = "",
        __event_emitter__=None,
        __event_call__=None,
        __request__=None,
        __user__=None,
    ) -> str:
        """
        Crée une vidéo LONGUE (20 s à 2 min) avec son, en enchaînant des scènes d'environ 9 secondes qui se suivent sans coupure.
        N'utilise cet outil QUE si l'utilisateur demande EXPLICITEMENT une durée de plus de 10 secondes (ex. « 30 secondes », « 1 minute », « 2 minutes »).
        Sinon utilise creer_video. Maximum 2 minutes : au-delà, propose 2 minutes. Une fenêtre montre à l'utilisateur le temps estimé (10 à 20 min par scène selon la qualité, chat en pause) et lui fait choisir la qualité avant le lancement.
        :param style: Description EN ANGLAIS commune à toutes les scènes : personnages (apparence précise), lieu, style visuel, lumière, ambiance sonore. Répétée à chaque scène pour garder la cohérence.
        :param scenes: Liste ordonnée des scènes EN ANGLAIS, une par tranche de 9 secondes (nombre de scènes = durée demandée ÷ 9, arrondi ; 14 maximum). Chaque scène décrit l'action, la caméra et les sons, et commence là où la précédente s'arrête.
        :param qualite: Laisse VIDE sauf si l'utilisateur précise la qualité : "rapide" (480p), "bonne" (768p) ou "meilleure" (768p, la plus belle et la plus lente). Une fenêtre lui permet de toute façon de choisir durée et qualité avant le lancement.
        """
        scenes = [x for x in (scenes or []) if str(x).strip()][:14]
        if len(scenes) < 2:
            return "Il faut au moins 2 scènes pour une vidéo longue ; pour une vidéo courte, utilise creer_video."
        duree = round(len(scenes) * 9.4)
        choix = await _choisir_video(__event_call__, f"🎞️ Vidéo longue : ~{duree} s", 5, qualite, scenes=len(scenes))
        if not choix or not choix[0]:
            return (
                f"Rien n'a été lancé : l'utilisateur n'a pas confirmé dans la fenêtre (durée ~{duree} s, {len(scenes)} scènes, "
                "10 à 20 min de calcul par scène selon la qualité, chat en pause pendant ce temps). Dis-le-lui simplement et "
                "propose une vidéo courte ou de relancer la vidéo longue quand il n'aura pas besoin du chat."
            )
        qualite = choix[2]
        return await self._run(
            {"type": "video_longue", "style": style, "scenes": scenes, "qualite": qualite},
            f"🎞️ Vidéo longue ({len(scenes)} scènes, ~{duree} s)",
            __event_emitter__,
            __request__,
            max_h=1 + len(scenes) * 0.6,
            __user__=__user__,
        )
