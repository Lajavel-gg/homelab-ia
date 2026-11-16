"""Test automatique de verifier-tests sur le petit projet Go de démonstration (demo-verifier-tests/).

Lancer depuis n'importe où (il faut git et go) :  python3 test_verifier_tests.py
Le projet est recréé dans un dossier temporaire : un commit avec avant/, puis apres/ copié par-dessus (non commité).
Chaque cas est dans son propre paquet Go, pour qu'un cas ne gêne pas les autres :
  preuve    vraie correction bien testée, plus un commentaire changé et des lignes réalignées : rien à signaler
  inutile   TestVide passe déjà sur l'ancien code                 : non_verifie (rapide)
  echoue    TestMoitie échoue à chaque passage                     : echec
  fragile   TestUnFragile échoue un passage sur trois              : fragile
  bloque    TestBloque ne finit jamais                             : echec (blocage)
  nonteste  le plancher à 0 de Prix n'est vérifié par aucun test   : non_verifie (--mutation seulement)
Pour --doivent-echouer (l'architecte, avant le travail des ouvriers) :
  squelette Triple = panic("à faire") : TestTriple et TestTripleNegatif échouent (bien), TestRien passe : non_verifie
  casse     le test appelle une fonction qui n'existe pas          : impossible
"""
import json, os, shutil, subprocess, tempfile, unittest

ICI = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ICI, "verifier-tests")
DEMO = os.path.join(ICI, "demo-verifier-tests")
FOIS = 6                                                          # TestUnFragile échoue aux passages 3 et 6
ENV = dict(os.environ, VERIFIER_LIMITE_FRAGILE="20s", VERIFIER_LIMITE_ANCIEN="20s")   # le blocage ne coûte que 20 s

ATTENDU_RAPIDE = {
    ("non_verifie", "TestVide"),
    ("echec", "TestMoitie"),
    ("fragile", "TestUnFragile"),
    ("echec", "TestBloque"),
}
ATTENDU_MUTATION = ATTENDU_RAPIDE | {("non_verifie", "nonteste/prix.go:6")}
ATTENDU_SQUELETTE = {("non_verifie", "TestRien"), ("impossible", "TestCasse")}
TRAVAIL = {"preuve", "inutile", "echoue", "fragile", "bloque", "nonteste"}
SQUELETTE = {"squelette", "casse"}


def projet(paquets=None):
    """Crée le dépôt de démonstration dans un dossier temporaire et renvoie son chemin."""
    d = tempfile.mkdtemp(prefix="demo-verifier-")
    def copier(source):
        for racine, _, fichiers in os.walk(source):
            rel = os.path.relpath(racine, source)
            if paquets and rel != "." and rel.split(os.sep)[0] not in paquets:
                continue
            os.makedirs(os.path.join(d, rel), exist_ok=True)
            for f in fichiers:
                shutil.copy(os.path.join(racine, f), os.path.join(d, rel, f))
    git = lambda *a: subprocess.run(["git", "-c", "user.email=demo@example.com", "-c", "user.name=demo", *a],
                                    cwd=d, check=True, capture_output=True)
    git("init", "-q")
    copier(os.path.join(DEMO, "avant"))
    git("add", ".")
    git("commit", "-qm", "départ")
    copier(os.path.join(DEMO, "apres"))
    return d


def lancer(dossier, *options):
    p = subprocess.run([SCRIPT, "--dossier", dossier, "--fois", str(FOIS), "--json", *options],
                       capture_output=True, text=True, env=ENV, timeout=900)
    return p.returncode, json.loads(p.stdout)


def resume(j):
    """Problèmes bloquants sous forme (type, test) ou (type, fichier:ligne)."""
    return {(p["type"], p["test"] if p["test"] and not p["test"].startswith("./") else f"{p['fichier']}:{p['ligne']}")
            for p in j["problemes"] if p["bloquant"]}


