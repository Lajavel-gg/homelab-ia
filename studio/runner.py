#!/usr/bin/env python3
"""Studio : file d'attente vidéo / 3D pour ComfyUI.

Les modèles vidéo et 3D ne tiennent pas en mémoire à côté de Flash-Next (~80 Go).
Pour chaque tâche : on attend que le chat soit inactif, on arrête llama-flashnext,
on lance le workflow ComfyUI, on libère ComfyUI puis on relance llama-flashnext.

API (127.0.0.1:8190) :
  POST /studio/api/jobs        {"type": "video"|"video_image"|"3d"|"3d_texte"|"image", "prompt": "...",
                                "image_b64": "...", "secondes": 5}
  GET  /studio/api/jobs/<id>   état d'une tâche
  GET  /studio                 page listant les créations
"""

import base64
import copy
import json
import os
import queue
import random
import re
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/opt/ai-stack/studio"
MEDIA = os.path.join(BASE, "media")
JOBS_FILE = os.path.join(BASE, "jobs.json")
WORKFLOWS = os.path.join(BASE, "workflows")
COMFY = "http://127.0.0.1:8188"
COMFY_OUT = "/opt/ai-stack/comfyui/output"
LLAMA = "http://127.0.0.1:8090"
LLAMA_UNIT = "llama-flashnext"
# « systemd » (par défaut) : le chat est arrêté/relancé avec systemctl. « swap » : llama-swap sert le chat sur LLAMA ;
# on le vide par son API et on pose un fichier de pause que garde-memoire lit (voir spark/scripts/activer-llama-swap).
LLAMA_MODE = os.environ.get("STUDIO_LLAMA", "systemd")
LLAMA_MODELE = "qwen3.8-flash-next"
PAUSE = os.environ.get("STUDIO_PAUSE", "/run/studio-pause")
# Lecteur 3D servi en local (avant : CDN, donc pas de 3D sans internet). Copié dans MEDIA au démarrage,
# car Caddy sert /studio/media sans mot de passe (l'iframe du chat ne peut pas en donner).
VIEWER = "model-viewer-4.0.0.min.js"
# Ménage automatique : les créations plus vieilles que ça quittent le Spark (sauf celles marquées « garder »).
# Désactivé par défaut (0 = jamais) : on l'active soi-même, de préférence avec STUDIO_ARCHIVE.
GARDER_JOURS = int(os.environ.get("STUDIO_GARDER_JOURS", "0"))
# Archivage sur le serveur Proxmox (pve2) au lieu de la suppression : destination rsync, ex. « studio@192.168.0.201: ».
# La clé SSH n'a le droit que d'AJOUTER des fichiers (rrsync -wo côté pve2, voir le README). Vide = suppression.
ARCHIVE = os.environ.get("STUDIO_ARCHIVE", "").strip()
ARCHIVE_CLE = os.environ.get("STUDIO_ARCHIVE_CLE", "/opt/ai-stack/studio/archive_ed25519")
# Sans STUDIO_ARCHIVE, le ménage n'efface JAMAIS rien définitivement, sauf choix explicite STUDIO_MENAGE_SUPPRIMER=1.
MENAGE_SUPPRIMER = os.environ.get("STUDIO_MENAGE_SUPPRIMER", "").strip() == "1"
RE_JID = re.compile(r"[0-9a-f]{10}")
RE_MEDIA = re.compile(r"/studio/media/([0-9a-f]{32}\.[a-z0-9]{2,4})")

lock = threading.Lock()
jobs: dict = {}
todo: "queue.Queue[str]" = queue.Queue()


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def save():
    with lock:
        tmp = JOBS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=1)
        os.replace(tmp, JOBS_FILE)


def update(jid, **kw):
    with lock:
        jobs[jid].update(kw)
    save()


def http(method, url, data=None, headers=None, timeout=60):
    body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def mem_go():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) // 1048576
    return 0


# ---------------------------------------------------------------- llama / mémoire

def llama_charge():
    """llama-swap : Flash-Next est-il chargé ? (interroger /upstream le chargerait, on demande d'abord /running)"""
    try:
        return any(m.get("model") == LLAMA_MODELE and m.get("state") == "ready"
                   for m in http("GET", LLAMA + "/running", timeout=5).get("running", []))
    except Exception:  # noqa: BLE001
        return False


def llama_busy():
    if LLAMA_MODE == "swap":
        if not llama_charge():
            return False
        url = f"{LLAMA}/upstream/{LLAMA_MODELE}/slots"
    else:
        url = LLAMA + "/slots"
    try:
        return any(s.get("is_processing") for s in http("GET", url, timeout=5))
    except Exception:  # noqa: BLE001 — arrêté ou en veille : pas occupé
        return False


def chat_en_pause():
    if LLAMA_MODE == "swap":
        return os.path.exists(PAUSE)
    return subprocess.run(["systemctl", "is-active", "--quiet", LLAMA_UNIT]).returncode != 0


