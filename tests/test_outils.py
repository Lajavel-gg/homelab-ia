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
            return []
        if methode == "POST":
            envoye.update(donnees)
            return "UPID:x"
        return {"status": "stopped", "exitstatus": "OK"}

    async def oui(_):
        return True
    monkeypatch.setattr(t, "_a", api)
    monkeypatch.setattr(proxmox_tool, "_ip_occupee", lambda ip: False)
    asyncio.run(t.creer_conteneur("test", __event_call__=oui))
    assert envoye["pool"] == "agent"


def test_proxmox_refuse_les_machines_protegees(monkeypatch):
    import proxmox_tool
    t = proxmox_tool.Tools()

    async def api(methode, chemin, donnees=None):
        return [{"vmid": 110, "type": "lxc", "node": "pve", "name": "vaultwarden", "status": "running"}]
    monkeypatch.setattr(t, "_a", api)
    assert "Refusé" in asyncio.run(t.arreter(110))
    assert "Refusé" in asyncio.run(t.supprimer(110))


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
