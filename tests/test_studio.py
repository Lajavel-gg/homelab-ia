"""Studio (studio/runner.py) : supprimer, garder, ménage automatique, page sans injection HTML."""
import ast
import http.client
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

import runner

JOUR = 86400


@pytest.fixture
def studio(tmp_path, monkeypatch):
    media, sortie = tmp_path / "media", tmp_path / "output"
    (sortie / "studio" / "long_x").mkdir(parents=True)
    media.mkdir()
    monkeypatch.setattr(runner, "MEDIA", str(media))
    monkeypatch.setattr(runner, "COMFY_OUT", str(sortie))
    monkeypatch.setattr(runner, "JOBS_FILE", str(tmp_path / "jobs.json"))
    runner.jobs.clear()
    yield media, sortie
    runner.jobs.clear()


def creation(media, jid, age_jours, statut="terminé", garder=False):
    nom = jid.ljust(32, "0") + ".mp4"
    (media / nom).write_bytes(b"video")
    vieux = time.time() - age_jours * JOUR
    os.utime(media / nom, (vieux, vieux))
    runner.jobs[jid] = {"type": "video", "statut": statut, "cree": vieux, "fichier": "/studio/media/" + nom, "garder": garder}
    return media / nom


def test_supprimer(studio):
    media, _ = studio
    f = creation(media, "aaaaaaaaaa", 1)
    assert runner.supprimer_job("aaaaaaaaaa") == "ok"
    assert not f.exists() and "aaaaaaaaaa" not in runner.jobs
    assert runner.supprimer_job("aaaaaaaaaa") == "inconnu"


def test_on_ne_supprime_pas_une_creation_en_cours(studio):
    media, _ = studio
    f = creation(media, "bbbbbbbbbb", 1, statut="en cours")
    assert runner.supprimer_job("bbbbbbbbbb") == "en cours"
    assert f.exists()


def test_fichier_hors_media_jamais_touche(studio, tmp_path):
    media, _ = studio
    precieux = tmp_path / "precieux.txt"
    precieux.write_text("x")
    runner.jobs["cccccccccc"] = {"statut": "terminé", "cree": 0, "fichier": "/studio/media/../../precieux.txt"}
    runner.supprimer_job("cccccccccc")
    assert precieux.exists()


def test_menage(studio):
    media, sortie = studio
    vieux = creation(media, "1111111111", 90)
    garde = creation(media, "2222222222", 90, garder=True)
    recent = creation(media, "3333333333", 5)
    orphelin = media / ("f" * 32 + ".png")
    orphelin.write_bytes(b"x")
    os.utime(orphelin, (0, 0))
    (media / runner.VIEWER).write_bytes(b"js")
    os.utime(media / runner.VIEWER, (0, 0))
    brut = sortie / "studio" / "long_x" / "clip.mp4"
    brut.write_bytes(b"x")
    os.utime(brut, (0, 0))
    autre = sortie / "a_moi.png"  # hors du dossier du Studio : jamais touché
    autre.write_bytes(b"x")
    os.utime(autre, (0, 0))

    n_jobs, n_fichiers = runner.nettoyer(jours=60)
    assert n_jobs == 1 and not vieux.exists()
    assert garde.exists() and recent.exists() and (media / runner.VIEWER).exists()
    assert not orphelin.exists() and not brut.exists() and not (sortie / "studio" / "long_x").exists()
    assert autre.exists() and (sortie / "studio").exists()
    assert runner.nettoyer(jours=0) == (0, 0)


@pytest.fixture
def api(studio):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), runner.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def appel(methode, chemin, corps=None, entetes=None):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request(methode, chemin, body=json.dumps(corps) if corps is not None else None, headers=entetes or {})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    yield appel
    srv.shutdown()


def test_api(api, studio):
    media, _ = studio
    creation(media, "dddddddddd", 1)
    assert api("POST", "/studio/api/jobs/dddddddddd/garder", {"garder": True}) == (200, {"ok": True, "garder": True})
    assert runner.jobs["dddddddddd"]["garder"] is True
    assert api("DELETE", "/studio/api/jobs/dddddddddd")[0] == 200
    assert api("DELETE", "/studio/api/jobs/dddddddddd")[0] == 404
    assert api("DELETE", "/studio/api/jobs/../../etc")[0] == 404
    assert api("GET", "/studio/api/place")[0] == 200
    assert api("POST", "/studio/api/jobs", entetes={"content-length": str(500 * 2**20)})[0] == 413