def stop_llama(jid):
    # Laisse le chat finir sa réponse en cours (jusqu'à 3 min)
    t0 = time.time()
    while llama_busy() and time.time() - t0 < 180:
        update(jid, etape="j'attends que le chat finisse sa réponse")
        time.sleep(3)
    update(jid, etape="je mets le chat en pause pour libérer la mémoire")
    if LLAMA_MODE == "swap":
        # 1) plus aucun modèle ne peut redémarrer (garde-memoire lit ce fichier), 2) on vide tout
        with open(PAUSE, "w") as f:
            f.write(f"Studio {jid}\n")
        try:
            http("POST", LLAMA + "/api/models/unload", b"", {}, timeout=120)
        except Exception as e:  # noqa: BLE001
            log(f"llama-swap n'a pas pu tout décharger : {e}")
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                if not http("GET", LLAMA + "/running", timeout=5).get("running"):
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(2)
    else:
        subprocess.run(["systemctl", "stop", LLAMA_UNIT], check=False)


def start_llama():
    if LLAMA_MODE == "swap":
        try:
            os.remove(PAUSE)
        except FileNotFoundError:
            pass
        sante = f"{LLAMA}/upstream/{LLAMA_MODELE}/health"  # cette requête recharge Flash-Next tout de suite
    else:
        subprocess.run(["systemctl", "start", "--no-block", LLAMA_UNIT], check=False)
        sante = LLAMA + "/health"
    t0 = time.time()
    while time.time() - t0 < 600:
        try:
            if http("GET", sante, timeout=60).get("status") == "ok":
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return False


def comfy_free():
    try:
        http("POST", COMFY + "/free", {"unload_models": True, "free_memory": True})
    except Exception:  # noqa: BLE001
        pass


def wait_mem(need, jid):
    t0 = time.time()
    while mem_go() < need:
        if time.time() - t0 > 180:
            raise RuntimeError(f"pas assez de mémoire ({mem_go()} Go libres, besoin {need})")
        update(jid, etape=f"j'attends la mémoire ({mem_go()}/{need} Go)")
        time.sleep(3)


# ---------------------------------------------------------------- ComfyUI

def upload_image(b64):
    data = base64.b64decode(b64)
    name = f"studio_{uuid.uuid4().hex[:12]}.png"
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
        f"Content-Type: image/png\r\n\r\n".encode() + data
        + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    )
    r = http("POST", COMFY + "/upload/image", body, {"content-type": f"multipart/form-data; boundary={boundary}"})
    return r["name"]


def load_workflow(name, **params):
    """Charge workflows/<name>.json et remplace les valeurs "{{cle}}" par params[cle]."""
    with open(os.path.join(WORKFLOWS, name + ".json")) as f:
        wf = json.load(f)

    def sub(v):
        if isinstance(v, str) and v.startswith("{{") and v.endswith("}}"):
            return params[v[2:-2]]
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [sub(x) for x in v]
        return v

    return sub(copy.deepcopy(wf))


