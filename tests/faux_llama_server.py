"""Faux llama-server pour les tests de llama-swap : note ses arguments et répond comme llama-server."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])
modele = sys.argv[sys.argv.index("-m") + 1]
with open(os.environ["FAUX_JOURNAL"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def repondre(self, obj):
        d = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_GET(self):
        self.repondre({"status": "ok"} if self.path == "/health" else [{"id": 0, "is_processing": False}])

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.path.endswith("/embeddings"):
            return self.repondre({"data": [{"embedding": [0.1, 0.2], "index": 0}], "model": modele})
        self.repondre({"choices": [{"message": {"role": "assistant", "content": f"réponse de {os.path.basename(modele)}"}}]})


ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
