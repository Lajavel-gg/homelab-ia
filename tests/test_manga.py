"""Agent Manga : lecture des fichiers, carnet par série, chapitre complet avec un faux manga-image-translator,
page refaite avec une remarque, liseuse, suppression / archivage, outil Open WebUI."""
import asyncio
import base64
import io
import json
import os
import shutil
import sys
import threading
import time
import types
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import manga
import runner


def png(couleur=0):
    """Petite image PNG valide (sans Pillow)."""
    import struct
    import zlib
    brut = b"".join(b"\x00" + bytes([couleur, 50, 100]) * 4 for _ in range(4))

    def bloc(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + bloc(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(brut)) + bloc(b"IEND", b""))


def cbz_de(noms):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in noms:
            z.writestr(n, png() if n.lower().endswith(".png") else b"texte")
    return b.getvalue()


@pytest.fixture
def dossiers(tmp_path, monkeypatch):
    monkeypatch.setattr(manga, "SERIES", str(tmp_path / "series"))
    monkeypatch.setattr(manga, "TRAVAIL", str(tmp_path / "travail"))
    monkeypatch.setattr(runner, "MEDIA", str(tmp_path / "media"))
    monkeypatch.setattr(runner, "COMFY_OUT", str(tmp_path / "out"))
    monkeypatch.setattr(runner, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(runner, "comfy_free", lambda: None)
    os.makedirs(tmp_path / "media")
    runner.jobs.clear()
    yield tmp_path
    runner.jobs.clear()


# ------------------------------------------------------------------ fichiers reçus

def test_archive_dans_le_bon_ordre_sans_fichiers_parasites(tmp_path):
    archive = cbz_de(["chap/page10.png", "chap/page2.png", "chap/page1.png", "__MACOSX/chap/._page1.png",
                      "chap/.cache.png", "chap/notes.txt", "../../evil.png"])
    pages = manga.pages_depuis_fichiers([("chapitre.cbz", archive)], str(tmp_path / "p"))
    assert [os.path.basename(p) for p in pages] == ["0001.png", "0002.png", "0003.png", "0004.png"]
    assert not (tmp_path / "evil.png").exists() and not (tmp_path.parent / "evil.png").exists()  # rien écrit hors du dossier


def test_images_et_archives_melangees(tmp_path):
    pages = manga.pages_depuis_fichiers([("a.jpg", png()), ("b.zip", cbz_de(["1.png", "2.png"])), ("lisez-moi.txt", b"x")],
                                        str(tmp_path / "p"))
    assert [os.path.basename(p) for p in pages] == ["0001.jpg", "0002.png", "0003.png"]


def test_rien_a_traduire(tmp_path):
    with pytest.raises(ValueError, match="aucune page"):
        manga.pages_depuis_fichiers([("notes.txt", b"x")], str(tmp_path / "p"))


def test_trop_de_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(manga, "MAX_PAGES", 3)
    with pytest.raises(ValueError, match="chapitre par chapitre"):
        manga.pages_depuis_fichiers([("x.cbz", cbz_de([f"{i}.png" for i in range(5)]))], str(tmp_path / "p"))


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="pdftoppm absent")
def test_pdf(tmp_path):
    pdf = (b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R]/Count 2>>endobj "
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 140]>>endobj 4 0 obj<</Type/Page/Parent 2 0 R"
           b"/MediaBox[0 0 100 140]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
    assert len(manga.pages_depuis_fichiers([("chap.pdf", pdf)], str(tmp_path / "p"))) == 2


# ------------------------------------------------------------------ carnet

def test_carnet(dossiers):
    manga.noter("One Piece", "ルフィ", "Luffy", "personnage principal")
    manga.noter("One Piece", "ゴムゴムの", "Gomu Gomu no")
    manga.noter("One Piece", "ルフィ", "Luffy", "capitaine")  # remplace, ne duplique pas
    c = manga.carnet("one piece")  # même série, autre écriture
    assert [e["original"] for e in c["entrees"]] == ["ゴムゴムの", "ルフィ"] and c["entrees"][1]["note"] == "capitaine"
    assert manga.lister_series() == [{"serie": "One Piece", "entrees": 2}]
    assert manga.oublier("One Piece", "ゴムゴムの") and not manga.oublier("One Piece", "absent")
    assert manga.slug("Ôtomari {x} / Shōnen!") == "otomari-x-shonen"