def run_workflow(wf, jid, label):
    try:
        r = http("POST", COMFY + "/prompt", {"prompt": wf, "client_id": "studio"})
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI refuse le workflow : {e.read().decode()[:400]}")
    if r.get("node_errors"):
        raise RuntimeError(f"ComfyUI : workflow invalide {json.dumps(r['node_errors'], ensure_ascii=False)[:400]}")
    pid = r["prompt_id"]
    t0 = time.time()
    while True:
        time.sleep(3)
        h = http("GET", f"{COMFY}/history/{pid}")
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                err = msgs[0][1].get("exception_message", "erreur") if msgs else "erreur"
                raise RuntimeError(f"ComfyUI : {err.strip()[:300]}")
            files = []
            for out in h[pid].get("outputs", {}).values():
                for key in ("images", "gifs", "videos", "3d", "result", "model_file", "meshes"):
                    for it in out.get(key, []) or []:
                        if isinstance(it, dict) and it.get("filename") and it.get("type", "output") == "output":
                            files.append(os.path.join(COMFY_OUT, it.get("subfolder", ""), it["filename"]))
                        elif isinstance(it, str):
                            files.append(os.path.join(COMFY_OUT, it))
            return files
        mins = int((time.time() - t0) // 60)
        update(jid, etape=f"{label} en cours ({mins} min)")
        if time.time() - t0 > 3 * 3600:
            http("POST", COMFY + "/interrupt", {})
            raise RuntimeError("trop long (> 3 h), arrêté")


def publish(src, ext):
    name = f"{uuid.uuid4().hex}{ext}"
    dst = os.path.join(MEDIA, name)
    try:
        os.link(src, dst)  # lien dur : même fichier, pas de 2e copie sur le disque (vidéos de plusieurs centaines de Mo)
    except OSError:  # autre disque ou système de fichiers : copie classique
        shutil.copy2(src, dst)
    os.chmod(dst, 0o644)
    return "/studio/media/" + name


def pick(files, exts):
    for f in files:
        if os.path.splitext(f)[1].lower() in exts and os.path.exists(f):
            return f
    raise RuntimeError(f"aucun fichier {exts} produit (sortie : {files})")


def process(jid):
    j = jobs[jid]
    kind, prompt = j["type"], j.get("prompt", "")
    seed = random.randint(1, 2**48)
    image_name = upload_image(j.pop("image_b64")) if j.get("image_b64") else None
    save()

    if kind == "image":
        # Image : tient à côté de Flash-Next, le chat n'est pas mis en pause.
        # On vide d'abord ComfyUI : Z-Image (images normales) + Krea chargés ensemble, c'est trop près de la limite.
        # Z-Image (réaliste, par défaut) ou Krea 2 (anime, illustration, affiche)
        # Qwen turbo : le plus fidèle aux détails, utilisé pour le 2e essai quand le 1er a raté des éléments
        modele = {"krea": "image_krea", "qwen": "image"}.get(j.get("modele"), "image_zimage")
        comfy_free()
        wait_mem(22, jid)
        w, h = {"portrait": (896, 1344), "paysage": (1344, 896)}.get(j.get("format"), (1152, 1152))
        update(jid, etape="je dessine l'image")
        wf = load_workflow(modele, prompt=prompt, seed=seed, width=w, height=h, prefix="studio/image")
        img = pick(run_workflow(wf, jid, "image"), {".png", ".jpg", ".webp"})
        return {"fichier": publish(img, ".png"), "format": "image", "comfy": os.path.relpath(img, COMFY_OUT)}

    if kind == "3d_texte":
        # 1) image du sujet (fond uni, objet entier) avec Qwen-Image, 2) 3D
        update(jid, etape="je dessine l'objet")
        wf = load_workflow("image", prompt=f"{prompt}. Single object, full view, centered, plain white background, studio lighting, no shadow on ground.", seed=seed, width=1024, height=1024, prefix="studio/ref")
        img = pick(run_workflow(wf, jid, "dessin"), {".png", ".jpg", ".webp"})
        j["apercu"] = publish(img, ".png")
        with open(img, "rb") as f:
            image_name = upload_image(base64.b64encode(f.read()).decode())
        kind = "3d"

    if kind == "3d":
        comfy_free()
        wait_mem(25, jid)
        wf = load_workflow("3d", image=image_name, seed=seed, prefix="studio/3d")
        out = pick(run_workflow(wf, jid, "modèle 3D"), {".glb"})
        return {"fichier": publish(out, ".glb"), "format": "glb"}

    if kind == "video_longue":
        return long_video(jid, j, seed)

    # vidéo
    secondes = max(2, min(int(j.get("secondes") or 5), 10))
    comfy_free()
    wait_mem(55, jid)
    name = "video_image" if kind == "video_image" else "video"
    wf = load_workflow(name, prompt=prompt, image=image_name, seed=seed, frames=h3_frames(secondes), prefix="studio/video")
    wf = appliquer_qualite(wf, j.get("qualite"))
    out = pick(run_workflow(wf, jid, "vidéo"), {".mp4", ".webm", ".mov"})
    return {"fichier": publish(out, os.path.splitext(out)[1].lower()), "format": "video"}


# Qualité vidéo choisie dans la fenêtre du chat (18/10/2026, tests : 10 s → rapide 12 min, bonne 10, meilleure 19)
QUALITES = {
    "rapide": None,  # 864x480, 20 étapes (réglage d'origine)
    "bonne": ("minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors", 4),
    "meilleure": ("minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors", 8),
}


def appliquer_qualite(wf, qualite):
    """Rapide = workflow tel quel ; bonne/meilleure = LoRA Turbo lightx2v en 768p (shift 6/3, euler)."""
    q = QUALITES.get(qualite)
    if not q:
        return wf
    lora, steps = q
    wf["30"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": lora, "strength_model": 1.0}}
    wf["31"] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["30", 0], "shift_video": 6.0, "shift_audio": 3.0}}
    wf["6"]["inputs"].update(model=["31", 0], steps=steps)
    wf["9"]["inputs"]["model"] = ["31", 0]
    wf["7"]["inputs"]["sampler_name"] = "euler"
    if isinstance(wf["5"]["inputs"].get("width"), int):
        wf["5"]["inputs"].update(width=1344, height=768)
    if "21" in wf and wf["21"]["class_type"] == "ImageScaleToTotalPixels":
        wf["21"]["inputs"]["megapixels"] = 0.98
    return wf


def h3_frames(secondes):
    """Nombre d'images accepté par MiniMax H3 (formule du workflow officiel)."""
    n = max(5, round(secondes * 24))
    return n + (5 - n % 17) % 17


MAX_SCENES = 14          # 14 × ~9,4 s ≈ 2 min 10
SCENE_SECONDES = 9
COMFY_CT = "ai-stack-comfyui-1"
VIDEOTOOLS = os.path.join(BASE, "tools", "videotools.py")


def videotool(*args):
    """Exécute tools/videotools.py dans le conteneur ComfyUI (PyAV y est installé)."""
    host2ct = lambda p: p.replace(COMFY_OUT, "/opt/ComfyUI/output")
    with open(VIDEOTOOLS) as f:
        r = subprocess.run(["docker", "exec", "-i", COMFY_CT, "python3", "-", *map(host2ct, args)],
                           stdin=f, capture_output=True, text=True, timeout=1800)
    if r.returncode:
        raise RuntimeError(f"assemblage vidéo : {r.stderr.strip()[-300:]}")
    return r.stdout.strip()


