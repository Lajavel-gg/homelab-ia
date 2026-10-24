"""
Serveur MCP « Mail » pour l'assistant du DGX Spark.

Droits volontairement limités (aucune fonction d'envoi ni de suppression n'existe ici) :
  lire, chercher, résumer, marquer lu/non lu, libellés/dossiers, archiver, créer des BROUILLONS.

Configuration : /app/mail/mail.env (MAIL_IMAP_HOST, MAIL_IMAP_PORT, MAIL_USER, MAIL_PASSWORD, MAIL_SSL).
"""

import base64
import email
import email.utils
import html
import imaplib
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage

from mcp.server.fastmcp import FastMCP

CONF_PATH = os.environ.get("MAIL_CONF", "/app/mail/mail.env")
mcp = FastMCP("mail")


def _conf() -> dict:
    conf = {}
    try:
        with open(CONF_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return conf


class PasConfigure(Exception):
    pass


@contextmanager
def _imap():
    c = _conf()
    if not c.get("MAIL_USER") or not c.get("MAIL_PASSWORD") or c.get("MAIL_PASSWORD") == "A_REMPLIR":
        raise PasConfigure()
    host = c.get("MAIL_IMAP_HOST", "imap.gmail.com")
    port = int(c.get("MAIL_IMAP_PORT", "993"))
    if c.get("MAIL_SSL", "true").lower() == "true":
        m = imaplib.IMAP4_SSL(host, port, timeout=30)
    else:
        m = imaplib.IMAP4(host, port, timeout=30)
    try:
        m.login(c["MAIL_USER"], c["MAIL_PASSWORD"].replace(" ", ""))
        yield m
    finally:
        try:
            m.logout()
        except Exception:
            pass


def _erreur(e: Exception) -> str:
    if isinstance(e, PasConfigure):
        return ("La boîte mail n'est pas encore connectée. Il faut remplir /opt/ai-stack/mcpo/mail/mail.env "
                "(adresse + mot de passe d'application) sur le Spark.")
    return f"Erreur mail : {e}"


def _caps(m) -> set[str]:
    return {(c.decode() if isinstance(c, bytes) else str(c)).upper() for c in (m.capabilities or ())}


def _is_gmail(m) -> bool:
    return "X-GM-EXT-1" in _caps(m)


def _dec(v) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return str(v)


def _utf7_encode(nom: str) -> str:
    """Nom de dossier → « UTF-7 modifié » (RFC 3501), obligatoire en IMAP dès qu'il y a un accent.
    « Résumés » → « R&AOk-sum&AOk-s ». Sans ça, imaplib plante (il n'envoie que de l'ASCII)."""
    out, tampon = [], []

    def vider():
        if tampon:
            b64 = base64.b64encode("".join(tampon).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append("&" + b64 + "-")
            tampon.clear()

    for c in nom:
        if 0x20 <= ord(c) <= 0x7E:
            vider()
            out.append("&-" if c == "&" else c)
        else:
            tampon.append(c)
    vider()
    return "".join(out)


def _utf7_decode(nom: str) -> str:
    """Inverse de _utf7_encode, pour afficher les noms de dossiers renvoyés par le serveur."""
    def remplacer(m):
        s = m.group(1)
        if not s:
            return "&"
        s = s.replace(",", "/")
        return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-16-be", errors="replace")
    return re.sub(r"&([A-Za-z0-9+,]*)-", remplacer, nom)


def _q(folder: str) -> str:
    return '"' + _utf7_encode(folder).replace('"', '\\"') + '"'


def _special(m, flag: str, fallbacks: list[str]) -> str:
    typ, data = m.list()
    names = []
    for raw in data or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        mo = re.match(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)$', line)
        if not mo:
            continue
        name = _utf7_decode(mo.group("name").strip().strip('"'))
        names.append(name)
        if flag.lower() in mo.group("flags").lower():
            return name
    for fb in fallbacks:
        if fb in names:
            return fb
    return None


def _texte(msg) -> str:
    if msg.is_multipart():
        plain, htm = None, None
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ct = part.get_content_type()
            if ct == "text/plain" and plain is None:
                plain = part
            elif ct == "text/html" and htm is None:
                htm = part
        part = plain or htm
    else:
        part = msg
    if part is None:
        return ""
    try:
        payload = part.get_payload(decode=True) or b""
        txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        txt = str(part.get_payload())
    if part.get_content_type() == "text/html":
        txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", txt)
        txt = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", txt)
        txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    return re.sub(r"\n\s*\n+", "\n\n", txt).strip()


def _pieces_jointes(msg) -> list[str]:
    return [_dec(p.get_filename()) for p in msg.walk() if p.get_content_disposition() == "attachment" and p.get_filename()]


def _resume_entete(uid: str, msg, flags: str, dossier: str = "INBOX", extrait: int = 200) -> str:
    lu = "lu" if "\\Seen" in flags else "NON LU"
    corps = _texte(msg)[:extrait].replace("\n", " ")
    pj = _pieces_jointes(msg)
    return (f"[uid {uid} | dossier {dossier}] {lu} | {_dec(msg.get('Date'))}\n  De : {_dec(msg.get('From'))}\n"
            f"  Sujet : {_dec(msg.get('Subject'))}" + (f"\n  Pièces jointes : {', '.join(pj)}" if pj else "")
            + f"\n  Début : {corps}")


def _fetch(m, uids: list[bytes], full: bool):
    out = []
    for uid in uids:
        part = "(FLAGS INTERNALDATE BODY.PEEK[])" if full else "(FLAGS INTERNALDATE BODY.PEEK[]<0.65536>)"
        typ, data = m.uid("FETCH", uid, part)
        if typ != "OK" or not data or data[0] is None:
            continue
        meta = b" ".join(d[0] if isinstance(d, tuple) else d for d in data if d).decode(errors="replace")
        raw = b"".join(d[1] for d in data if isinstance(d, tuple))
        msg = email.message_from_bytes(raw)
        if not msg.get("Date"):
            mo = re.search(r'INTERNALDATE "([^"]+)"', meta)
            if mo:
                msg["Date"] = mo.group(1)
        out.append((uid.decode(), msg, meta))
    return out


@mcp.tool()
def lister_mails(dossier: str = "INBOX", non_lus_seulement: bool = False, depuis_jours: int = 2, maximum: int = 40) -> str:
    """Liste les mails récents d'un dossier (par défaut la boîte de réception) avec expéditeur, sujet, date,
    statut lu/non lu et un extrait. Utilise-le pour faire le point ou le résumé du matin.
    depuis_jours : remonte N jours en arrière. maximum : nombre max de mails (les plus récents)."""
    try:
        with _imap() as m:
            m.select(_q(dossier), readonly=True)
            depuis = (datetime.now() - timedelta(days=max(depuis_jours, 0))).strftime("%d-%b-%Y")
            crit = ["SINCE", depuis] + (["UNSEEN"] if non_lus_seulement else [])
            typ, data = m.uid("SEARCH", None, *crit)
            uids = (data[0] or b"").split()[-max(1, min(maximum, 150)):]
            if not uids:
                return f"Aucun mail dans {dossier} sur les {depuis_jours} dernier(s) jour(s)."
            mails = _fetch(m, list(reversed(uids)), full=False)
            return f"{len(mails)} mail(s) dans {dossier} (du plus récent au plus ancien) :\n\n" + "\n\n".join(
                _resume_entete(u, msg, fl, dossier) for u, msg, fl in mails)
    except Exception as e:
        return _erreur(e)


@mcp.tool()
def lire_mail(uid: str, dossier: str = "INBOX") -> str:
    """Lit le contenu complet d'un mail (par son uid, obtenu avec lister_mails ou rechercher_mails).
    Ne le marque PAS comme lu."""
    try:
        with _imap() as m:
            m.select(_q(dossier), readonly=True)
            mails = _fetch(m, [uid.encode()], full=True)
            if not mails:
                return f"Mail uid {uid} introuvable dans {dossier}."
            u, msg, fl = mails[0]
            pj = _pieces_jointes(msg)
            return (f"De : {_dec(msg.get('From'))}\nÀ : {_dec(msg.get('To'))}\nCc : {_dec(msg.get('Cc'))}\n"
                    f"Date : {_dec(msg.get('Date'))}\nSujet : {_dec(msg.get('Subject'))}\n"
                    + (f"Pièces jointes : {', '.join(pj)}\n" if pj else "") + "\n" + _texte(msg)[:12000])
    except Exception as e:
        return _erreur(e)


@mcp.tool()
def rechercher_mails(requete: str, partout: bool = False, maximum: int = 20) -> str:
    """Cherche des mails. Par défaut dans la boîte de réception ; partout=true pour chercher dans TOUTE la boîte
    (archives comprises). Sur Gmail, la requête utilise la syntaxe Gmail (ex. « from:banque newer_than:30d »,
    « has:attachment facture »). IMPORTANT : un uid n'est valable que dans le dossier indiqué à côté de lui ;
    pour lire ou agir sur un résultat, passe toujours ce dossier aux autres outils."""
    try:
        with _imap() as m:
            gmail = _is_gmail(m)
            dossier = "INBOX"
            if partout:
                dossier = (_special(m, "\\All", ["[Gmail]/All Mail", "[Gmail]/Tous les messages"]) if gmail else None) or "INBOX"
            m.select(_q(dossier), readonly=True)
            if gmail:
                m.literal = requete.encode()  # en littéral : les accents passent (une chaîne ASCII plantait)
                typ, data = m.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
            else:
                m.literal = requete.encode()
                typ, data = m.uid("SEARCH", "CHARSET", "UTF-8", "TEXT")
            uids = (data[0] or b"").split()[-max(1, min(maximum, 50)):]
            if not uids:
                return f"Aucun résultat pour « {requete} » dans {dossier}."
            mails = _fetch(m, list(reversed(uids)), full=False)
            return (f"{len(mails)} résultat(s) dans le dossier « {dossier} » (uids valables uniquement dans ce dossier) :\n\n"
                    + "\n\n".join(_resume_entete(u, msg, fl, dossier) for u, msg, fl in mails))
    except Exception as e:
        return _erreur(e)


@mcp.tool()
def lister_dossiers() -> str:
    """Liste les dossiers / libellés de la boîte mail."""
    try:
        with _imap() as m:
            typ, data = m.list()
            noms = []
            for raw in data or []:
                line = raw.decode(errors="replace")
                mo = re.match(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)$', line)
                if mo:
                    noms.append(_utf7_decode(mo.group("name").strip().strip('"')))
            return "Dossiers : " + ", ".join(noms)
    except Exception as e:
        return _erreur(e)


def _uids(uids: str) -> list[str]:
    return [u for u in re.split(r"[,\s]+", uids.strip()) if u.isdigit()]


def _presents(m, dossier: str, liste: list[str]) -> set[str]:
    """uids de la liste réellement présents dans le dossier (vérification après action)."""
    m.select(_q(dossier), readonly=True)
    typ, data = m.uid("SEARCH", None, "UID", ",".join(liste)) if liste else ("OK", [b""])
    return set(x.decode() for x in (data[0] or b"").split())


def _bilan(fait: int, total: int, quoi: str, echecs: list[str]) -> str:
    if not echecs:
        return f"{fait} mail(s) {quoi} (vérifié auprès du serveur)."
    return (f"ATTENTION : seulement {fait}/{total} mail(s) {quoi}. Échec pour les uids : {', '.join(echecs)}. "
            "Dis-le clairement à l'utilisateur, n'affirme pas que tout est fait.")


@mcp.tool()
def marquer_lu(uids: str, lu: bool = True, dossier: str = "INBOX") -> str:
    """Marque un ou plusieurs mails comme lus (lu=true) ou non lus (lu=false). uids séparés par des virgules."""
    try:
        liste = _uids(uids)
        with _imap() as m:
            m.select(_q(dossier))
            for u in liste:
                m.uid("STORE", u, "+FLAGS" if lu else "-FLAGS", "(\\Seen)")
            m.select(_q(dossier), readonly=True)
            typ, data = m.uid("SEARCH", None, "UID", ",".join(liste), "SEEN" if lu else "UNSEEN")
            ok = set(x.decode() for x in (data[0] or b"").split())
            return _bilan(len(ok), len(liste), "marqué(s) comme " + ("lu(s)" if lu else "non lu(s)"), [u for u in liste if u not in ok])
    except Exception as e:
        return _erreur(e)


@mcp.tool()
def ranger(uids: str, libelle: str, dossier: str = "INBOX") -> str:
    """Range un ou plusieurs mails sous un libellé / dans un dossier (créé s'il n'existe pas).
    Sur Gmail, ajoute le libellé (le mail reste aussi dans la boîte de réception, utilise archiver pour l'en sortir)."""
    try:
        liste = _uids(uids)
        with _imap() as m:
            m.create(_q(libelle))  # sans effet s'il existe déjà
            m.select(_q(dossier))
            echecs = []
            if _is_gmail(m):
                for u in liste:
                    m.uid("STORE", u, "+X-GM-LABELS", "(" + _q(libelle) + ")")
                for u in liste:
                    typ, data = m.uid("FETCH", u, "(X-GM-LABELS)")
                    txt = b" ".join(d if isinstance(d, bytes) else d[0] for d in data if d).decode(errors="replace")
                    if _utf7_encode(libelle).lower() not in txt.lower() and libelle.lower() not in txt.lower():
                        echecs.append(u)
            else:
                for u in liste:
                    typ, _ = m.uid("COPY", u, _q(libelle))
                    if typ != "OK":
                        echecs.append(u)
            return _bilan(len(liste) - len(echecs), len(liste), f"rangé(s) sous « {libelle} »", echecs)
    except Exception as e:
        return _erreur(e)


@mcp.tool()
def archiver(uids: str) -> str:
    """Archive un ou plusieurs mails de la BOÎTE DE RÉCEPTION (uids du dossier INBOX uniquement) : ils sortent de la
    réception mais restent disponibles (rien n'est supprimé). uids séparés par des virgules."""
    try:
        liste = _uids(uids)
        with _imap() as m:
            avant = _presents(m, "INBOX", liste)
            absents = [u for u in liste if u not in avant]
            cibles = [u for u in liste if u in avant]
            if _is_gmail(m):
                dest = _special(m, "\\All", ["[Gmail]/All Mail", "[Gmail]/Tous les messages"])
            else:
                dest = _special(m, "\\Archive", ["Archive", "Archives"]) or "Archive"
                m.create(_q(dest))
            if cibles:
                caps = _caps(m)
                m.select("INBOX")
                for i in range(0, len(cibles), 50):
                    lot = ",".join(cibles[i:i + 50])
                    if "MOVE" in caps:
                        m.uid("MOVE", lot, _q(dest))
                        continue
                    # Serveur sans MOVE : copie, puis retrait de la réception de CES mails seulement.
                    # Jamais de EXPUNGE simple : il effacerait aussi tous les autres mails marqués \Deleted.
                    typ, _ = m.uid("COPY", lot, _q(dest))
                    if typ != "OK" or "UIDPLUS" not in caps:
                        continue  # copiés (ou non) mais laissés dans la réception : rien n'est effacé
                    m.uid("STORE", lot, "+FLAGS", "(\\Deleted)")
                    m.uid("EXPUNGE", lot)
            apres = _presents(m, "INBOX", cibles)
            echecs = [u for u in cibles if u in apres]
            msg = _bilan(len(cibles) - len(echecs), len(liste), "archivé(s) (toujours consultables, rien de supprimé)", echecs)
            if absents:
                msg += f" Note : {len(absents)} uid(s) n'étaient pas dans la réception : {', '.join(absents)}."
            return msg
    except Exception as e:
        return _erreur(e)


@mcp.tool()
def creer_brouillon(destinataire: str, sujet: str, corps: str, en_reponse_a_uid: str = "", cc: str = "") -> str:
    """Crée un BROUILLON dans le dossier Brouillons de la boîte (il n'est PAS envoyé : l'utilisateur le relit
    et l'envoie lui-même). Pour répondre à un mail, passe son uid dans en_reponse_a_uid (garde le fil de discussion)."""
    try:
        c = _conf()
        with _imap() as m:
            msg = EmailMessage()
            msg["From"] = c.get("MAIL_FROM", c["MAIL_USER"])
            msg["To"] = destinataire
            if cc:
                msg["Cc"] = cc
            msg["Date"] = email.utils.formatdate(localtime=True)
            if en_reponse_a_uid:
                m.select("INBOX", readonly=True)
                orig = _fetch(m, [en_reponse_a_uid.encode()], full=False)
                if orig:
                    _, o, _ = orig[0]
                    mid = o.get("Message-ID")
                    if mid:
                        msg["In-Reply-To"] = mid
                        msg["References"] = ((o.get("References") or "") + " " + mid).strip()
                    s = _dec(o.get("Subject"))
                    if not sujet:
                        sujet = s if s.lower().startswith("re:") else f"Re: {s}"
            msg["Subject"] = sujet
            msg.set_content(corps)
            drafts = _special(m, "\\Drafts", ["[Gmail]/Drafts", "[Gmail]/Brouillons", "Drafts", "Brouillons"])
            if drafts is None:
                drafts = "Drafts"
                m.create(_q(drafts))
            typ, _ = m.append(_q(drafts), "(\\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            if typ != "OK":
                return f"Impossible de créer le brouillon dans {drafts}."
            return f"Brouillon créé dans « {drafts} » pour {destinataire} (sujet : {sujet}). Rien n'a été envoyé."
    except Exception as e:
        return _erreur(e)


if __name__ == "__main__":
    mcp.run()
