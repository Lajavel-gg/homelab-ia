"""Rend le détecteur « paddle » facultatif si son module Rust n'a pas pu être installé (ARM64).
Sans ça, `import manga_translator.detection` échoue et tout le serveur refuse de démarrer."""
import importlib.util
import re

if importlib.util.find_spec("rusty_manga_image_translator") is None:
    p = "/app/manga_translator/detection/__init__.py"
    s = open(p).read()
    s = s.replace("from .paddle_rust import PaddleDetector\n",
                  "try:\n    from .paddle_rust import PaddleDetector\nexcept ImportError:  # pas de module Rust (ARM)\n"
                  "    PaddleDetector = None\n", 1)
    s = re.sub(r"(DETECTORS = \{.*?\n\})", r"\1\nDETECTORS = {k: v for k, v in DETECTORS.items() if v is not None}", s,
               count=1, flags=re.S)
    open(p, "w").write(s)
    print("détecteur paddle désactivé (module Rust absent)")
else:
    print("module Rust présent : rien à changer")