def long_video(jid, j, seed):
    scenes, style = j["scenes"], j.get("style", "")
    comfy_free()
    wait_mem(55, jid)
    clips, image = [], None
    for i, scene in enumerate(scenes, 1):
        prompt = f"{style}\n\n{scene}".strip()
        label = f"scène {i}/{len(scenes)}"
        if image is None:
            wf = load_workflow("video", prompt=prompt, seed=seed, frames=h3_frames(SCENE_SECONDES), prefix=f"studio/long_{jid}/clip")
        else:
            wf = load_workflow("video_suite", prompt=prompt, image=image, seed=seed + i, frames=h3_frames(SCENE_SECONDES), prefix=f"studio/long_{jid}/clip")
        wf = appliquer_qualite(wf, j.get("qualite"))
        clip = pick(run_workflow(wf, jid, label), {".mp4", ".webm", ".mov"})
        clips.append(clip)
        update(jid, scenes_faites=i)
        if i < len(scenes):
            png = os.path.join(os.path.dirname(clip), f"dernier_{i}.png")
            videotool("lastframe", clip, png)
            with open(png, "rb") as f:
                image = upload_image(base64.b64encode(f.read()).decode())
    update(jid, etape="j'assemble les scènes")
    final = os.path.join(os.path.dirname(clips[0]), "final.mp4")
    videotool("concat", final, *clips)
    return {"fichier": publish(final, ".mp4"), "format": "video"}