def test_consignes_avec_carnet_remarque_et_accolades(dossiers):
    manga.noter("Série {test}", "先生", "Sensei", "toujours {Sensei}")
    chemin = manga.consignes("Série {test}", remarque="bulle 2 : c'est {ironique}")
    texte = json.load(open(chemin))["chat_system_template"]
    rendu = texte.format(to_lang="French")  # ce que fait manga-image-translator : ne doit pas planter
    assert "先生 → Sensei (toujours {Sensei})" in rendu and "c'est {ironique}" in rendu
    assert "<|number|>" in rendu and "Translate the following text into French" in rendu


# ------------------------------------------------------------------ faux manga-image-translator

class FauxMIT:
    def __init__(self, en_erreur=()):
        self.recus, self.en_erreur = [], set(en_erreur)
        recus, en_erreur = self.recus, self.en_erreur

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                corps = self.rfile.read(int(self.headers["content-length"]))
                conf = json.loads(corps.split(b'name="config"\r\n\r\n', 1)[1].split(b"\r\n--", 1)[0])
                consignes = json.load(open(conf["translator"]["gpt_config"]))["chat_system_template"]
                recus.append({"conf": conf, "consignes": consignes})
                if len(recus) in en_erreur:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b'{"detail":"OCR"}')
                    return
                d = png(len(recus))
                self.send_response(200)
                self.send_header("content-length", str(len(d)))
                self.end_headers()
                self.wfile.write(d)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


def chapitre(jid, n=3, serie="Ma série", langue="ja"):
    runner.jobs[jid] = {"type": "manga", "statut": "en cours", "cree": time.time(), "serie": serie, "langue": langue,
                        "fichiers_b64": [{"nom": "chap.cbz", "b64": base64.b64encode(cbz_de([f"p{i}.png" for i in range(1, n + 1)])).decode()}]}
    return runner.process(jid)


def test_chapitre_complet(dossiers, monkeypatch):
    mit = FauxMIT()
    monkeypatch.setattr(manga, "MANGA_URL", mit.url)
    manga.noter("Ma série", "先輩", "Senpai")
    res = chapitre("aaaaaaaaaa")
    assert res["format"] == "cbz" and len(res["pages"]) == 3 and res["apercu"] == res["pages"][0]
    assert res["pages_non_traduites"] == []
    conf = mit.recus[0]["conf"]
    assert conf["translator"]["translator"] == "custom_openai" and conf["translator"]["target_lang"] == "FRA"
    assert conf["render"]["direction"] == "horizontal" and "先輩 → Senpai" in mit.recus[0]["consignes"]
    assert not os.path.exists(conf["translator"]["gpt_config"])  # consignes temporaires supprimées
    with zipfile.ZipFile(os.path.join(runner.MEDIA, res["fichier"].rsplit("/", 1)[1])) as z:
        assert z.namelist() == ["001.png", "002.png", "003.png"]
    assert "fichiers_b64" not in runner.jobs["aaaaaaaaaa"]
    runner.save()
    assert "b64" not in open(runner.JOBS_FILE).read()  # jamais les fichiers dans jobs.json


def test_une_page_en_erreur_reste_en_version_originale(dossiers, monkeypatch):
    mit = FauxMIT(en_erreur=[2])
    monkeypatch.setattr(manga, "MANGA_URL", mit.url)
    res = chapitre("bbbbbbbbbb")
    assert res["pages_non_traduites"] == [2] and len(res["pages"]) == 3