def page_js():
    page = next(n for n in ast.parse(open(runner.__file__).read()).body
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PAGE").value.value
    return page.split("<script>\n", 1)[1].split("</script>")[0]


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_page_sans_injection_html(tmp_path):
    (tmp_path / "page.js").write_text(page_js())
    (tmp_path / "t.js").write_text("""
const fs=require('fs');
eval(fs.readFileSync(process.argv[2],'utf8')+';globalThis.T={rendre,statsHtml,filtresHtml,sousTitre,regler:v=>{infos=v}}');
const I={archive:true,garder_jours:60,octets:2**30};T.regler(I);
const jobs={
 'aaaaaaaaaa':{type:'video',statut:'terminé',prompt:'<img src=x onerror=alert(1)>',etape:'"><script>x</script>',cree:1,format:'video',fichier:'/studio/media/0123456789abcdef0123456789abcdef.mp4'},
 'bbbbbbbbbb':{type:'image',statut:'terminé',fichier:'javascript:alert(1)',format:'image',cree:2},
 'cccccccccc':{type:'3d',statut:'archivé',prompt:'coffre',cree:3,archive:'2026/08/',archive_fichiers:['<b>x</b>.glb']},
 'dddddddddd':{type:'image',statut:'échec',etape:'<svg onload=alert(1)>',cree:4},
 "x');alert(1);('":{type:'image',statut:'terminé',cree:5}};
console.log(JSON.stringify({tout:T.rendre(jobs,'tout'),archives:T.rendre(jobs,'archive'),echecs:T.rendre(jobs,'echec'),
  stats:T.statsHtml(jobs,I),filtres:T.filtresHtml(jobs),sous:T.sousTitre(I)}));""")
    out = json.loads(subprocess.run(["node", tmp_path / "t.js", tmp_path / "page.js"], capture_output=True, text=True,
                                    check=True).stdout)
    tout = out["tout"] + out["archives"] + out["echecs"]
    for piege in ("<img src=x", "<script>", "javascript:", "<svg", "<b>x</b>", "alert(1);("):
        assert piege not in tout, piege
    assert "0123456789abcdef0123456789abcdef.mp4" in out["tout"] and "Supprimer" in out["tout"]
    assert "Archiver" in out["tout"] and "coffre" not in out["tout"]  # les archivées ont leur filtre
    assert "coffre" in out["archives"] and "2026/08/" in out["archives"]
    assert "1,0 Go" in out["stats"] and "pve2" in out["sous"]


def test_page_chemins_absolus():
    # en relatif, la page ouverte sur « /studio » (sans / final) cherchait /api/jobs et restait vide
    js = page_js()
    assert "fetch('api/" not in js and "'api/" not in js
    assert "fetch('/studio/api/jobs')" in js and "fetch('/studio/api/place')" in js and "'/studio/api/jobs/'+id" in js


# ------------------------------------------------------------------ archivage sur pve2

def test_archiver_sur_pve2(studio, tmp_path, monkeypatch):
    if not shutil.which("rsync"):
        pytest.skip("rsync absent")
    media, _ = studio
    pve2 = tmp_path / "pve2"
    monkeypatch.setattr(runner, "ARCHIVE", str(pve2) + "/")  # destination locale : même rsync, sans SSH
    f = creation(media, "eeeeeeeeee", 90)
    runner.jobs["eeeeeeeeee"]["prompt"] = "un phare au coucher du soleil"
    assert runner.archiver_job("eeeeeeeeee") == "ok"
    mois = time.strftime("%Y/%m", time.localtime(runner.jobs["eeeeeeeeee"]["cree"]))
    envoyes = sorted(p.name for p in (pve2 / mois).iterdir())
    assert [n for n in envoyes if n.endswith(".mp4")] and [n for n in envoyes if n.endswith(".json")]
    fiche = json.loads(next((pve2 / mois).glob("*.json")).read_text())
    assert fiche["prompt"] == "un phare au coucher du soleil"
    assert not f.exists()  # retiré du Spark seulement après l'envoi
    assert runner.jobs["eeeeeeeeee"]["statut"] == "archivé" and "fichier" not in runner.jobs["eeeeeeeeee"]


def test_archivage_rate_rien_n_est_supprime(studio, monkeypatch):
    if not shutil.which("rsync"):
        pytest.skip("rsync absent")
    media, _ = studio
    monkeypatch.setattr(runner, "ARCHIVE", "/proc/impossible/")
    f = creation(media, "ffffffffff", 90)
    assert runner.archiver_job("ffffffffff").startswith("envoi vers pve2 impossible")
    assert f.exists() and runner.jobs["ffffffffff"]["statut"] == "terminé"
    assert runner.nettoyer(jours=60) == (0, 0)  # le ménage réessaiera plus tard, sans rien perdre
    assert f.exists()


def test_menage_avec_archivage(studio, tmp_path, monkeypatch):
    if not shutil.which("rsync"):
        pytest.skip("rsync absent")
    media, _ = studio
    monkeypatch.setattr(runner, "ARCHIVE", str(tmp_path / "pve2") + "/")
    creation(media, "1111111111", 90)
    creation(media, "2222222222", 90, statut="échec")
    creation(media, "3333333333", 90, garder=True)
    assert runner.nettoyer(jours=60)[0] == 2
    assert runner.jobs["1111111111"]["statut"] == "archivé"  # terminée → archivée
    assert "2222222222" not in runner.jobs  # échec → rien à garder
    assert runner.jobs["3333333333"]["statut"] == "terminé"  # gardée → reste sur le Spark
    assert runner.nettoyer(jours=60)[0] == 0  # une archivée n'est pas archivée deux fois


def test_api_archiver_sans_configuration(api, studio, monkeypatch):
    media, _ = studio
    monkeypatch.setattr(runner, "ARCHIVE", "")
    creation(media, "abcdefabcd", 1)
    assert api("POST", "/studio/api/jobs/abcdefabcd/archiver")[0] == 501