def worker():
    while True:
        jid = todo.get()
        llama_stopped = False
        try:
            if jobs[jid]["type"] != "image":
                stop_llama(jid)
                llama_stopped = True
            update(jid, statut="en cours", debut=time.time())
            res = process(jid)
            update(jid, statut="terminé", etape="terminé", fin=time.time(), **res)
            log(f"{jid} terminé : {res}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            update(jid, statut="échec", etape=str(e)[:400], fin=time.time())
        finally:
            comfy_free()
            chat_arrete = chat_en_pause()
            if not llama_stopped and not (chat_arrete and todo.empty()):
                update(jid, relance_chat="inutile")
            elif todo.empty():  # (une image passée derrière une vidéo relance aussi le chat)
                update(jid, relance_chat="en cours")
                ok = start_llama()
                update(jid, relance_chat="ok" if ok else "échec")


# ---------------------------------------------------------------- ménage

def fichiers_du_job(j):
    """Fichiers de MEDIA qui appartiennent à une création (le rendu et l'aperçu), noms vérifiés."""
    out = []
    for cle in ("fichier", "apercu"):
        m = RE_MEDIA.fullmatch(str(j.get(cle) or ""))
        if m:
            out.append(os.path.join(MEDIA, m.group(1)))
    return out


def supprimer_job(jid):
    """« ok », « inconnu » ou « en cours » (on ne supprime pas une création qui tourne)."""
    with lock:
        j = jobs.get(jid)
        if j is None:
            return "inconnu"
        if j.get("statut") in ("en attente", "en cours"):
            return "en cours"
        del jobs[jid]
    for f in fichiers_du_job(j):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
    save()
    return "ok"


def _nom_lisible(j, jid, chemin):
    """« 2026-10-24_video_ab12cd34ef.mp4 » : retrouvable à la main dans les archives."""
    return f"{datetime.fromtimestamp(j.get('cree', 0)):%Y-%m-%d}_{re.sub(r'[^a-z0-9]', '', str(j.get('type', 'x')))}_{jid}" \
        + os.path.splitext(chemin)[1]


def archiver_job(jid):
    """Envoie la création sur pve2 (fichiers + fiche .json avec le prompt), puis la retire du Spark.
    Rien n'est supprimé si l'envoi échoue. Renvoie « ok », « inconnu », « en cours », « pas configuré » ou l'erreur."""
    if not ARCHIVE:
        return "pas configuré"
    with lock:
        j = dict(jobs.get(jid) or {})
    if not j:
        return "inconnu"
    if j.get("statut") in ("en attente", "en cours"):
        return "en cours"
    sous = f"{datetime.fromtimestamp(j.get('cree', 0)):%Y/%m}"
    envoi = os.path.join(os.path.dirname(MEDIA), "archive-envoi", jid)
    shutil.rmtree(envoi, ignore_errors=True)
    os.makedirs(envoi)
    try:
        noms = []
        for f in fichiers_du_job(j):
            if os.path.exists(f):
                nom = _nom_lisible(j, jid, f) if not noms else _nom_lisible(j, jid + "_apercu", f)
                try:
                    os.link(f, os.path.join(envoi, nom))  # lien dur : pas de copie sur le disque
                except OSError:
                    shutil.copy2(f, os.path.join(envoi, nom))
                noms.append(nom)
        fiche = {k: j.get(k) for k in ("type", "prompt", "style", "scenes", "qualite", "secondes", "format", "modele", "statut", "etape")}
        fiche.update(id=jid, cree=datetime.fromtimestamp(j.get("cree", 0)).isoformat(timespec="seconds"), fichiers=noms)
        with open(os.path.join(envoi, _nom_lisible(j, jid, "fiche.json")), "w") as f:
            json.dump(fiche, f, ensure_ascii=False, indent=1)
        r = subprocess.run(["rsync", "-t", "--mkpath", "--partial", "-e",
                            f"ssh -i {ARCHIVE_CLE} -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=yes",
                            *sorted(os.path.join(envoi, n) for n in os.listdir(envoi)), f"{ARCHIVE}{sous}/"],
                           capture_output=True, text=True, timeout=3600)
        if r.returncode:
            return f"envoi vers pve2 impossible : {(r.stderr or r.stdout).strip()[-200:]}"
    finally:
        shutil.rmtree(envoi, ignore_errors=True)
    with lock:
        if jid not in jobs:
            return "inconnu"
        jobs[jid].update(statut="archivé", archive=f"{sous}/", archive_fichiers=noms, archive_le=time.time())
        jobs[jid].pop("fichier", None)
        jobs[jid].pop("apercu", None)
    for f in fichiers_du_job(j):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
    save()
    return "ok"


def nettoyer(jours=None, maintenant=None):
    """Archive sur pve2 (ou supprime si l'archivage n'est pas configuré) les créations de plus de `jours` jours
    non marquées « garder », puis supprime les fichiers de MEDIA qui
    n'appartiennent plus à aucune création, et les sorties brutes de ComfyUI du Studio du même âge.
    Les rendus publiés sont des liens durs : effacer l'original de ComfyUI ne les touche pas.
    Renvoie (créations archivées ou supprimées, fichiers supprimés)."""
    jours = GARDER_JOURS if jours is None else jours
    if jours <= 0 or not (ARCHIVE or MENAGE_SUPPRIMER):
        return 0, 0
    limite = (maintenant or time.time()) - jours * 86400
    with lock:
        vieux = [k for k, j in jobs.items() if j.get("cree", 0) < limite and not j.get("garder")
                 and j.get("statut") not in ("en attente", "en cours", "archivé")]
    n_jobs = 0
    for k in vieux:
        if ARCHIVE and jobs.get(k, {}).get("statut") == "terminé":
            r = archiver_job(k)  # en cas d'échec on ne supprime rien : on réessaiera au prochain ménage
            if r != "ok":
                log(f"archivage de {k} : {r}")
        else:  # échecs (rien à garder), ou archivage non configuré
            r = supprimer_job(k)
        n_jobs += r == "ok"
    n_fichiers = 0
    with lock:
        utilises = {os.path.basename(f) for j in jobs.values() for f in fichiers_du_job(j)} | {VIEWER}
    for nom in os.listdir(MEDIA) if os.path.isdir(MEDIA) else []:
        chemin = os.path.join(MEDIA, nom)
        if nom not in utilises and os.path.isfile(chemin) and os.path.getmtime(chemin) < limite:
            os.remove(chemin)
            n_fichiers += 1
    racine = os.path.join(COMFY_OUT, "studio")  # uniquement ce que le Studio a produit, jamais le reste de ComfyUI
    for dossier, sous, fichiers in os.walk(racine, topdown=False):
        for nom in fichiers:
            chemin = os.path.join(dossier, nom)
            if os.path.getmtime(chemin) < limite:
                os.remove(chemin)
                n_fichiers += 1
        if dossier != racine and not os.listdir(dossier):
            os.rmdir(dossier)
    return n_jobs, n_fichiers


def menage():
    if GARDER_JOURS > 0 and not (ARCHIVE or MENAGE_SUPPRIMER):
        log(f"ménage : STUDIO_GARDER_JOURS={GARDER_JOURS} mais ni STUDIO_ARCHIVE ni STUDIO_MENAGE_SUPPRIMER=1 : rien ne sera effacé")
    while True:
        try:
            n_jobs, n_fichiers = nettoyer()
            if n_jobs or n_fichiers:
                log(f"ménage : {n_jobs} création(s) {'archivée(s) sur pve2' if ARCHIVE else 'supprimée(s)'} et "
                    f"{n_fichiers} fichier(s) de plus de {GARDER_JOURS} jours supprimés")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(6 * 3600)


def place_utilisee():
    total = 0
    for racine in (MEDIA, os.path.join(COMFY_OUT, "studio")):
        vus = set()
        for dossier, _, fichiers in os.walk(racine):
            for nom in fichiers:
                st = os.stat(os.path.join(dossier, nom))
                if st.st_ino not in vus:  # un lien dur n'est compté qu'une fois
                    vus.add(st.st_ino)
                    total += st.st_size
    return total


# ---------------------------------------------------------------- HTTP

PAGE = r"""<!doctype html><html lang=fr><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Studio</title>
<script type=module src="/studio/media/model-viewer-4.0.0.min.js"></script>
<style>
:root{--fond:#0e0e10;--carte:#18181b;--carte2:#202024;--bord:#2a2a2f;--texte:#f2f2f3;--texte2:#a1a1aa;--discret:#71717a;
--accent:#7c6cf0;--accent2:#2d2a45;--bon:#22c55e;--attention:#f5b942;--critique:#f05252;--rayon:14px;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--fond:#f4f4f5;--carte:#fff;--carte2:#f4f4f5;--bord:#e4e4e7;--texte:#18181b;
--texte2:#52525b;--discret:#8a8a93;--accent2:#ece9fe;color-scheme:light}}
*{box-sizing:border-box}body{margin:0;background:var(--fond);color:var(--texte);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.page{max-width:1200px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:20px}
h1{font-size:28px;margin:0;letter-spacing:-.02em}.sous{color:var(--texte2);font-size:14px;margin-top:4px}
.stats{display:flex;gap:10px;flex-wrap:wrap}.stat{background:var(--carte);border:1px solid var(--bord);border-radius:12px;padding:8px 14px}
.stat b{display:block;font-size:18px}.stat span{font-size:12px;color:var(--discret)}
.filtres{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
.filtres button{background:var(--carte);color:var(--texte2);border:1px solid var(--bord);border-radius:999px;padding:6px 14px;font:inherit;font-size:13px;cursor:pointer}
.filtres button.actif{background:var(--accent2);color:var(--texte);border-color:var(--accent)}
.filtres button i{font-style:normal;color:var(--discret);margin-left:4px}
.grille{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;align-items:start}
.c{background:var(--carte);border:1px solid var(--bord);border-radius:var(--rayon);overflow:hidden;display:flex;flex-direction:column}
.c.encours{border-color:var(--accent)}
.media{background:#000;aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;min-height:0}
.media img,.media video,.media model-viewer{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:block}
.media.court{aspect-ratio:auto;height:76px;background:var(--carte2)}
.media .vide{color:var(--discret);font-size:13px;text-align:center;padding:20px}
.barre{position:absolute;left:0;right:0;bottom:0;height:3px;background:var(--accent);animation:p 1.6s ease-in-out infinite;transform-origin:left}
@keyframes p{0%{transform:scaleX(.05)}50%{transform:scaleX(.7)}100%{transform:scaleX(.05);transform-origin:right}}
.corps{padding:12px 14px 14px;display:flex;flex-direction:column;gap:8px;flex:1}
.haut{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--discret)}
.badge{font-size:11px;font-weight:600;border-radius:6px;padding:2px 7px;background:var(--carte2);color:var(--texte2);text-transform:uppercase;letter-spacing:.04em}
.etat{display:inline-flex;align-items:center;gap:5px}.etat:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--couleur,var(--discret))}
.prompt{font-size:14px;color:var(--texte);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer}
.prompt.ouvert{-webkit-line-clamp:unset}
.detail{font-size:12px;color:var(--discret)}.detail.err{color:var(--critique)}
.actions{display:flex;gap:6px;align-items:center;margin-top:auto;padding-top:4px}.actions .fin{margin-left:auto;display:flex;gap:6px}
.actions a,.actions button{font:inherit;font-size:13px;background:var(--carte2);color:var(--texte);border:1px solid var(--bord);border-radius:8px;padding:5px 11px;cursor:pointer;text-decoration:none;white-space:nowrap}
.actions .ico{width:32px;padding:5px 0;text-align:center;font-size:15px;line-height:1.2}
.actions button:hover,.actions a:hover{border-color:var(--accent)}
.actions .on{color:var(--attention);border-color:var(--attention)}.actions .danger{color:var(--critique)}
.vide-liste{color:var(--texte2);text-align:center;padding:60px 0}
@media (max-width:520px){.page{padding:18px 12px 40px}.grille{grid-template-columns:1fr}h1{font-size:24px}}
</style></head><body><div class=page>
<header><div><h1>Studio</h1><div class=sous id=p></div></div><div class=stats id=s></div></header>
<div class=filtres id=f></div><div class=grille id=l></div></div>
<script>
// Tout ce qui vient d'une création (prompt, étape, erreurs) est échappé : la page est sur la même adresse
// qu'Open WebUI, une injection HTML pourrait y voler la session. Seuls les liens /studio/media/<hex> sont acceptés.
const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const media=f=>/^\/studio\/media\/[0-9a-f]{32}\.[a-z0-9]{2,4}$/.test(f||'')?f:'';
const TYPES={image:'Image',video:'Vidéo',video_image:'Animation',video_longue:'Vidéo longue','3d':'3D','3d_texte':'3D'};
const FAMILLE=t=>t=='image'?'image':(t||'').startsWith('3d')?'3d':'video';
const ETATS={'terminé':['Terminée','var(--bon)'],'échec':['Échec','var(--critique)'],'en cours':['En cours','var(--accent)'],
 'en attente':['En attente','var(--attention)'],'archivé':['Archivée sur pve2','var(--discret)']};
const FILTRES=[['tout','Tout'],['image','Images'],['video','Vidéos'],['3d','3D'],['garde','Gardées'],['archive','Archivées'],['echec','Échecs']];
let filtre='tout',jobs={},avant='',infos={};
function ilya(t){const s=Date.now()/1000-t;if(s<90)return"à l'instant";if(s<3600)return`il y a ${Math.round(s/60)} min`;
 if(s<86400)return`il y a ${Math.round(s/3600)} h`;if(s<86400*30)return`il y a ${Math.round(s/86400)} j`;
 return new Date(t*1000).toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'})}
function garde(x){return!!x.garder}
function dans(x,f){return f=='tout'?x.statut!='archivé':f=='garde'?garde(x)&&x.statut!='archivé':f=='archive'?x.statut=='archivé':
 f=='echec'?x.statut=='échec':x.statut!='archivé'&&FAMILLE(x.type)==f}
function apercu(x){const f=media(x.fichier);
 if(x.statut=='terminé'&&f)return x.format=='image'?`<img src="${f}" loading=lazy alt="">`:x.format=='glb'?
  `<model-viewer src="${f}" camera-controls auto-rotate shadow-intensity=1></model-viewer>`:`<video src="${f}" controls loop playsinline preload=metadata></video>`;
 if(x.statut=='en cours'||x.statut=='en attente')return`<div class=vide>${e(x.etape||'…')}</div><div class=barre></div>`;
 if(x.statut=='archivé')return`<div class=vide>Archivée sur pve2<br>${e(x.archive||'')}</div>`;
 return`<div class=vide>Pas d'aperçu</div>`}
function carte(id,x){if(!/^[0-9a-f]{10}$/.test(id))return'';
 const [nomEtat,couleur]=ETATS[x.statut]||[x.statut,'var(--discret)'],f=media(x.fichier),fini=x.statut=='terminé'||x.statut=='échec';
 let a='',b='';
 if(x.statut=='terminé'&&f)a+=`<a href="${f}?dl=1" download>Télécharger</a>`;
 if(x.statut=='terminé'&&infos.archive)a+=`<button onclick="arc('${id}')" title="Envoyer sur pve2 et libérer le Spark">Archiver</button>`;
 if(fini)b+=`<button class="ico ${garde(x)?'on':''}" onclick="gar('${id}',${!garde(x)})" title="${garde(x)?'Gardée : jamais archivée ni supprimée automatiquement':'Garder sur le Spark'}" aria-label="Garder">${garde(x)?'★':'☆'}</button>`;
 if(fini||x.statut=='archivé')b+=`<button class="ico danger" onclick="sup('${id}')" title="Supprimer" aria-label="Supprimer">✕</button>`;
 a+=`<span class=fin>${b}</span>`;
 const detail=x.statut=='échec'?`<div class="detail err">${e(x.etape)}</div>`:x.statut=='archivé'?`<div class=detail>${e((x.archive_fichiers||[]).join(', '))}</div>`:'';
 const court=!(x.statut=='terminé'&&f)&&x.statut!='en cours';
 return`<div class="c${x.statut=='en cours'?' encours':''}"><div class="media${court?' court':''}">${apercu(x)}</div><div class=corps>
<div class=haut><span class=badge>${e(TYPES[x.type]||x.type)}</span><span class=etat style="--couleur:${couleur}">${e(nomEtat)}</span>
<span style="margin-left:auto" title="${e(new Date(x.cree*1000).toLocaleString('fr-FR'))}">${e(ilya(x.cree))}</span></div>
<div class=prompt onclick="this.classList.toggle('ouvert')">${e(x.prompt||x.style||'(sans description)')}</div>${detail}
<div class=actions>${a}</div></div></div>`}
function rendre(j,f){const l=Object.entries(j).filter(([,x])=>dans(x,f)).sort((a,b)=>(b[1].cree||0)-(a[1].cree||0));
 return l.length?l.map(([id,x])=>carte(id,x)).join(''):`<div class=vide-liste>Rien ici pour l'instant.</div>`}
function filtresHtml(j){return FILTRES.map(([k,n])=>{const c=Object.values(j).filter(x=>dans(x,k)).length;
 return`<button class="${k==filtre?'actif':''}" onclick="filtrer('${k}')">${n}<i>${c}</i></button>`}).join('')}
function statsHtml(j,p){const v=Object.values(j),go=(p.octets||0)/2**30;
 return`<div class=stat><b>${v.filter(x=>x.statut=='terminé').length}</b><span>créations</span></div>`+
 `<div class=stat><b>${go.toFixed(1).replace('.',',')} Go</b><span>sur le Spark</span></div>`+
 (p.archive?`<div class=stat><b>${v.filter(x=>x.statut=='archivé').length}</b><span>archivées sur pve2</span></div>`:'')}
function sousTitre(p){return p.garder_jours>0?`Après ${p.garder_jours} jours, les créations ${p.archive?'partent sur pve2':'sont supprimées'}, sauf celles marquées ★.`:''}
async function charger(force){const r=await (await fetch('/studio/api/jobs')).text();
 // on ne redessine que si quelque chose a changé : sinon une vidéo en lecture repartirait de zéro
 if(r===avant&&!force)return;avant=r;jobs=JSON.parse(r);dessiner()}
function dessiner(){document.getElementById('l').innerHTML=rendre(jobs,filtre);document.getElementById('f').innerHTML=filtresHtml(jobs);
 document.getElementById('s').innerHTML=statsHtml(jobs,infos);document.getElementById('p').textContent=sousTitre(infos)}
async function place(){infos=await (await fetch('/studio/api/place')).json();dessiner()}
function filtrer(k){filtre=k;dessiner()}
async function act(url,opt){const r=await fetch(url,opt);if(!r.ok){const t=await r.json().catch(()=>({}));alert(t.erreur||'Erreur')}await charger(true);place()}
function sup(id){if(confirm('Supprimer définitivement cette création ?'))act('/studio/api/jobs/'+id,{method:'DELETE'})}
function arc(id){if(confirm('Envoyer cette création sur pve2 et la retirer du Spark ?'))act('/studio/api/jobs/'+id+'/archiver',{method:'POST'})}
function gar(id,v){act('/studio/api/jobs/'+id+'/garder',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({garder:v})})}
if(typeof window!=='undefined'&&window.addEventListener){charger();place();setInterval(charger,10000)}
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, ctype="application/json"):
        data = obj.encode() if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", ctype + "; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/")
        if p in ("/studio", ""):
            return self.send(200, PAGE, "text/html")
        if p == "/studio/api/jobs":
            with lock:
                return self.send(200, {k: {x: y for x, y in v.items() if x != "image_b64"} for k, v in jobs.items()})
        if p == "/studio/api/place":
            return self.send(200, {"octets": place_utilisee(), "garder_jours": GARDER_JOURS if (ARCHIVE or MENAGE_SUPPRIMER) else 0, "archive": bool(ARCHIVE)})
        if p.startswith("/studio/api/jobs/"):
            jid = p.rsplit("/", 1)[1]
            with lock:
                j = jobs.get(jid)
                j = {x: y for x, y in j.items() if x != "image_b64"} if j else None
            if j:
                j["position"] = list(todo.queue).index(jid) + 1 if jid in todo.queue else 0
            return self.send(200 if j else 404, j or {"erreur": "inconnu"})
        self.send(404, {"erreur": "inconnu"})

    def do_DELETE(self):
        m = re.fullmatch(r"/studio/api/jobs/([0-9a-f]{10})", self.path.split("?")[0].rstrip("/"))
        if not m:
            return self.send(404, {"erreur": "inconnu"})
        r = supprimer_job(m.group(1))
        return self.send({"ok": 200, "inconnu": 404, "en cours": 409}[r],
                         {"ok": True} if r == "ok" else {"erreur": "création inconnue" if r == "inconnu" else
                                                          "création en cours : attends qu'elle finisse"})

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        m = re.fullmatch(r"/studio/api/jobs/([0-9a-f]{10})/archiver", self.path.split("?")[0].rstrip("/"))
        if m:
            r = archiver_job(m.group(1))
            codes = {"ok": 200, "inconnu": 404, "en cours": 409, "pas configuré": 501}
            return self.send(codes.get(r, 502), {"ok": True} if r == "ok" else {"erreur": {
                "inconnu": "création inconnue", "en cours": "création en cours : attends qu'elle finisse",
                "pas configuré": "archivage sur pve2 pas configuré (STUDIO_ARCHIVE)"}.get(r, r)})
        m = re.fullmatch(r"/studio/api/jobs/([0-9a-f]{10})/garder", self.path.split("?")[0].rstrip("/"))
        if m:
            try:
                garder = bool(json.loads(self.rfile.read(min(n, 1000)) or b"{}").get("garder", True))
            except ValueError:
                return self.send(400, {"erreur": "json invalide"})
            with lock:
                if m.group(1) not in jobs:
                    return self.send(404, {"erreur": "création inconnue"})
                jobs[m.group(1)]["garder"] = garder
            save()
            return self.send(200, {"ok": True, "garder": garder})
        if self.path.rstrip("/") != "/studio/api/jobs":
            return self.send(404, {"erreur": "inconnu"})
        if n > 60 * 2**20:  # une image en base64 fait quelques Mo ; au-delà, on refuse au lieu de remplir la mémoire
            return self.send(413, {"erreur": "requête trop grosse"})
        try:
            req = json.loads(self.rfile.read(n))
        except ValueError:
            return self.send(400, {"erreur": "json invalide"})
        kind = req.get("type")
        if kind not in ("video", "video_image", "3d", "3d_texte", "video_longue", "image"):
            return self.send(400, {"erreur": "type inconnu"})
        if kind in ("video_image", "3d") and not req.get("image_b64"):
            return self.send(400, {"erreur": "image manquante"})
        scenes = [str(x)[:3000] for x in (req.get("scenes") or []) if str(x).strip()][:MAX_SCENES]
        if kind == "video_longue" and len(scenes) < 2:
            return self.send(400, {"erreur": "il faut au moins 2 scènes"})
        jid = uuid.uuid4().hex[:10]
        with lock:
            jobs[jid] = {"type": kind, "prompt": str(req.get("prompt", ""))[:4000], "secondes": req.get("secondes"),
                         "image_b64": req.get("image_b64"), "scenes": scenes, "style": str(req.get("style", ""))[:1500],
                         "format": str(req.get("format", ""))[:20], "modele": str(req.get("modele", ""))[:20],
                         "qualite": req.get("qualite") if req.get("qualite") in QUALITES else "rapide",
                         "statut": "en attente", "etape": "en file d'attente",
                         "cree": time.time()}
        save()
        todo.put(jid)
        self.send(200, {"id": jid, "position": todo.qsize()})


def main():
    os.makedirs(MEDIA, exist_ok=True)
    src = os.path.join(BASE, "static", VIEWER)
    if os.path.exists(src):
        shutil.copyfile(src, os.path.join(MEDIA, VIEWER))
        os.chmod(os.path.join(MEDIA, VIEWER), 0o644)
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE) as f:
            jobs.update(json.load(f))
        for j in jobs.values():  # tâches interrompues par un redémarrage
            if j.get("statut") in ("en attente", "en cours"):
                j.update(statut="échec", etape="interrompu (redémarrage du service)")
            j.pop("image_b64", None)
    # Si le service a été coupé pendant une tâche, le chat est peut-être resté en pause
    if LLAMA_MODE == "swap":
        try:
            os.remove(PAUSE)
        except FileNotFoundError:
            pass
    else:
        subprocess.run(["systemctl", "start", "--no-block", LLAMA_UNIT], check=False)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=menage, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 8190), H).serve_forever()


if __name__ == "__main__":
    main()