def test_aucune_page_traduite(dossiers, monkeypatch):
    mit = FauxMIT(en_erreur=[1, 2])
    monkeypatch.setattr(manga, "MANGA_URL", mit.url)
    with pytest.raises(RuntimeError, match="aucune page"):
        chapitre("cccccccccc", n=2)


def test_serveur_absent(dossiers, monkeypatch):
    monkeypatch.setattr(manga, "MANGA_URL", "http://127.0.0.1:1")
    with pytest.raises(RuntimeError, match="injoignable"):
        chapitre("dddddddddd", n=1)


def test_refaire_une_page(dossiers, monkeypatch):
    mit = FauxMIT()
    monkeypatch.setattr(manga, "MANGA_URL", mit.url)
    res = chapitre("eeeeeeeeee")
    runner.jobs["eeeeeeeeee"].update(statut="terminé", **res)
    ancienne_page, ancien_cbz = res["pages"][1], res["fichier"]
    runner.jobs["ffffffffff"] = {"type": "manga_page", "statut": "en cours", "cree": time.time(),
                                 "parent": "eeeeeeeeee", "page": 2, "remarque": "la bulle du haut est une blague"}
    r = runner.process("ffffffffff")
    assert "blague" in mit.recus[-1]["consignes"]
    chap = runner.jobs["eeeeeeeeee"]
    assert chap["pages"][1] == r["page_url"] != ancienne_page and chap["fichier"] != ancien_cbz
    for ancien in (ancienne_page, ancien_cbz):
        assert not os.path.exists(os.path.join(runner.MEDIA, ancien.rsplit("/", 1)[1]))
    assert "fichier" not in r  # supprimer la tâche « page » ne doit pas effacer la page du chapitre
    with pytest.raises(RuntimeError, match="inexistante"):
        runner.jobs["1111111111"] = {"type": "manga_page", "parent": "eeeeeeeeee", "page": 9, "cree": 0}
        runner.process("1111111111")


def test_le_manga_ne_met_pas_le_chat_en_pause():
    assert "manga" not in runner.AVEC_PAUSE and "manga_page" not in runner.AVEC_PAUSE and "video" in runner.AVEC_PAUSE


def test_supprimer_et_archiver_un_chapitre(dossiers, monkeypatch):
    mit = FauxMIT()
    monkeypatch.setattr(manga, "MANGA_URL", mit.url)
    res = chapitre("2222222222")
    runner.jobs["2222222222"].update(statut="terminé", **res)
    fichiers = runner.fichiers_du_job(runner.jobs["2222222222"])
    assert len(fichiers) == 4  # 3 pages + le .cbz
    if shutil.which("rsync"):
        monkeypatch.setattr(runner, "ARCHIVE", str(dossiers / "pve2") + "/")
        assert runner.archiver_job("2222222222") == "ok"
        envoyes = [f for _, _, fs in os.walk(dossiers / "pve2") for f in fs]
        assert len([f for f in envoyes if f.endswith(".png")]) == 3 and any(f.endswith(".cbz") for f in envoyes)
        assert "pages" not in runner.jobs["2222222222"]
    else:
        assert runner.supprimer_job("2222222222") == "ok"
    assert not any(os.path.exists(f) for f in fichiers)
    assert not os.path.exists(os.path.join(manga.TRAVAIL, "2222222222"))


def test_liseuse_echappe_tout():
    page = runner.page_liseuse({"type": "manga", "statut": "terminé", "serie": "<script>alert(1)</script>",
                                "pages": ["/studio/media/" + "a" * 32 + ".png", "javascript:alert(1)"],
                                "fichier": "/studio/media/" + "b" * 32 + ".cbz", "pages_non_traduites": [2]})
    assert "<script>alert" not in page and "javascript:" not in page and "a" * 32 + ".png" in page
    assert "Télécharger .cbz" in page and "version originale" in page
    assert "pas (ou plus) sur le Spark" in runner.page_liseuse({})


# ------------------------------------------------------------------ outil Open WebUI

