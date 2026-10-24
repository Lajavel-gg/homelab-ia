"""Tests du homelab, lancés par GitHub à chaque modification (.github/workflows/tests.yml).
Aucun vrai serveur n'est touché : serveur mail, modèle, Proxmox et ComfyUI sont simulés.
En local : pip install -r tests/requirements.txt && pytest -q"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("mail", "resume", "studio", "openwebui/outils", "openwebui/filtres"):
    sys.path.insert(0, os.path.join(RACINE, d))


class FauxServeurHTTP:
    """Petit serveur HTTP local : routes = {(méthode, chemin): fonction(corps) -> (code, objet)}."""

    def __init__(self, routes):
        routes_ = routes

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _repondre(self, methode):
                n = int(self.headers.get("content-length") or 0)
                corps = self.rfile.read(n) if n else b""
                f = routes_.get((methode, self.path.split("?")[0]))
                code, obj = f(corps, self) if f else (404, {})
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._repondre("GET")

            def do_POST(self):
                self._repondre("POST")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def fermer(self):
        self.srv.shutdown()


@pytest.fixture
def faux_http():
    serveurs = []

    def creer(routes):
        s = FauxServeurHTTP(routes)
        serveurs.append(s)
        return s
    yield creer
    for s in serveurs:
        s.fermer()
