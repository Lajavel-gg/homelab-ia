"""Outil mail (mail/mail_mcp.py) : archivage sans effacement, dossiers et recherches avec accents."""
import contextlib

import pytest

import mail_mcp as mm


class FauxImap:
    def __init__(self, caps=("IMAP4REV1", "MOVE"), inbox=("1", "2", "3"), dossiers=()):
        self.capabilities = tuple(c.encode() for c in caps)
        self.inbox = set(inbox)
        self.dossiers = dossiers
        self.log = []
        self.literal = None

    def select(self, *a, **k):
        return "OK", [b""]

    def create(self, nom):
        self.log.append(("CREATE", nom))
        return "OK", [b""]

    def list(self):
        return "OK", list(self.dossiers) or [b'(\\HasNoChildren \\Archive) "/" "Archive"']

    def expunge(self):
        self.log.append(("EXPUNGE-GLOBAL",))
        return "OK", []

    def uid(self, cmd, *args):
        self.log.append((cmd,) + args + ((("LITTERAL", self.literal),) if self.literal else ()))
        self.literal = None
        if cmd == "SEARCH":
            if args and args[0] is None and args[1] == "UID":
                return "OK", [" ".join(u for u in args[2].split(",") if u in self.inbox).encode()]
            return "OK", [b""]
        if cmd in ("MOVE", "EXPUNGE"):
            for u in args[0].split(","):
                self.inbox.discard(u)
        if cmd == "FETCH" and "X-GM-LABELS" in args[1]:
            return "OK", [b'1 (X-GM-LABELS ("\\\\Inbox" "M&AOk-dical"))']
        return "OK", [b""]


@pytest.fixture
def boite(monkeypatch):
    def brancher(fake):
        monkeypatch.setattr(mm, "_imap", contextlib.contextmanager(lambda: (yield fake)))
        return fake
    return brancher


@pytest.mark.parametrize("caps, attendu", [
    (("IMAP4REV1", "MOVE"), [("MOVE", "1,2", '"Archive"')]),
    (("IMAP4REV1", "UIDPLUS"), [("COPY", "1,2", '"Archive"'), ("STORE", "1,2", "+FLAGS", "(\\Deleted)"), ("EXPUNGE", "1,2")]),
    (("IMAP4REV1",), [("COPY", "1,2", '"Archive"')]),
])
def test_archiver_n_efface_jamais_d_autres_mails(boite, caps, attendu):
    f = boite(FauxImap(caps))
    mm.archiver("1,2")
    actions = [c for c in f.log if c[0] in ("MOVE", "COPY", "STORE", "EXPUNGE")]
    assert actions == attendu
    assert ("EXPUNGE-GLOBAL",) not in f.log


def test_archiver_sans_move_ni_uidplus_le_dit_honnêtement(boite):
    boite(FauxImap(("IMAP4REV1",)))
    assert "ATTENTION" in mm.archiver("1,2")


def test_utf7_exemple_de_la_rfc():
    assert mm._utf7_encode("~peter/mail/台北/日本語") == "~peter/mail/&U,BTFw-/&ZeVnLIqe-"


@pytest.mark.parametrize("nom", ["Résumés du matin", "Médical", "Impôts & taxes", "École", "INBOX", "Été 🌞"])
def test_utf7_aller_retour(nom):
    enc = mm._utf7_encode(nom)
    assert enc.isascii()
    assert mm._utf7_decode(enc) == nom


def test_ranger_sous_un_libelle_avec_accent(boite):
    f = boite(FauxImap(("IMAP4REV1", "X-GM-EXT-1")))
    assert "rangé" in mm.ranger("5", "Médical")
    assert ("CREATE", '"M&AOk-dical"') in f.log
    assert ("STORE", "5", "+X-GM-LABELS", '("M&AOk-dical")') in f.log


def test_dossiers_affichés_décodés(boite):
    boite(FauxImap(dossiers=[b'(\\HasNoChildren) "/" "M&AOk-dical"']))
    assert "Médical" in mm.lister_dossiers()


def test_recherche_gmail_avec_accents(boite):
    f = boite(FauxImap(("IMAP4REV1", "X-GM-EXT-1")))
    assert "Erreur" not in mm.rechercher_mails("facture électricité")
    recherche = [c for c in f.log if c[0] == "SEARCH" and "X-GM-RAW" in c][0]
    assert ("LITTERAL", "facture électricité".encode()) in recherche