def test_outil_recupere_les_fichiers_du_message(monkeypatch, tmp_path):
    (tmp_path / "chap.cbz").write_bytes(cbz_de(["1.png"]))
    fichiers = {"11111111-1111-1111-1111-111111111111": types.SimpleNamespace(path="chap.cbz", filename="chap.cbz")}

    class Files:
        @staticmethod
        async def get_file_by_id(fid):
            return fichiers.get(fid)

    class Storage:
        @staticmethod
        def get_file(p):
            return str(tmp_path / p)
    monkeypatch.setitem(sys.modules, "open_webui", types.ModuleType("open_webui"))
    monkeypatch.setitem(sys.modules, "open_webui.models", types.ModuleType("open_webui.models"))
    monkeypatch.setitem(sys.modules, "open_webui.models.files", types.SimpleNamespace(Files=Files))
    monkeypatch.setitem(sys.modules, "open_webui.storage", types.ModuleType("open_webui.storage"))
    monkeypatch.setitem(sys.modules, "open_webui.storage.provider", types.SimpleNamespace(Storage=Storage))
    import manga_tool
    data = "data:image/jpeg;base64," + base64.b64encode(png()).decode()
    messages = [{"role": "user", "content": [{"type": "text", "text": "traduis"}, {"type": "image_url", "image_url": {"url": data}}]}]
    out = asyncio.run(manga_tool._fichiers_du_message(
        [{"id": "11111111-1111-1111-1111-111111111111", "name": "chap.cbz"}], messages, None))
    assert [n for n, _ in out] == ["chap.cbz", "page002.jpg"]


def test_outil_vue_n_accepte_que_les_medias_du_studio():
    import manga_tool
    vue = manga_tool._vue({"pages": ["/studio/media/" + "a" * 32 + ".png", "https://evil.example/x.png"],
                           "fichier": "javascript:alert(1)"}, "abcdefabcd")
    assert "evil.example" not in vue and "javascript:" not in vue and "/studio/manga/abcdefabcd" in vue


def test_api_manga(dossiers):
    import http.client
    srv = ThreadingHTTPServer(("127.0.0.1", 0), runner.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def appel(methode, chemin, corps=None):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request(methode, chemin, body=json.dumps(corps) if corps is not None else None)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    try:
        assert appel("POST", "/studio/api/jobs", {"type": "manga"})[0] == 400  # pas de fichiers
        assert appel("POST", "/studio/api/jobs", {"type": "manga_page", "parent": "../x", "page": "1"})[0] == 400
        assert appel("POST", "/studio/api/manga/carnet", {"serie": "Test", "original": "猫", "traduction": "Chat"})[0] == 200
        assert appel("GET", "/studio/api/manga/carnet?serie=Test")[1]["entrees"][0]["traduction"] == "Chat"
        assert appel("GET", "/studio/api/manga/series")[1] == [{"serie": "Test", "entrees": 1}]
        assert appel("POST", "/studio/api/manga/carnet", {"serie": "Test", "original": ""})[0] == 400
        code, r = appel("POST", "/studio/api/jobs", {"type": "manga", "serie": "Test", "langue": "xx",
                                                    "fichiers_b64": [{"nom": "a.png", "b64": "AA=="}]})
        assert code == 200 and runner.jobs[r["id"]]["langue"] == "auto"
        assert "fichiers_b64" not in appel("GET", "/studio/api/jobs/" + r["id"])[1]  # jamais renvoyés
    finally:
        srv.shutdown()
        while not runner.todo.empty():
            runner.todo.get_nowait()


def test_rendu_avec_une_police_qui_a_les_accents():
    conf = manga.configuration("ja", "/x.json")
    assert conf["render"]["renderer"] == "manga2eng"  # police comic shanns 2 : accents OK (anime_ace les casse)
    assert conf["translator"]["target_lang"] == "FRA" and conf["ocr"]["use_mocr_merge"] is True
    assert manga.configuration("en", "/x.json")["ocr"]["use_mocr_merge"] is False
    post = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manga", "fr_post_dict.txt")).read()
    assert "œ\toe" in post and "Œ\tOE" in post
