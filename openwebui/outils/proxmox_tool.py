"""
title: Proxmox
description: Gère le cluster Proxmox de la maison (pve + pve2) : état, VM/conteneurs, sauvegardes, création de conteneurs. Toute action qui change quelque chose demande d'abord la confirmation de l'utilisateur.
version: 1.0
"""

# Outil Open WebUI créé le 19/10/2026. Clé API spark@pve!spark (rôle PVEAdmin) dans les valves (chiffrées).
# Règles : lecture libre ; démarrer/arrêter/créer/sauvegarder = fenêtre de confirmation ;
# les conteneurs d'infrastructure (coffre, VPN, proxy) ne peuvent pas être arrêtés ni supprimés par l'agent.

import asyncio
import json
import secrets
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

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


class Tools:
    class Valves(BaseModel):
        hote: str = Field("192.168.0.200", description="Adresse d'un nœud du cluster")
        token_id: str = Field("", description="ex. spark@pve!spark")
        token_secret: str = Field("", description="Secret de la clé API")
        ca_pem: str = Field("", description="Certificat racine du cluster (pve-root-ca.pem)")

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
        Donne l'état complet du cluster Proxmox : serveurs (processeur, mémoire, disques), toutes les VM et conteneurs
        avec leur état, et le stockage. À utiliser pour « comment vont mes serveurs », « qu'est-ce qui tourne », etc.
        """
        try:
            res = await self._a("GET", "/cluster/resources")
        except Exception as e:  # noqa: BLE001
            return f"Impossible de joindre Proxmox : {e}"
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

    async def creer_conteneur(self, nom: str, memoire_mo: int = 1024, disque_go: int = 8, coeurs: int = 1,
                              docker: bool = False, __event_call__=None, __event_emitter__=None) -> str:
        """
        Crée un nouveau conteneur Debian 13 sur pve (le gros serveur) avec une adresse IP fixe qui suit la règle IP = 192.168.0.(ID + 100) (ID 110 à 149),
        démarré automatiquement avec le serveur et inclus dans la sauvegarde de chaque nuit. Demande confirmation.
        Ensuite, l'installation des logiciels dedans se fait par Claude (SSH) ou par l'utilisateur.
        :param nom: Nom court en minuscules, lettres/chiffres/tirets (ex. « minecraft »).
        :param memoire_mo: Mémoire en Mo (1024 par défaut).
        :param disque_go: Taille du disque en Go (8 par défaut).
        :param coeurs: Nombre de cœurs (1 par défaut).
        :param docker: true si le conteneur doit pouvoir faire tourner Docker.
        """
        nom = "".join(c for c in nom.lower() if c.isalnum() or c == "-").strip("-")[:30]
        if not nom:
            return "Nom invalide : utilise des lettres, chiffres et tirets."
        memoire_mo, disque_go, coeurs = max(256, min(int(memoire_mo), 65536)), max(2, min(int(disque_go), 500)), max(1, min(int(coeurs), 16))
        existants = {int(v["vmid"]) for v in await self._a("GET", "/cluster/resources?type=vm")}
        vmid = next((i for i in IDS if i not in existants), None)
        if vmid is None:
            return "Plus de numéro libre entre 110 et 149 (règle IP = ID + 100)."
        ip = f"192.168.0.{vmid + 100}"
        if await asyncio.to_thread(_ip_occupee, ip):
            return (f"L'adresse {ip} (prévue pour le n° {vmid}) est déjà utilisée par un autre appareil de la maison. "
                    "Rien n'a été créé : il faut vérifier la plage DHCP de la box (elle ne doit pas distribuer .200 à .249).")
        if not await self._confirmer(__event_call__, f"Créer le conteneur « {nom} » ?",
                                     f"Conteneur n° {vmid} sur pve : Debian 13, {coeurs} cœur(s), {memoire_mo} Mo de mémoire, "
                                     f"{disque_go} Go de disque, adresse {ip}{', Docker possible' if docker else ''}. "
                                     "Démarrage automatique et sauvegarde chaque nuit."):
            return "Création annulée : l'utilisateur n'a pas confirmé."
        await self._statut(__event_emitter__, f"🖥️ Création du conteneur {nom}…")
        donnees = {
            "vmid": vmid, "hostname": nom, "ostemplate": MODELE_CT, "cores": coeurs, "memory": memoire_mo, "swap": 512,
            "rootfs": f"local-lvm:{disque_go}", "unprivileged": 1, "onboot": 1, "start": 1,
            "features": "nesting=1,keyctl=1" if docker else "nesting=1",
            "net0": f"name=eth0,bridge=vmbr0,ip={ip}/24,gw=192.168.0.1", "nameserver": "192.168.0.1",
            "timezone": "Europe/Paris", "tags": "agent", "ssh-public-keys": CLE_SSH_SPARK,
            "password": secrets.token_urlsafe(24),
            "description": f"Créé par l'agent Proxmox le {time.strftime('%d/%m/%Y')}",
        }
        upid = await self._a("POST", "/nodes/pve/lxc", donnees)
        res = await self._attendre_tache("pve", upid, 600)
        await self._statut(__event_emitter__, f"🖥️ Conteneur {nom} : {res}", True)
        if res != "OK":
            return f"La création a échoué : {res}."
        return (f"Conteneur « {nom} » créé : n° {vmid}, adresse {ip}, démarré. Accès root par la clé SSH du Spark "
                "(Claude peut y installer des logiciels). Il est inclus dans la sauvegarde Proxmox de chaque nuit.")

    async def supprimer(self, vmid: int, __event_call__=None, __event_emitter__=None) -> str:
        """
        Supprime DÉFINITIVEMENT une VM ou un conteneur et ses disques. À n'utiliser que si l'utilisateur le demande
        explicitement. La machine doit être arrêtée. Demande confirmation. Interdit sur les machines protégées.
        :param vmid: Numéro de la machine.
        """
        v = await self._invite(vmid)
        if not v:
            return f"Aucune VM ni conteneur n° {vmid}."
        if int(vmid) in PROTEGES:
            return f"Refusé : {vmid} est {PROTEGES[int(vmid)]}, une machine protégée que l'agent ne peut pas supprimer."
        if v.get("status") != "stopped":
            return f"{v.get('name')} est encore allumée : il faut d'abord l'arrêter (outil arreter)."
        if not await self._confirmer(__event_call__, f"⚠️ SUPPRIMER DÉFINITIVEMENT {v.get('name')} ?",
                                     f"La machine {vmid} « {v.get('name')} » et tous ses disques seront effacés. "
                                     "Seules les sauvegardes déjà faites sur pve2 permettraient de la récupérer."):
            return "Suppression annulée : l'utilisateur n'a pas confirmé."
        sorte = "qemu" if v["type"] == "qemu" else "lxc"
        upid = await self._a("DELETE", f"/nodes/{v['node']}/{sorte}/{vmid}?purge=1&destroy-unreferenced-disks=1")
        res = await self._attendre_tache(v["node"], upid, 600)
        return f"Suppression de {v.get('name')} : {res}."
