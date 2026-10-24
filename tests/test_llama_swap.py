"""llama-swap : configuration du dépôt lancée avec le vrai binaire et un faux llama-server, garde-memoire,
et le Studio qui met le chat en pause pendant une vidéo puis le relance."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
import yaml

import runner

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GARDE = os.path.join(RACINE, "spark", "scripts", "garde-memoire")
BINAIRE = os.environ.get("LLAMA_SWAP_BIN") or shutil.which("llama-swap")


def test_garde_memoire_refuse_pendant_une_pause(tmp_path):
    pause = tmp_path / "pause"
    env = {**os.environ, "STUDIO_PAUSE": str(pause), "WAIT_MEM": "true"}
    ok = subprocess.run([GARDE, "80", "echo", "lancé"], env=env, capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.strip() == "lancé"
    pause.write_text("Studio")
    refus = subprocess.run([GARDE, "80", "echo", "lancé"], env=env, capture_output=True, text=True)
    assert refus.returncode == 75 and "pause" in refus.stderr and "lancé" not in refus.stdout
    manque = subprocess.run([GARDE, "80", "echo", "lancé"], env={**env, "WAIT_MEM": "false", "STUDIO_PAUSE": "/nulle-part"},
                            capture_output=True, text=True)
    assert manque.returncode == 75  # pas assez de mémoire : on ne lance pas


def port_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def appel(url, corps=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(corps).encode() if corps is not None else None,
                                 headers={"content-type": "application/json"}, method="POST" if corps is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        brut = r.read()
    try:
        return json.loads(brut)
    except ValueError:
        return brut.decode()


@pytest.fixture
def llama_swap(tmp_path):
    if not BINAIRE:
        pytest.skip("binaire llama-swap absent (installé par l'action GitHub)")
    conf = yaml.safe_load(open(os.path.join(RACINE, "spark", "llama-swap", "config.yaml")))
    conf["macros"] = {"llama": f"{sys.executable} {os.path.join(RACINE, 'tests', 'faux_llama_server.py')}", "garde": GARDE}
    conf["startPort"] = port_libre() + 1000
    conf["healthCheckTimeout"] = 30
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(conf, allow_unicode=True))
    journal, pause, port = tmp_path / "journal", tmp_path / "pause", port_libre()
    env = {**os.environ, "FAUX_JOURNAL": str(journal), "STUDIO_PAUSE": str(pause), "WAIT_MEM": "true"}
    p = subprocess.Popen([BINAIRE, "-config", str(tmp_path / "config.yaml"), "-listen", f"127.0.0.1:{port}"], env=env,
                         stdout=open(tmp_path / "log", "w"), stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            appel(url + "/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    yield url, journal, pause
    p.terminate()
    p.wait(10)


def arguments(journal, nom_fichier):
    return next(json.loads(l) for l in journal.read_text().splitlines() if nom_fichier in l)


def test_config_du_depot_avec_le_vrai_llama_swap(llama_swap):
    url, journal, _ = llama_swap
    # « verif » (vérificateur d'images du Studio) est un alias de Flash-Next
    rep = appel(url + "/v1/chat/completions", {"model": "verif", "messages": [{"role": "user", "content": "x"}]})
    assert "Flash-Next" in rep["choices"][0]["message"]["content"]
    args = arguments(journal, "Flash-Next")
    assert '{"max_tool_response_chars":30000}' in args  # l'argument entre apostrophes arrive entier
    assert "--sleep-idle-seconds" not in args  # remplacé par le ttl de llama-swap
    # embeddings et petit modèle de tâches
    emb = appel(url + "/v1/embeddings", {"model": "bge-m3", "input": "bonjour"})
    assert emb["data"][0]["embedding"]
    assert {"--embeddings", "cls"} <= set(arguments(journal, "bge-m3"))
    appel(url + "/v1/chat/completions", {"model": "tache", "messages": [{"role": "user", "content": "titre"}]})
    assert '{"enable_thinking":false}' in arguments(journal, "Qwen3.5-2B")
    # les trois tournent ensemble (groupe « chat »), bge-m3 n'apparaît pas dans la liste du chat
    assert {m["model"] for m in appel(url + "/running")["running"]} == {"qwen3.8-flash-next", "bge-m3", "tache"}
    ids = {m["id"] for m in appel(url + "/v1/models")["data"]}
    assert "qwen3.8-flash-next" in ids and "tache" in ids and "bge-m3" not in ids


def test_le_studio_met_le_chat_en_pause_puis_le_relance(llama_swap, tmp_path, monkeypatch):
    url, journal, pause = llama_swap
    monkeypatch.setattr(runner, "LLAMA", url)
    monkeypatch.setattr(runner, "LLAMA_MODE", "swap")
    monkeypatch.setattr(runner, "PAUSE", str(pause))
    monkeypatch.setattr(runner, "JOBS_FILE", str(tmp_path / "jobs.json"))
    runner.jobs.clear()
    runner.jobs["0123456789"] = {"type": "video", "statut": "en attente", "cree": time.time()}
    appel(url + "/v1/chat/completions", {"model": "qwen3.8-flash-next", "messages": [{"role": "user", "content": "x"}]})
    assert runner.llama_charge() and not runner.llama_busy()

    runner.stop_llama("0123456789")  # avant une vidéo
    assert pause.exists() and runner.chat_en_pause()
    assert appel(url + "/running")["running"] == []  # toute la mémoire est rendue à ComfyUI
    with pytest.raises(urllib.error.HTTPError):  # un message pendant la vidéo ne recharge pas le modèle
        appel(url + "/v1/chat/completions", {"model": "qwen3.8-flash-next", "messages": [{"role": "user", "content": "x"}]})
    assert appel(url + "/running")["running"] == []

    assert runner.start_llama() is True  # après la vidéo
    assert not pause.exists() and not runner.chat_en_pause()
    assert runner.llama_charge()
    runner.jobs.clear()