class TestVerifierTests(unittest.TestCase):
    def setUp(self):
        self.dossiers = []

    def tearDown(self):
        for d in self.dossiers:
            shutil.rmtree(d, ignore_errors=True)

    def nouveau(self, paquets=None):
        d = projet(paquets)
        self.dossiers.append(d)
        return d

    def etat(self, d):
        return subprocess.run(["git", "status", "--porcelain"], cwd=d, capture_output=True, text=True).stdout

    def test_rapide(self):
        d = self.nouveau(TRAVAIL)
        avant = self.etat(d)
        code, j = lancer(d)
        self.assertEqual(code, 1)
        self.assertEqual(j["statut"], "probleme")
        self.assertEqual(resume(j), ATTENDU_RAPIDE)
        self.assertEqual([p for p in j["problemes"] if p["type"] == "impossible"], [])
        self.assertEqual(self.etat(d), avant, "le dossier de travail a été modifié")

    def test_mutation(self):
        d = self.nouveau(TRAVAIL)
        code, j = lancer(d, "--mutation")
        self.assertEqual(code, 1)
        self.assertEqual(resume(j), ATTENDU_MUTATION)
        # le commentaire changé et les lignes réalignées de preuve/calcul.go ne sont pas signalés
        self.assertFalse([p for p in j["problemes"] if p["fichier"].startswith("preuve/")])
        # les paquets où un test échoue déjà sont sautés, avec une explication
        for paquet in ("./bloque", "./echoue", "./fragile"):
            self.assertIn(f"mutation sautée pour {paquet} : des tests y échouent déjà", j["avertissements"])
        self.assertEqual(subprocess.run(["git", "worktree", "list"], cwd=d, capture_output=True, text=True).stdout.count("\n"), 1,
                         "une copie temporaire est restée")

    def test_rien_a_signaler(self):
        """Seulement preuve : correction testée, commentaire changé, lignes réalignées -> code 0 dans les deux modes."""
        d = self.nouveau(paquets={"preuve"})
        for options in ((), ("--mutation",)):
            code, j = lancer(d, *options)
            self.assertEqual((code, j["statut"], resume(j)), (0, "ok", set()), options)

    def test_doivent_echouer(self):
        """Squelette de l'architecte : chaque test lancé seul doit compiler et échouer."""
        d = self.nouveau(SQUELETTE)
        code, j = lancer(d, "--doivent-echouer")
        self.assertEqual((code, j["statut"]), (1, "probleme"))
        self.assertEqual(resume(j), ATTENDU_SQUELETTE)
        # sans les deux mauvais tests, tout va bien : les deux vrais tests échouent bien sur le squelette
        os.remove(os.path.join(d, "casse", "casse_test.go"))
        with open(os.path.join(d, "squelette", "calcul_test.go")) as f:
            texte = f.read()
        with open(os.path.join(d, "squelette", "calcul_test.go"), "w") as f:
            f.write(texte.split("// ne prouve rien")[0])
        code, j = lancer(d, "--doivent-echouer")
        self.assertEqual((code, j["statut"], resume(j)), (0, "ok", set()))
        self.assertEqual({t["test"] for t in j["tests"]}, {"TestTriple", "TestTripleNegatif"})

    def test_erreurs_code_2(self):
        code, j = lancer("/dossier/qui/nexiste/pas")
        self.assertEqual((code, j["statut"]), (2, "erreur"))
        d = tempfile.mkdtemp(prefix="demo-py-")
        self.dossiers.append(d)
        open(os.path.join(d, "pyproject.toml"), "w").close()
        for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=demo@example.com", "-c", "user.name=demo", "commit", "-qm", "x"]):
            subprocess.run(["git", *cmd], cwd=d, check=True, capture_output=True)
        code, j = lancer(d)
        self.assertEqual((code, j["statut"]), (2, "erreur"))
        self.assertIn("langage pas encore géré", j["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
