"""
title: Proxmox
description: Gère le cluster Proxmox de la maison (pve + pve2) : tableau de bord avec graphiques, VM/conteneurs, sauvegardes, création et suppression par fenêtres. Toute action qui change quelque chose demande d'abord la confirmation de l'utilisateur.
version: 2.0
"""

# Outil Open WebUI créé le 19/10/2026. Clé API spark@pve!spark dans les valves.
# Droits de la clé (voir README, « Sécuriser la clé Proxmox de l'IA ») : lecture seule sur tout le cluster,
# actions seulement sur le pool « agent ». C'est Proxmox qui bloque le reste ; la liste PROTEGES ci-dessous
# n'est qu'une seconde barrière. Les valves ne sont chiffrées que si ENABLE_VALVE_ENCRYPTION=true dans Open WebUI.
# Règles : lecture libre ; démarrer/arrêter/créer/sauvegarder = fenêtre de confirmation ;
# les conteneurs d'infrastructure (coffre, VPN, proxy) ne peuvent pas être arrêtés ni supprimés par l'agent.

import asyncio
import html
import json
import math
import secrets
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

PROTEGES = {110: "vaultwarden (coffre de mots de passe)", 111: "vpn (WireGuard)", 112: "proxy (HAProxy)", 113: "languagetool (correcteur de grammaire)"}
MODELE_CT = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
CLE_SSH_SPARK = "<CLE_SSH_PUBLIQUE_DU_SPARK>"
# Règle de la maison : IP = 192.168.0.(ID + 100), ID de 110 à 149 → IP .210 à .249 (ex. 113 → .213)
IDS = range(110, 150)


def _h(ts):
    return datetime.fromtimestamp(ts or 0, ZoneInfo("Europe/Paris")).strftime("%d/%m %H:%M")


