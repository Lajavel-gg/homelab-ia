"""Correcteur phonétique et scripts."""
import os
import random
import subprocess
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def charger_phonetique(tmp_path):
    random.seed(1)
    lettres = "pbtdkgfvszmnlRSZiyueoa"
    lignes = ["ortho\tphon\tfreqfilms2"] + [
        f"mot{i}\t{''.join(random.choice(lettres) for _ in range(random.randint(2, 9)))}\t{random.random() * 5:.2f}"
        for i in range(3000)]
    (tmp_path / "lex.tsv").write_text("\n".join(lignes) + "\n")
    src = open(os.path.join(RACINE, "correcteur", "phonetique.py")).read()
    src = src.replace("/opt/ai-stack/phonetique/data/Lexique383.tsv", str(tmp_path / "lex.tsv")).split('if __name__ == "__main__":')[0]
    ns = {"__name__": "phonetique"}
    exec(src, ns)
    return ns


def test_phonetique_meme_resultat_que_la_recherche_complete(tmp_path):
    ph = charger_phonetique(tmp_path)

    def proches_complet(cle, n=6):  # l'ancienne version : compare à toutes les clés
        exact = sorted(ph["PAR_SON"].get(cle, {}).items(), key=lambda x: -x[1])
        if exact:
            return [m for m, _ in exact[:n]], "même son"
        if len(cle) < 3:
            return [], ""
        cands = {}
        for k in ph["CLES"]:
            if abs(len(k) - len(cle)) <= 1 and ph["distance1"](k, cle):
                for m, fq in ph["PAR_SON"][k].items():
                    cands[m] = max(cands.get(m, 0), fq)
        return [m for m, f in sorted(cands.items(), key=lambda x: -x[1])[:n] if f > 0.5], "son proche"
    random.seed(2)
    for _ in range(200):
        cle = "".join(random.choice("pbtdkgfvszmnlRSZiyueoa") for _ in range(random.randint(3, 8)))
        assert ph["proches"](cle) == proches_complet(cle)


def test_levenshtein(tmp_path):
    ph = charger_phonetique(tmp_path)
    assert ph["levenshtein"]("chat", "chat", 2) == 0
    assert ph["levenshtein"]("chat", "caht", 2) == 1  # lettres inversées
    assert ph["levenshtein"]("maison", "masion", 2) == 1


def test_verifier_modeles_liste_ce_qui_manque(tmp_path):
    r = subprocess.run([sys.executable, os.path.join(RACINE, "spark", "scripts", "verifier-modeles"), str(tmp_path),
                        os.path.join(RACINE, "studio")], capture_output=True, text=True)
    assert r.returncode == 1
    assert "MANQUANT  z_image_turbo_int8_convrot.safetensors" in r.stdout
    assert "768p" in r.stdout  # les LoRA des qualités vidéo du runner sont aussi vérifiées
