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
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/opt/ai-stack/studio"
MEDIA = os.path.join(BASE, "media")
JOBS_FILE = os.path.join(BASE, "jobs.json")
WORKFLOWS = os.path.join(BASE, "workflows")
COMFY = "http://127.0.0.1:8188"
COMFY_OUT = "/opt/ai-stack/comfyui/output"
LLAMA = "http://127.0.0.1:8090"
LLAMA_UNIT = "llama-flashnext"

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

def llama_busy():
    try:
        return any(s.get("is_processing") for s in http("GET", LLAMA + "/slots", timeout=5))
    except Exception:  # noqa: BLE001 — arrêté ou en veille : pas occupé
        return False


def stop_llama(jid):
    # Laisse le chat finir sa réponse en cours (jusqu'à 3 min)
    t0 = time.time()
    while llama_busy() and time.time() - t0 < 180:
        update(jid, etape="j'attends que le chat finisse sa réponse")
        time.sleep(3)
    update(jid, etape="je mets le chat en pause pour libérer la mémoire")
    subprocess.run(["systemctl", "stop", LLAMA_UNIT], check=False)


def start_llama():
    subprocess.run(["systemctl", "start", "--no-block", LLAMA_UNIT], check=False)
    t0 = time.time()
    while time.time() - t0 < 300:
        try:
            if http("GET", LLAMA + "/health", timeout=5).get("status") == "ok":
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
    shutil.copy2(src, os.path.join(MEDIA, name))
    os.chmod(os.path.join(MEDIA, name), 0o644)
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
            chat_arrete = subprocess.run(["systemctl", "is-active", "--quiet", LLAMA_UNIT]).returncode != 0
            if not llama_stopped and not (chat_arrete and todo.empty()):
                update(jid, relance_chat="inutile")
            elif todo.empty():  # (une image passée derrière une vidéo relance aussi le chat)
                update(jid, relance_chat="en cours")
                ok = start_llama()
                update(jid, relance_chat="ok" if ok else "échec")


# ---------------------------------------------------------------- HTTP

PAGE = """<!doctype html><html lang=fr><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Studio</title>
<script type=module src="https://cdn.jsdelivr.net/npm/@google/model-viewer@4.0.0/dist/model-viewer.min.js"></script>
<style>body{font-family:system-ui;background:#111;color:#eee;margin:0;padding:16px}
.c{background:#1c1c1c;border-radius:12px;padding:12px;margin:0 0 16px;max-width:900px}
video,model-viewer{width:100%;max-height:70vh;height:60vh;background:#000;border-radius:8px}
small{color:#999}a{color:#8cf}</style></head><body><h1>🎬 Studio</h1><div id=l></div>
<script>
// Tout est échappé : prompt, étape et messages d'erreur peuvent contenir du texte venu du web.
// La page est sur la même adresse qu'Open WebUI, une injection HTML pourrait y voler la session.
const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const media=f=>/^\\/studio\\/media\\/[0-9a-f]{32}\\.[a-z0-9]{2,4}$/.test(f||'')?f:'';
async function go(){const j=await (await fetch('api/jobs')).json();
document.getElementById('l').innerHTML=Object.entries(j).reverse().map(([id,x])=>{
let m='';const f=media(x.fichier);if(x.statut=='terminé'&&f){m=x.format=='image'?`<img src="${f}" style="max-width:100%;border-radius:8px">`:x.format=='glb'?`<model-viewer src="${f}" camera-controls auto-rotate shadow-intensity=1></model-viewer>`:`<video src="${f}" controls loop playsinline></video>`;
m+=`<br><a href="${f}" download>⬇️ Télécharger</a>`}
return `<div class=c><b>${e(x.type)}</b> — ${e(x.statut)}<br>${e(x.prompt)}<br><small>${e(x.etape)} · ${new Date(x.cree*1000).toLocaleString()}</small><br>${m}</div>`}).join('')}
go();setInterval(go,10000)
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
        if p.startswith("/studio/api/jobs/"):
            jid = p.rsplit("/", 1)[1]
            with lock:
                j = jobs.get(jid)
                j = {x: y for x, y in j.items() if x != "image_b64"} if j else None
            if j:
                j["position"] = list(todo.queue).index(jid) + 1 if jid in todo.queue else 0
            return self.send(200 if j else 404, j or {"erreur": "inconnu"})
        self.send(404, {"erreur": "inconnu"})

    def do_POST(self):
        if self.path.rstrip("/") != "/studio/api/jobs":
            return self.send(404, {"erreur": "inconnu"})
        n = int(self.headers.get("content-length", 0))
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
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE) as f:
            jobs.update(json.load(f))
        for j in jobs.values():  # tâches interrompues par un redémarrage
            if j.get("statut") in ("en attente", "en cours"):
                j.update(statut="échec", etape="interrompu (redémarrage du service)")
            j.pop("image_b64", None)
        # Si le service a été coupé pendant une tâche, le chat est peut-être resté arrêté
        subprocess.run(["systemctl", "start", "--no-block", LLAMA_UNIT], check=False)
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 8190), H).serve_forever()


if __name__ == "__main__":
    main()