def _ip_occupee(ip):
    """Vrai si un appareil du réseau répond déjà à cette adresse (vérification ARP, pas besoin de ping)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as so:
            so.sendto(b"x", (ip, 9))
    except OSError:
        pass
    time.sleep(1.5)
    try:
        for ligne in open("/proc/net/arp").read().splitlines()[1:]:
            c = ligne.split()
            if c[0] == ip and c[2] == "0x2":
                return True
    except OSError:
        pass
    return False


def _go(n):
    return round((n or 0) / 2**30, 1)


# ---------- graphiques (affichés dans la conversation) ----------
TZ = ZoneInfo("Europe/Paris")
SYSTEMES = {  # choix proposés dans la fenêtre de création : nom affiché -> modèle Proxmox (téléchargé si absent)
    "Debian 13": "debian-13-standard_13.1-2_amd64.tar.zst",
    "Debian 12": "debian-12-standard_12.12-1_amd64.tar.zst",
    "Ubuntu 24.04": "ubuntu-24.04-standard_24.04-2_amd64.tar.zst",
    "Ubuntu 26.04": "ubuntu-26.04-standard_26.04-1_amd64.tar.zst",
    "Alpine 3.24": "alpine-3.24-default_20260714_amd64.tar.xz",
}
NOEUDS = ("pve", "pve2")


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def _couleur(pct, orange, rouge):
    return "#ff5c5c" if pct >= rouge else "#f5b942" if pct >= orange else "#2ecc71"


def _jauge(titre, pct, texte, orange=75, rouge=90):
    pct = max(0.0, min(100.0, pct))
    a = math.pi * (1 - pct / 100)
    x, y = 60 + 48 * math.cos(a), 58 - 48 * math.sin(a)
    arc = (f'<path d="M12,58 A48,48 0 0 1 {x:.1f},{y:.1f}" fill="none" stroke="{_couleur(pct, orange, rouge)}" '
           f'stroke-width="10" stroke-linecap="round"/>' if pct > 0.5 else "")
    return (f'<div class="j"><svg viewBox="0 0 120 70"><path d="M12,58 A48,48 0 0 1 108,58" fill="none" stroke="#3334" '
            f'stroke-width="10" stroke-linecap="round"/>{arc}<text x="60" y="54" text-anchor="middle" font-size="13" '
            f'font-weight="600" fill="#eee">{_esc(texte)}</text></svg><div class="jt">{_esc(titre)}</div></div>')


def _barre(pct, texte=""):
    pct = max(0.0, min(100.0, pct))
    return (f'<div class="b"><div style="width:{pct:.0f}%;background:{_couleur(pct, 75, 90)}"></div></div>'
            f'<span class="bt">{_esc(texte)}</span>')


def _courbes(titre, series, points, unite, mini=0, maxi=100, hauteur=120):
    pts = [p for p in points if any(p.get(k) is not None for k, _, _ in series)]
    if len(pts) < 2:
        return f'<div class="g"><div class="gt">{titre}</div><div class="vide">Pas encore de données.</div></div>'
    W, H, g, d, h, b = 640, hauteur, 40, 10, 10, 20
    vals = [p[k] for p in pts for k, _, _ in series if p.get(k) is not None]
    hi, lo = max(maxi, max(vals)), min(mini, min(vals))
    t0, t1 = pts[0]["t"], pts[-1]["t"]
    x = lambda t: g + (t - t0) * (W - g - d) / max(1, t1 - t0)
    y = lambda v: h + (hi - v) * (H - h - b) / ((hi - lo) or 1)
    svg = ""
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        svg += (f'<line x1="{g}" x2="{W-d}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#8883" stroke-width="0.5"/>'
                f'<text x="{g-5}" y="{y(v)+4:.1f}" text-anchor="end" font-size="10" fill="#999">{v:.0f}</text>')
    for t, ancre in ((t0, "start"), ((t0 + t1) // 2, "middle"), (t1, "end")):
        svg += (f'<text x="{x(t):.1f}" y="{H-5}" text-anchor="{ancre}" font-size="10" fill="#999">'
                f'{datetime.fromtimestamp(t, TZ).strftime("%H:%M")}</text>')
    leg = ""
    for k, nom, c in series:
        chemin = " ".join(f"{'M' if i == 0 else 'L'}{x(p['t']):.1f},{y(p[k]):.1f}"
                          for i, p in enumerate(q for q in pts if q.get(k) is not None))
        svg += f'<path d="{chemin}" fill="none" stroke="{c}" stroke-width="1.6"/>'
        dern = next((p[k] for p in reversed(pts) if p.get(k) is not None), None)
        if dern is not None:
            leg += f'<span><i style="background:{c}"></i>{nom} : <b>{dern:.0f}{unite}</b></span>'
    return (f'<div class="g"><div class="gt">{titre}</div><div class="lg">{leg}</div>'
            f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto">{svg}</svg></div>')


def _page(titre, contenu):
    resize = ("<script>function h(){parent.postMessage({type:'iframe:height',height:document.documentElement.scrollHeight},'*')}"
              "addEventListener('load',h);new ResizeObserver(h).observe(document.body);setTimeout(h,300)</script>")
    css = ("body{margin:0;font-family:system-ui;color:#ddd;background:#1c1c20}.c{padding:12px 14px}"
           ".t{font-size:15px;font-weight:600;margin-bottom:8px}.s{font-size:12px;color:#999;margin:-4px 0 10px}"
           ".nds{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:10px}"
           ".nd{background:#26262c;border-radius:8px;padding:8px 10px}.ndt{font-weight:600;font-size:14px}.ndt small{font-weight:400;color:#999;margin-left:6px}"
           ".js{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}.j{text-align:center}.j svg{width:100%;max-width:130px}"
           ".jt{font-size:11.5px;color:#aaa;margin-top:-4px}"
           ".g{background:#26262c;border-radius:8px;padding:8px 10px;margin-bottom:10px}.gt{font-size:13px;font-weight:600}"
           ".lg{font-size:12px;color:#aaa;display:flex;gap:14px;flex-wrap:wrap;margin:2px 0 4px}"
           ".lg i{display:inline-block;width:10px;height:3px;margin-right:5px;vertical-align:middle}.vide{font-size:12px;color:#888;padding:14px 0}"
           ".cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}"
           ".tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px}td,th{padding:4px 5px;border-bottom:1px solid #333;"
           "text-align:left;white-space:nowrap}th{color:#999;font-weight:500}"
           ".p{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}"
           ".b{display:inline-block;width:70px;height:7px;background:#3335;border-radius:4px;vertical-align:middle;overflow:hidden}"
           ".b div{height:100%}.bt{font-size:11.5px;color:#aaa;margin-left:6px}.tag{font-size:10.5px;color:#aaa;border:1px solid #444;"
           "border-radius:6px;padding:0 5px;margin-left:4px}.n{font-size:11px;color:#888;margin-top:4px}")
    return HTMLResponse(f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body><div class="c">'
                        f'<div class="t">{titre}</div>{contenu}</div>{resize}</body></html>',
                        headers={"Content-Disposition": "inline"})


# ---------- fenêtres (créer / supprimer) ----------
# Ouvertes dans le navigateur de l'utilisateur. Tout ce qui revient de la fenêtre est revérifié côté serveur.
FENETRE_CREER_JS = r"""
const P = __PARAMS__;
return await new Promise((resolve) => {
  let fini = false;
  const ov = document.createElement('div');
  const fin = (v) => { if (fini) return; fini = true; ov.remove(); resolve(v); };
  ov.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;padding:16px;font-family:system-ui,sans-serif';
  const box = document.createElement('div');
  box.style.cssText = 'background:#1e1e22;color:#eee;border-radius:16px;padding:20px;width:100%;max-width:440px;max-height:92vh;overflow:auto;box-shadow:0 10px 40px rgba(0,0,0,.5)';
  const h = (t, css, txt) => { const e = document.createElement(t); if (css) e.style.cssText = css; if (txt != null) e.textContent = txt; return e; };
  const champ = 'width:100%;box-sizing:border-box;padding:8px 10px;border-radius:8px;border:1px solid #444;background:#26262b;color:#eee;font-size:14px';
  const lab = (t) => h('div', 'margin:12px 0 5px;font-size:13px;color:#bbb', t);
  box.append(h('div', 'font-size:18px;font-weight:600;margin-bottom:4px', 'Nouveau conteneur'),
             h('div', 'font-size:13px;color:#999', 'N° ' + P.vmid + ' · adresse ' + P.ip + ' (règle : IP = n° + 100)'));
  box.append(lab('Nom'));
  const nom = h('input', champ); nom.value = P.nom; nom.maxLength = 30; box.append(nom);
  box.append(lab('Système'));
  const os = h('select', champ);
  for (const s of P.systemes) { const o = h('option', '', s + (P.locaux.includes(s) ? '' : ' (téléchargé à la création)')); o.value = s; if (s === P.systeme) o.selected = true; os.append(o); }
  box.append(os);
  const curseur = (titre, min, max, pas, val, fmt) => {
    const l = lab(''); const r = h('input', 'width:100%;accent-color:#7c6cf0');
    r.type = 'range'; r.min = min; r.max = max; r.step = pas; r.value = val;
    const maj = () => { l.textContent = titre + ' : ' + fmt(+r.value); }; r.oninput = maj; maj();
    box.append(l, r); return r;
  };
  const mem = curseur('Mémoire', 256, 16384, 256, P.memoire_mo, (v) => v >= 1024 ? (v / 1024).toFixed(v % 1024 ? 2 : 0).replace('.', ',') + ' Go' : v + ' Mo');
  const dsk = curseur('Disque', 2, 200, 1, P.disque_go, (v) => v + ' Go');
  const cpu = curseur('Cœurs', 1, 8, 1, P.coeurs, (v) => v + (v > 1 ? ' cœurs' : ' cœur'));
  box.append(lab('Serveur'));
  const nd = h('select', champ);
  for (const [n, det] of P.noeuds) { const o = h('option', '', n + ' — ' + det); o.value = n; if (n === P.noeud) o.selected = true; nd.append(o); }
  box.append(nd);
  const dl = h('label', 'display:flex;gap:8px;align-items:center;margin-top:14px;font-size:14px;cursor:pointer');
  const dk = h('input', ''); dk.type = 'checkbox'; dk.checked = P.docker; dl.append(dk, h('span', '', 'Pouvoir y faire tourner Docker'));
  box.append(dl, h('div', 'font-size:12px;color:#999;margin-top:12px', 'Démarre avec le serveur et entre dans la sauvegarde de chaque nuit.'));
  const err = h('div', 'color:#ff7a7a;font-size:13px;min-height:18px;margin-top:8px', '');
  const bar = h('div', 'display:flex;gap:10px;justify-content:flex-end;margin-top:8px');
  const an = h('button', 'padding:9px 16px;border-radius:10px;border:1px solid #555;background:transparent;color:#eee;cursor:pointer', 'Annuler');
  const go = h('button', 'padding:9px 18px;border-radius:10px;border:0;background:#7c6cf0;color:#fff;font-weight:600;cursor:pointer', 'Créer');
  an.onclick = () => fin({ ok: false });
  go.onclick = () => {
    const n = nom.value.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9-]{0,29}$/.test(n)) { err.textContent = 'Nom : lettres minuscules, chiffres et tirets.'; return; }
    fin({ ok: true, nom: n, systeme: os.value, memoire_mo: +mem.value, disque_go: +dsk.value, coeurs: +cpu.value, noeud: nd.value, docker: dk.checked });
  };
  bar.append(an, go); box.append(err, bar);
  ov.append(box); document.body.append(ov); nom.focus();
  setTimeout(() => fin(null), 15 * 60 * 1000);
});
"""

FENETRE_SUPPRIMER_JS = r"""
const P = __PARAMS__;
return await new Promise((resolve) => {
  let fini = false;
  const ov = document.createElement('div');
  const fin = (v) => { if (fini) return; fini = true; ov.remove(); resolve(v); };
  ov.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.65);display:flex;align-items:center;justify-content:center;padding:16px;font-family:system-ui,sans-serif';
  const box = document.createElement('div');
  box.style.cssText = 'background:#241c1e;color:#eee;border:1px solid #7a2e35;border-radius:16px;padding:20px;width:100%;max-width:440px;box-shadow:0 10px 40px rgba(0,0,0,.5)';
  const h = (t, css, txt) => { const e = document.createElement(t); if (css) e.style.cssText = css; if (txt != null) e.textContent = txt; return e; };
  box.append(h('div', 'font-size:18px;font-weight:600;color:#ff7a7a;margin-bottom:10px', 'Supprimer définitivement ?'));
  const t = h('table', 'width:100%;font-size:14px;border-collapse:collapse;margin-bottom:12px');
  for (const [k, v] of P.lignes) { const tr = h('tr'); tr.append(h('td', 'color:#aaa;padding:3px 0', k), h('td', 'text-align:right;padding:3px 0', v)); t.append(tr); }
  box.append(t, h('div', 'font-size:13px;color:#ddd;margin-bottom:12px', 'La machine et tous ses disques seront effacés. Seules les sauvegardes déjà faites permettraient de la récupérer.'));
  let arr = null;
  if (P.allumee) {
    const l = h('label', 'display:flex;gap:8px;align-items:center;margin-bottom:12px;font-size:14px;cursor:pointer');
    arr = h('input', ''); arr.type = 'checkbox'; l.append(arr, h('span', '', 'Elle est allumée : l\'arrêter d\'abord')); box.append(l);
  }
  box.append(h('div', 'font-size:13px;color:#bbb;margin-bottom:6px', 'Pour confirmer, tape son nom : ' + P.nom));
  const ch = h('input', 'width:100%;box-sizing:border-box;padding:8px 10px;border-radius:8px;border:1px solid #5a3a3e;background:#1c1618;color:#eee;font-size:14px');
  box.append(ch);
  const bar = h('div', 'display:flex;gap:10px;justify-content:flex-end;margin-top:16px');
  const an = h('button', 'padding:9px 16px;border-radius:10px;border:1px solid #555;background:transparent;color:#eee;cursor:pointer', 'Annuler');
  const go = h('button', 'padding:9px 18px;border-radius:10px;border:0;background:#c0392b;color:#fff;font-weight:600;cursor:pointer;opacity:.4', 'Supprimer');
  go.disabled = true;
  const maj = () => { const ok = ch.value.trim() === P.nom && (!P.allumee || arr.checked); go.disabled = !ok; go.style.opacity = ok ? 1 : .4; };
  ch.oninput = maj; if (arr) arr.onchange = maj;
  an.onclick = () => fin({ ok: false });
  go.onclick = () => { if (!go.disabled) fin({ ok: true, nom_tape: ch.value.trim(), arreter: arr ? arr.checked : false }); };
  bar.append(an, go); box.append(bar);
  ov.append(box); document.body.append(ov); ch.focus();
  setTimeout(() => fin(null), 15 * 60 * 1000);
});
"""


async def _fenetre(__event_call__, js, params):
    """Ouvre une fenêtre dans le navigateur. Renvoie le dict choisi, ou None si elle n'a pas pu s'ouvrir."""
    if not __event_call__:
        return None
    code = "try {" + js.replace("__PARAMS__", json.dumps(params, ensure_ascii=False)) + "} catch (e) { return null; }"
    try:
        r = await __event_call__({"type": "execute", "data": {"code": code}})
    except Exception:  # noqa: BLE001
        return None
    return r if isinstance(r, dict) and "ok" in r else None


class Tools:
    class Valves(BaseModel):
        hote: str = Field("192.168.0.200", description="Adresse d'un nœud du cluster")
        token_id: str = Field("", description="ex. spark@pve!spark")
        token_secret: str = Field("", description="Secret de la clé API")
        ca_pem: str = Field("", description="Certificat racine du cluster (pve-root-ca.pem)")
        pool: str = Field("", description="Pool Proxmox où ranger les conteneurs créés (ex. agent). Vide = aucun pool.")

    def __init__(self):
        self.valves = self.Valves()

    # ---------- accès API ----------
    def _ctx(self):
        if self.valves.ca_pem.strip():
            ctx = ssl.create_default_context(cadata=self.valves.ca_pem)
            ctx.check_hostname = False  # certificat émis pour pve.example.com, on parle à l'IP
            return ctx
        return ssl._create_unverified_context()

    def _api(self, methode, chemin, donnees=None):
        url = f"https://{self.valves.hote}:8006/api2/json{chemin}"
        corps = urllib.parse.urlencode(donnees, doseq=True).encode() if donnees else None
        req = urllib.request.Request(url, data=corps, method=methode, headers={
            "Authorization": f"PVEAPIToken={self.valves.token_id}={self.valves.token_secret}"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=self._ctx()) as r:
                return json.load(r).get("data")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Proxmox a refusé ({e.code}) : {e.read().decode()[:300]}")

    async def _a(self, *args):
        return await asyncio.to_thread(self._api, *args)

    async def _attendre_tache(self, noeud, upid, limite=600):
        t0 = time.time()
        while time.time() - t0 < limite:
            st = await self._a("GET", f"/nodes/{noeud}/tasks/{urllib.parse.quote(upid, safe='')}/status")
            if st.get("status") == "stopped":
                return st.get("exitstatus", "?")
            await asyncio.sleep(2)
        return "toujours en cours"

    async def _confirmer(self, __event_call__, titre, message):
        if not __event_call__:
            return False
        try:
            return await __event_call__({"type": "confirmation", "data": {"title": titre, "message": message}}) is True
        except Exception:  # noqa: BLE001
            return False

    async def _invite(self, vmid):
        for r in await self._a("GET", "/cluster/resources?type=vm"):
            if r.get("vmid") == int(vmid):
                return r
        return None

    async def _statut(self, emitter, msg, fini=False):
        if emitter:
            await emitter({"type": "status", "data": {"description": msg, "done": fini}})

    # ---------- lecture (sans confirmation) ----------
    async def etat_cluster(self) -> str:
        """
        Affiche le TABLEAU DE BORD du cluster Proxmox avec jauges et courbes sur 24 h : serveurs (processeur, mémoire,
        disques), toutes les VM et conteneurs (allumé/éteint, mémoire, IP), et le stockage. À utiliser pour « comment vont
        mes serveurs », « qu'est-ce qui tourne », « montre-moi Proxmox », etc.
        """
        try:
            res = await self._a("GET", "/cluster/resources")
        except Exception as e:  # noqa: BLE001
            return f"Impossible de joindre Proxmox : {e}"
        texte = self._texte_cluster(res)
        try:
            page = await self._tableau_de_bord(res)
        except Exception as e:  # noqa: BLE001
            return texte + f"\n(Le graphique n'a pas pu être construit : {e})"
        return page, ("Tableau de bord Proxmox affiché à l'utilisateur (jauges, courbes 24 h, liste des machines). Données :\n"
                      + texte + "\nRéponds en français simple en 3 à 5 phrases : dis si tout va bien, signale ce qui mérite "
                      "attention (serveur éteint, mémoire ou disque au-dessus de 85 %, machine arrêtée qui devrait tourner) et "
                      "réponds à la question. Ne recopie pas toute la liste.")

    async def _tableau_de_bord(self, res):
        noeuds = sorted([r for r in res if r["type"] == "node"], key=lambda n: n["node"])
        machines = sorted([r for r in res if r["type"] in ("qemu", "lxc")], key=lambda r: r["vmid"])

        async def rrd(n):
            try:
                return n, await self._a("GET", f"/nodes/{n}/rrddata?timeframe=day&cf=AVERAGE")
            except Exception:  # noqa: BLE001
                return n, []

        async def ip(v):
            if v["type"] != "lxc":
                return v["vmid"], ""
            try:
                conf = await self._a("GET", f"/nodes/{v['node']}/lxc/{v['vmid']}/config")
                net = conf.get("net0", "")
                return v["vmid"], next((p[3:].split("/")[0] for p in net.split(",") if p.startswith("ip=")), "")
            except Exception:  # noqa: BLE001
                return v["vmid"], ""

        rrds = dict(await asyncio.gather(*[rrd(n["node"]) for n in noeuds if n.get("status") == "online"]))
        ips = dict(await asyncio.gather(*[ip(v) for v in machines]))

        cartes = ""
        for n in noeuds:
            en_ligne = n.get("status") == "online"
            if en_ligne:
                cpu = n.get("cpu", 0) * 100
                mem = 100 * n.get("mem", 0) / (n.get("maxmem") or 1)
                dsk = 100 * n.get("disk", 0) / (n.get("maxdisk") or 1)
                corps = ('<div class="js">' + _jauge("Processeur", cpu, f"{cpu:.0f} %")
                         + _jauge("Mémoire", mem, f"{_go(n.get('mem')):.0f}/{_go(n.get('maxmem')):.0f} Go")
                         + _jauge("Disque système", dsk, f"{dsk:.0f} %") + "</div>")
                info = f"en ligne depuis {n.get('uptime', 0) / 86400:.1f} j · {n.get('maxcpu')} cœurs"
            else:
                corps, info = '<div class="vide" style="color:#ff7a7a">Hors ligne</div>', "ne répond pas"
            nb = sum(1 for v in machines if v.get("node") == n["node"] and v.get("status") == "running")
            cartes += (f'<div class="nd"><div class="ndt"><span class="p" style="background:{"#2ecc71" if en_ligne else "#ff5c5c"}">'
                       f'</span>{_esc(n["node"])}<small>{info} · {nb} machine(s) allumée(s)</small></div>{corps}</div>')

        couleurs = {"pve": "#4fa3ff", "pve2": "#f5b942"}
        par_t = {}
        for n, lignes in rrds.items():
            for l in lignes or []:
                if l.get("cpu") is None:
                    continue
                p = par_t.setdefault(l["time"], {"t": l["time"]})
                p[f"cpu_{n}"] = l["cpu"] * 100
                if l.get("memtotal"):
                    p[f"mem_{n}"] = 100 * l.get("memused", 0) / l["memtotal"]
        pts = [par_t[t] for t in sorted(par_t)]
        courbes = (_courbes("Processeur sur 24 h (%)", [(f"cpu_{n}", n, couleurs.get(n, "#a78bfa")) for n in rrds], pts, " %")
                   + _courbes("Mémoire sur 24 h (%)", [(f"mem_{n}", n, couleurs.get(n, "#a78bfa")) for n in rrds], pts, " %"))

        lignes = ""
        for v in machines:
            allume = v.get("status") == "running"
            memp = 100 * v.get("mem", 0) / (v.get("maxmem") or 1) if allume else 0
            prot = '<span class="tag">protégée</span>' if v["vmid"] in PROTEGES else ""
            txt_mem = f"{_go(v.get('mem')) if allume else 0:g}/{_go(v.get('maxmem')):g} Go"
            lignes += (f'<tr><td><span class="p" style="background:{"#2ecc71" if allume else "#777"}"></span>{v["vmid"]}</td>'
                       f'<td>{_esc(v.get("name"))}{prot}</td><td>{"VM" if v["type"] == "qemu" else "conteneur"}</td>'
                       f'<td>{_esc(v.get("node"))}</td>'
                       f'<td>{_barre(memp, txt_mem)}</td>'
                       f'<td>{_esc(ips.get(v["vmid"]))}</td></tr>')
        tableau = ('<div class="g"><div class="gt">Machines</div><div class="tw"><table><tr><th>N°</th><th>Nom</th><th>Type</th>'
                   f'<th>Serveur</th><th>Mémoire</th><th>IP</th></tr>{lignes}</table></div></div>')

        vus, stock = set(), ""
        for s in sorted([r for r in res if r["type"] == "storage"], key=lambda s: (not s.get("shared"), s["storage"], s["node"])):
            cle = s["storage"] if s.get("shared") else f"{s['storage']} ({s['node']})"
            if cle in vus or not s.get("maxdisk"):
                continue
            vus.add(cle)
            p = 100 * s.get("disk", 0) / s["maxdisk"]
            txt = f"{_go(s.get('disk')):g}/{_go(s['maxdisk']):g} Go"
            stock += f'<tr><td>{_esc(cle)}</td><td>{_barre(p, txt)}</td></tr>'
        stockage = f'<div class="g"><div class="gt">Stockage</div><table>{stock}</table></div>'

        allumes = sum(v.get("status") == "running" for v in machines)
        hors = [n["node"] for n in noeuds if n.get("status") != "online"]
        sous = (f'{"Tout est en ligne" if not hors else "Hors ligne : " + ", ".join(hors)} · {allumes}/{len(machines)} machines allumées · '
                f'{datetime.now(TZ).strftime("%d/%m à %H:%M")}')
        return _page("Cluster Proxmox", f'<div class="s">{_esc(sous)}</div><div class="nds">{cartes}</div>{courbes}'
                     f'{tableau}{stockage}<div class="n">Pastille verte = allumée, grise = éteinte.</div>')

    def _texte_cluster(self, res):
        lignes = ["SERVEURS :"]
        for n in [r for r in res if r["type"] == "node"]:
            lignes.append(f"- {n['node']} : {n.get('status')}, processeur {round(n.get('cpu', 0) * 100)} %, "
                          f"mémoire {_go(n.get('mem'))}/{_go(n.get('maxmem'))} Go, disque système {_go(n.get('disk'))}/{_go(n.get('maxdisk'))} Go, "
                          f"allumé depuis {round(n.get('uptime', 0) / 86400, 1)} j")
        lignes.append("VM ET CONTENEURS :")
        for v in sorted([r for r in res if r["type"] in ("qemu", "lxc")], key=lambda r: r["vmid"]):
            t = "VM" if v["type"] == "qemu" else "conteneur"
            prot = " [protégé]" if v["vmid"] in PROTEGES else ""
            lignes.append(f"- {v['vmid']} {v.get('name')} ({t}, sur {v.get('node')}) : {v.get('status')}, "
                          f"mémoire {_go(v.get('mem'))}/{_go(v.get('maxmem'))} Go, disque {_go(v.get('maxdisk'))} Go{prot}")
        lignes.append("STOCKAGE :")
        vus = set()
        for s in [r for r in res if r["type"] == "storage"]:
            cle = s["storage"] if s.get("shared") else f"{s['storage']}@{s['node']}"
            if cle in vus:
                continue
            vus.add(cle)
            lignes.append(f"- {cle} : {_go(s.get('disk'))}/{_go(s.get('maxdisk'))} Go utilisés")
        return "\n".join(lignes)

    async def details_machine(self, vmid: int) -> str:
        """
        Détails d'une VM ou d'un conteneur : configuration (processeur, mémoire, disques, réseau, adresse IP), état, notes.
        :param vmid: Numéro de la VM ou du conteneur (ex. 110).
        """
        v = await self._invite(vmid)
        if not v:
            return f"Aucune VM ni conteneur n° {vmid}."
        sorte = "qemu" if v["type"] == "qemu" else "lxc"
        conf = await self._a("GET", f"/nodes/{v['node']}/{sorte}/{vmid}/config")
        conf = {k: val for k, val in conf.items() if k not in ("digest", "sshkeys", "cipassword")}
        return f"{json.dumps(v, ensure_ascii=False)}\nConfiguration : {json.dumps(conf, ensure_ascii=False)}"

    async def sauvegardes(self) -> str:
        """
        Liste les sauvegardes Proxmox (date, taille, machine) et les tâches de sauvegarde programmées, avec le résultat des dernières.
        """
        lignes = ["TÂCHES PROGRAMMÉES :"]
        for j in await self._a("GET", "/cluster/backup"):
            lignes.append(f"- {j.get('id')} : {j.get('schedule')} vers {j.get('storage')}, "
                          f"{'toutes les machines' if j.get('all') else j.get('vmid')}, actif={j.get('enabled', 1)}")
        lignes.append("SAUVEGARDES PRÉSENTES (shared-hdd) :")
        try:
            contenus = await self._a("GET", "/nodes/pve/storage/shared-hdd/content?content=backup")
            for c in sorted(contenus, key=lambda c: c.get("ctime", 0))[-20:]:
                lignes.append(f"- {_h(c.get('ctime', 0))} machine {c.get('vmid')} "
                              f"{_go(c.get('size'))} Go ({c.get('notes', '')})")
        except Exception as e:  # noqa: BLE001
            lignes.append(f"(lecture impossible : {e})")
        lignes.append("DERNIÈRES TÂCHES DE SAUVEGARDE :")
        for n in ("pve", "pve2"):
            try:
                for t in (await self._a("GET", f"/nodes/{n}/tasks?typefilter=vzdump&limit=5")) or []:
                    lignes.append(f"- {n} {_h(t['starttime'])} : {t.get('status')}")
            except Exception:  # noqa: BLE001
                pass
        return "\n".join(lignes)

    async def taches_recentes(self, nombre: int = 15) -> str:
        """
        Dernières actions faites sur le cluster (démarrages, créations, sauvegardes, erreurs), sur les deux serveurs.
        :param nombre: Nombre de tâches à afficher (15 par défaut).
        """
        out = []
        for n in ("pve", "pve2"):
            try:
                for t in (await self._a("GET", f"/nodes/{n}/tasks?limit={int(nombre)}")) or []:
                    out.append((t["starttime"], f"{_h(t['starttime'])} {n} "
                                                f"{t.get('type')} {t.get('id', '')} par {t.get('user')} : {t.get('status', 'en cours')}"))
            except Exception:  # noqa: BLE001
                pass
        return "\n".join(l for _, l in sorted(out, reverse=True)[: int(nombre)]) or "Aucune tâche."

    # ---------- actions (avec confirmation) ----------
    async def demarrer(self, vmid: int, __event_call__=None, __event_emitter__=None) -> str:
        """
        Démarre une VM ou un conteneur (demande confirmation à l'utilisateur).
        :param vmid: Numéro de la machine.
        """
        return await self._etat(vmid, "start", "démarrer", __event_call__, __event_emitter__)

    async def arreter(self, vmid: int, forcer: bool = False, __event_call__=None, __event_emitter__=None) -> str:
        """
        Arrête proprement une VM ou un conteneur (comme appuyer sur le bouton d'arrêt). Demande confirmation.
        Les machines protégées (coffre de mots de passe, VPN, proxy) ne peuvent pas être arrêtées par l'agent.
        :param vmid: Numéro de la machine.
        :param forcer: true seulement si l'utilisateur demande explicitement un arrêt forcé (coupure brutale).
        """
        return await self._etat(vmid, "stop" if forcer else "shutdown", "arrêter de force" if forcer else "arrêter",
                                __event_call__, __event_emitter__)

    async def redemarrer(self, vmid: int, __event_call__=None, __event_emitter__=None) -> str:
        """
        Redémarre une VM ou un conteneur. Demande confirmation. Interdit sur les machines protégées.
        :param vmid: Numéro de la machine.
        """
        return await self._etat(vmid, "reboot", "redémarrer", __event_call__, __event_emitter__)

    async def _etat(self, vmid, action, verbe, __event_call__, emitter):
        v = await self._invite(vmid)
        if not v:
            return f"Aucune VM ni conteneur n° {vmid}."
        if action != "start" and int(vmid) in PROTEGES:
            return (f"Refusé : {vmid} est {PROTEGES[int(vmid)]}, une machine protégée. L'agent ne peut pas l'arrêter ni la "
                    "redémarrer ; l'utilisateur doit le faire lui-même dans l'interface Proxmox s'il le veut vraiment.")
        if not await self._confirmer(__event_call__, f"Proxmox : {verbe} {v.get('name')} ?",
                                     f"Machine {vmid} « {v.get('name')} » sur {v['node']} (actuellement : {v.get('status')})."):
            return "Action annulée : l'utilisateur n'a pas confirmé. Ne rien faire de plus."
        sorte = "qemu" if v["type"] == "qemu" else "lxc"
        await self._statut(emitter, f"🖥️ {verbe} {v.get('name')}…")
        upid = await self._a("POST", f"/nodes/{v['node']}/{sorte}/{vmid}/status/{action}")
        res = await self._attendre_tache(v["node"], upid, 300)
        await self._statut(emitter, f"🖥️ {verbe} {v.get('name')} : {res}", True)
        return f"Terminé : {verbe} {v.get('name')} → {res}."

    async def sauvegarder_maintenant(self, vmid: int, __event_call__=None, __event_emitter__=None) -> str:
        """
        Lance tout de suite une sauvegarde Proxmox d'une VM ou d'un conteneur vers shared-hdd (pve2). Demande confirmation.
        :param vmid: Numéro de la machine.
        """
        v = await self._invite(vmid)
        if not v:
            return f"Aucune VM ni conteneur n° {vmid}."
        if not await self._confirmer(__event_call__, f"Sauvegarder {v.get('name')} maintenant ?",
                                     f"Sauvegarde complète de la machine {vmid} vers shared-hdd (pve2). Quelques minutes."):
            return "Action annulée : l'utilisateur n'a pas confirmé."
        await self._statut(__event_emitter__, f"💾 Sauvegarde de {v.get('name')}…")
        upid = await self._a("POST", f"/nodes/{v['node']}/vzdump", {"vmid": vmid, "storage": "shared-hdd",
                                                                     "mode": "snapshot", "compress": "zstd"})
        res = await self._attendre_tache(v["node"], upid, 3600)
        await self._statut(__event_emitter__, f"💾 Sauvegarde de {v.get('name')} : {res}", True)
        return f"Sauvegarde de {v.get('name')} terminée : {res}."

    async def _modeles_locaux(self):
        """Modèles de conteneur déjà présents : {nom du fichier: volid}. shared-hdd (partagé) en priorité."""
        out = {}
        for st in ("local", "shared-hdd"):
            try:
                for c in await self._a("GET", f"/nodes/pve/storage/{st}/content?content=vztmpl"):
                    out.setdefault(c["volid"].split("/")[-1], (st, c["volid"]))
            except Exception:  # noqa: BLE001
                pass
        return out

    async def _modele(self, systeme, noeud, emitter):
        """volid du modèle pour ce système, téléchargé sur shared-hdd (visible des deux serveurs) s'il manque."""
        fichier = SYSTEMES[systeme]
        locaux = await self._modeles_locaux()
        if fichier in locaux:
            st, volid = locaux[fichier]
            if st == "shared-hdd" or noeud == "pve":
                return volid
        await self._statut(emitter, f"🖥️ Téléchargement du modèle {systeme}…")
        upid = await self._a("POST", "/nodes/pve/aplinfo", {"storage": "shared-hdd", "template": fichier})
        res = await self._attendre_tache("pve", upid, 900)
        if res != "OK":
            raise RuntimeError(f"téléchargement du modèle {systeme} : {res}")
        return f"shared-hdd:vztmpl/{fichier}"

    async def creer_conteneur(self, nom: str, memoire_mo: int = 1024, disque_go: int = 8, coeurs: int = 1,
                              docker: bool = False, systeme: str = "Debian 13",
                              __event_call__=None, __event_emitter__=None) -> str:
        """
        Crée un nouveau conteneur. Ouvre une FENÊTRE dans laquelle l'utilisateur vérifie et ajuste tout (nom, système,
        mémoire, disque, cœurs, serveur, Docker) avant de cliquer sur « Créer » : propose de bonnes valeurs selon l'usage
        demandé. Adresse IP fixe selon la règle IP = 192.168.0.(ID + 100) (ID 110 à 149), démarrage automatique et
        sauvegarde chaque nuit. Ensuite, l'installation des logiciels dedans se fait par Claude (SSH) ou par l'utilisateur.
        :param nom: Nom court en minuscules, lettres/chiffres/tirets (ex. « minecraft »).
        :param memoire_mo: Mémoire proposée en Mo (1024 par défaut).
        :param disque_go: Disque proposé en Go (8 par défaut).
        :param coeurs: Nombre de cœurs proposé (1 par défaut).
        :param docker: true si le conteneur doit pouvoir faire tourner Docker.
        :param systeme: « Debian 13 » (par défaut), « Debian 12 », « Ubuntu 24.04 », « Ubuntu 26.04 » ou « Alpine 3.24 ».
        """
        def propre(n):
            return "".join(c for c in str(n).lower() if c.isalnum() or c == "-").strip("-")[:30]

        def bornes(m, d, c):
            return max(256, min(int(m), 16384)), max(2, min(int(d), 200)), max(1, min(int(c), 8))

        nom = propre(nom) or "conteneur"
        systeme = systeme if systeme in SYSTEMES else "Debian 13"
        memoire_mo, disque_go, coeurs = bornes(memoire_mo, disque_go, coeurs)
        res = await self._a("GET", "/cluster/resources")
        existants = {int(v["vmid"]) for v in res if v["type"] in ("qemu", "lxc")}
        vmid = next((i for i in IDS if i not in existants), None)
        if vmid is None:
            return "Plus de numéro libre entre 110 et 149 (règle IP = ID + 100)."
        ip = f"192.168.0.{vmid + 100}"
        if await asyncio.to_thread(_ip_occupee, ip):
            return (f"L'adresse {ip} (prévue pour le n° {vmid}) est déjà utilisée par un autre appareil de la maison. "
                    "Rien n'a été créé : il faut vérifier la plage DHCP de la box (elle ne doit pas distribuer .200 à .249).")
        noeuds = []
        for n in sorted([r for r in res if r["type"] == "node" and r.get("status") == "online"], key=lambda r: r["node"]):
            if n["node"] in NOEUDS:
                noeuds.append([n["node"], f"{_go(n.get('maxmem', 0) - n.get('mem', 0)):.0f} Go de mémoire libre"])
        if not noeuds:
            return "Aucun serveur Proxmox en ligne."
        locaux = await self._modeles_locaux()
        choix = await _fenetre(__event_call__, FENETRE_CREER_JS, {
            "vmid": vmid, "ip": ip, "nom": nom, "systeme": systeme, "systemes": list(SYSTEMES),
            "locaux": [k for k, f in SYSTEMES.items() if f in locaux], "memoire_mo": memoire_mo, "disque_go": disque_go,
            "coeurs": coeurs, "noeuds": noeuds, "noeud": noeuds[0][0], "docker": bool(docker)})
        noeud = noeuds[0][0]
        if choix is None:  # fenêtre impossible à ouvrir : simple confirmation
            if not await self._confirmer(__event_call__, f"Créer le conteneur « {nom} » ?",
                                         f"Conteneur n° {vmid} sur {noeud} : {systeme}, {coeurs} cœur(s), {memoire_mo} Mo de mémoire, "
                                         f"{disque_go} Go de disque, adresse {ip}{', Docker possible' if docker else ''}. "
                                         "Démarrage automatique et sauvegarde chaque nuit."):
                return "Création annulée : l'utilisateur n'a pas confirmé."
        elif not choix.get("ok"):
            return "Création annulée par l'utilisateur dans la fenêtre. Ne rien faire de plus."
        else:  # tout ce qui vient du navigateur est revérifié
            nom = propre(choix.get("nom", nom)) or nom
            systeme = choix.get("systeme") if choix.get("systeme") in SYSTEMES else systeme
            try:
                memoire_mo, disque_go, coeurs = bornes(choix.get("memoire_mo", memoire_mo), choix.get("disque_go", disque_go),
                                                       choix.get("coeurs", coeurs))
            except (TypeError, ValueError):
                return "Valeurs invalides dans la fenêtre : rien n'a été créé."
            noeud = choix.get("noeud") if choix.get("noeud") in [n for n, _ in noeuds] else noeud
            docker = choix.get("docker") is True
            if vmid in {int(v["vmid"]) for v in await self._a("GET", "/cluster/resources?type=vm")}:
                return f"Le n° {vmid} vient d'être pris entre-temps : redemande la création."
        try:
            modele = await self._modele(systeme, noeud, __event_emitter__)
        except Exception as e:  # noqa: BLE001
            await self._statut(__event_emitter__, "🖥️ Modèle indisponible", True)
            return f"Rien n'a été créé : {e}."
        await self._statut(__event_emitter__, f"🖥️ Création du conteneur {nom} sur {noeud}…")
        donnees = {
            "vmid": vmid, "hostname": nom, "ostemplate": modele, "cores": coeurs, "memory": memoire_mo, "swap": 512,
            "rootfs": f"local-lvm:{disque_go}", "unprivileged": 1, "onboot": 1, "start": 1,
            "features": "nesting=1,keyctl=1" if docker else "nesting=1",
            "net0": f"name=eth0,bridge=vmbr0,ip={ip}/24,gw=192.168.0.1", "nameserver": "192.168.0.1",
            "timezone": "Europe/Paris", "tags": "agent", "ssh-public-keys": CLE_SSH_SPARK,
            "password": secrets.token_urlsafe(24),
            "description": f"Créé par l'agent Proxmox le {time.strftime('%d/%m/%Y')} ({systeme})",
        }
        if self.valves.pool.strip():
            # Sans ça, la clé limitée au pool ne pourrait plus gérer le conteneur qu'elle vient de créer
            donnees["pool"] = self.valves.pool.strip()
        upid = await self._a("POST", f"/nodes/{noeud}/lxc", donnees)
        res = await self._attendre_tache(noeud, upid, 600)
        await self._statut(__event_emitter__, f"🖥️ Conteneur {nom} : {res}", True)
        if res != "OK":
            return f"La création a échoué : {res}."
        return (f"Conteneur « {nom} » créé : n° {vmid} sur {noeud}, {systeme}, {coeurs} cœur(s), {memoire_mo} Mo, "
                f"{disque_go} Go, adresse {ip}, démarré. Accès root par la clé SSH du Spark (Claude peut y installer des "
                "logiciels). Il est inclus dans la sauvegarde Proxmox de chaque nuit.")

    async def supprimer(self, vmid: int, __event_call__=None, __event_emitter__=None) -> str:
        """
        Supprime DÉFINITIVEMENT une VM ou un conteneur et ses disques. À n'utiliser que si l'utilisateur le demande
        explicitement. Ouvre une fenêtre rouge où l'utilisateur doit TAPER LE NOM de la machine pour confirmer (et peut
        choisir de l'arrêter d'abord si elle est allumée). Interdit sur les machines protégées.
        :param vmid: Numéro de la machine.
        """
        v = await self._invite(vmid)
        if not v:
            return f"Aucune VM ni conteneur n° {vmid}."
        vmid = int(vmid)
        if vmid in PROTEGES:
            return f"Refusé : {vmid} est {PROTEGES[vmid]}, une machine protégée que l'agent ne peut pas supprimer."
        nom = str(v.get("name") or vmid)
        sorte = "qemu" if v["type"] == "qemu" else "lxc"
        # L'état de /cluster/resources a quelques secondes de retard après un arrêt : on lit l'état réel de la machine
        try:
            etat = (await self._a("GET", f"/nodes/{v['node']}/{sorte}/{vmid}/status/current") or {}).get("status")
        except Exception:  # noqa: BLE001
            etat = None
        allumee = (etat or v.get("status")) != "stopped"
        derniere = "aucune"
        try:
            sv = [c for c in await self._a("GET", "/nodes/pve/storage/shared-hdd/content?content=backup")
                  if int(c.get("vmid") or 0) == vmid]
            if sv:
                derniere = _h(max(c.get("ctime", 0) for c in sv))
        except Exception:  # noqa: BLE001
            derniere = "inconnue"
        choix = await _fenetre(__event_call__, FENETRE_SUPPRIMER_JS, {"nom": nom, "allumee": allumee, "lignes": [
            ["Machine", f"{vmid} · {nom}"], ["Type", "VM" if v["type"] == "qemu" else "conteneur"], ["Serveur", v.get("node")],
            ["État", "allumée" if allumee else "éteinte"], ["Mémoire", f"{_go(v.get('maxmem')):g} Go"],
            ["Disque", f"{_go(v.get('maxdisk')):g} Go"], ["Dernière sauvegarde", derniere]]})
        arreter = False
        if choix is None:  # fenêtre impossible à ouvrir : ancienne confirmation, machine éteinte obligatoire
            if allumee:
                return f"{nom} est encore allumée : il faut d'abord l'arrêter (outil arreter)."
            if not await self._confirmer(__event_call__, f"⚠️ SUPPRIMER DÉFINITIVEMENT {nom} ?",
                                         f"La machine {vmid} « {nom} » et tous ses disques seront effacés. "
                                         f"Dernière sauvegarde : {derniere}."):
                return "Suppression annulée : l'utilisateur n'a pas confirmé."
        elif not choix.get("ok") or choix.get("nom_tape") != nom:
            return "Suppression annulée par l'utilisateur. Ne rien faire de plus."
        else:
            arreter = choix.get("arreter") is True
            if allumee and not arreter:
                return "Suppression annulée : la machine est allumée et l'arrêt n'a pas été choisi."
        if allumee and arreter:
            await self._statut(__event_emitter__, f"🖥️ Arrêt de {nom}…")
            upid = await self._a("POST", f"/nodes/{v['node']}/{sorte}/{vmid}/status/stop")
            res = await self._attendre_tache(v["node"], upid, 300)
            if res != "OK":
                return f"L'arrêt de {nom} a échoué ({res}) : rien n'a été supprimé."
        await self._statut(__event_emitter__, f"🖥️ Suppression de {nom}…")
        upid = await self._a("DELETE", f"/nodes/{v['node']}/{sorte}/{vmid}?purge=1&destroy-unreferenced-disks=1")
        res = await self._attendre_tache(v["node"], upid, 600)
        await self._statut(__event_emitter__, f"🖥️ Suppression de {nom} : {res}", True)
        return f"Suppression de {nom} : {res}."
