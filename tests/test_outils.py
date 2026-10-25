"""Outils et filtres Open WebUI : garde-fous qui ne doivent jamais régresser."""
import asyncio
import inspect
import os

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_trading_refuse_sans_confirmation_possible(monkeypatch):
    import trading_tool
    appels = []
    monkeypatch.setattr(trading_tool, "_post", lambda *a: appels.append(a) or {})
    t = trading_tool.Tools()
    assert "Annulé" in asyncio.run(t.changer_reglages(pause=True, __event_call__=None))
    assert "Annulé" in asyncio.run(t.analyser_maintenant(__event_call__=None))

    async def refuse(_):
        return False
    assert "Annulé" in asyncio.run(t.changer_reglages(pause=True, __event_call__=refuse))
    assert appels == []


@pytest.mark.parametrize("texte, attendu", [
    ("une femme en bikini", True), ("sexy girl", True), ("She wears lingerie", True), ("femme nue", True),
    ("bra and panties", True), ("un chat qui dort", False), ("portrait in a park, blue dress", False),
    ("braid hair", False), ("bras amputé", False),
])
def test_detection_scene_suggestive(texte, attendu):
    import studio_tool
    assert studio_tool._suggestif(texte) is attendu


def test_correcteur_ne_bloque_pas_open_webui():
    import correcteur_memoire
    f = correcteur_memoire.Filter()
    # Open WebUI appelle un filtre « def » directement dans sa boucle : il doit être async
    assert inspect.iscoroutinefunction(f.inlet) and inspect.iscoroutinefunction(f.outlet)


def test_proxmox_range_dans_le_pool(monkeypatch):
    import proxmox_tool
    t = proxmox_tool.Tools()
    t.valves.pool = "agent"
    envoye = {}

    async def api(methode, chemin, donnees=None):
        if chemin.startswith("/cluster/resources"):
            return [{"type": "node", "node": "pve", "status": "online", "mem": 0, "maxmem": 2**34}]
        if chemin.endswith("content=vztmpl"):
            return []
        if methode == "POST":
            envoye[chemin] = donnees
            return "UPID:x"
        return {"status": "stopped", "exitstatus": "OK"}

    async def oui(_):
        return True
    monkeypatch.setattr(t, "_a", api)
    monkeypatch.setattr(proxmox_tool, "_ip_occupee", lambda ip: False)
    asyncio.run(t.creer_conteneur("test", __event_call__=oui))
    assert envoye["/nodes/pve/lxc"]["pool"] == "agent"


def test_proxmox_refuse_les_machines_protegees(monkeypatch):
    import proxmox_tool
    t = proxmox_tool.Tools()

    async def api(methode, chemin, donnees=None):
        return [{"vmid": 110, "type": "lxc", "node": "pve", "name": "vaultwarden", "status": "running"}]
    monkeypatch.setattr(t, "_a", api)
    assert "Refusé" in asyncio.run(t.arreter(110))
    assert "Refusé" in asyncio.run(t.supprimer(110))


def test_proxmox_supprimer_lit_l_etat_reel(monkeypatch):
    """Juste après un arrêt, /cluster/resources dit encore « running » : on se fie à status/current."""
    import proxmox_tool
    t = proxmox_tool.Tools()
    appels = []

    async def api(methode, chemin, donnees=None):
        appels.append((methode, chemin))
        if chemin.startswith("/cluster/resources"):
            return [{"vmid": 120, "type": "lxc", "node": "pve", "name": "essai", "status": "running"}]
        if chemin == "/nodes/pve/lxc/120/status/current":
            return {"status": "stopped"}
        if "content=backup" in chemin:
            return []
        if methode == "DELETE":
            return "UPID:x"
        return {"status": "stopped", "exitstatus": "OK"}

    async def oui(_):
        return True  # pas un dict : la fenêtre « ne s'ouvre pas », on retombe sur la confirmation simple
    monkeypatch.setattr(t, "_a", api)
    assert "OK" in asyncio.run(t.supprimer(120, __event_call__=oui))
    assert ("DELETE", "/nodes/pve/lxc/120?purge=1&destroy-unreferenced-disks=1") in appels
    assert not any(c.endswith("/status/stop") for _, c in appels)


def test_serveurs_mcp_figes():
    """Pas de « dernière version » téléchargée à chaque démarrage (la 2.x de mcp a cassé le serveur mail)."""
    import json
    conf = json.load(open(os.path.join(RACINE, "mail", "config.json")))
    for nom, s in conf["mcpServers"].items():
        cmd, args = s["command"], s.get("args", [])
        if cmd == "uvx":
            paquets = [args[0]]
        elif cmd == "npx":
            paquets = [a for a in args if not a.startswith("-")][:1]
        elif cmd == "uv":
            paquets = [args[i + 1] for i, a in enumerate(args) if a == "--with"]
        else:
            continue  # programme installé dans l'image (version figée dans le Dockerfile)
        for p in paquets:
            assert "==" in p or "@" in p.lstrip("@"), f"{nom} : version non figée ({p})"


def test_lecteur_du_chat_gere_les_creations_archivees():
    import studio_tool
    for fmt in ("image", "video", "glb"):
        page = studio_tool._embed({"format": fmt, "fichier": "/studio/media/" + "a" * 32 + ".mp4"})
        assert "addEventListener('error',absent)" in page and "archivée sur pve2" in page
        assert 'id="dl"' in page and "Toutes mes créations" in page


def test_image_refusee_supprimee_du_studio(monkeypatch):
    """Personne qui paraît mineure dans une scène suggestive : rien n'est affiché ET la création est effacée du Studio."""
    import studio_tool
    ids = iter(["a1a1a1a1a1", "b2b2b2b2b2"])
    supprimes, publies = [], []
    monkeypatch.setattr(studio_tool, "_image_job", lambda *a: {"comfy": "x.png", "id": next(ids)})
    monkeypatch.setattr(studio_tool, "_comfy_file", lambda rel: b"png")
    monkeypatch.setattr(studio_tool, "_verifier", lambda *a: {"ok": False, "trop_jeune": True, "corrections": ["fix"]})
    monkeypatch.setattr(studio_tool, "_supprimer_job", lambda jid: supprimes.append(jid) or {"ok": True})

    async def publier(*a):
        publies.append(a)
    monkeypatch.setattr(studio_tool, "_publier_image", publier)
    prompt = "A young woman in a bikini on the beach, " + "sunny day with blue sky and waves, " * 3
    r = asyncio.run(studio_tool.Tools().creer_image(prompt, __messages__=[{"role": "user", "content": "une fille en bikini"}]))
    assert r.startswith("REFUSÉ") and publies == []
    assert supprimes == ["a1a1a1a1a1", "b2b2b2b2b2"]


def test_supprimer_job_appelle_le_runner(monkeypatch):
    import studio_tool
    vu = {}

    class Rep:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return b'{"ok": true}'

    def ouvrir(req, timeout=None):
        vu.update(methode=req.get_method(), url=req.full_url)
        return Rep()
    monkeypatch.setattr(studio_tool.urllib.request, "urlopen", ouvrir)
    assert studio_tool._supprimer_job("a1a1a1a1a1") == {"ok": True}
    assert vu == {"methode": "DELETE", "url": "http://127.0.0.1:8190/studio/api/jobs/a1a1a1a1a1"}
