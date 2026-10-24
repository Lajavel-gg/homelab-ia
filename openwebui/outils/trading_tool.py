"""
title: Trading
description: Suivi du portefeuille FICTIF géré par l'IA du Spark (TradingAgents + Flash-Next) : état, graphiques, décisions expliquées, réglages. Aucun argent réel.
version: 1.0
"""

# Outil Open WebUI créé le 19/10/2026. Lit l'API locale du bot (127.0.0.1:8192, service trading-api).

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request

from fastapi.responses import HTMLResponse

API = "http://127.0.0.1:8192"
NOMS = {"GC=F": "Or", "^GSPC": "S&P 500", "EURUSD=X": "EUR/USD"}
ALIAS = {"or": "GC=F", "gold": "GC=F", "s&p": "^GSPC", "s&p 500": "^GSPC", "sp500": "^GSPC", "eurusd": "EURUSD=X",
         "eur/usd": "EURUSD=X", "euro": "EURUSD=X"}


def _get(chemin):
    with urllib.request.urlopen(API + chemin, timeout=60) as r:
        return json.load(r)


def _post(chemin, data):
    req = urllib.request.Request(API + chemin, data=json.dumps(data).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def _symbole(marche):
    m = (marche or "or").strip()
    return ALIAS.get(m.lower(), m)


def _svg_ligne(points, couleur="#4fa3ff", marques=(), hauteur=220, unite=""):
    """Courbe SVG simple. points = [(étiquette, valeur)], marques = [(index, 'achat'|'vente')]."""
    if len(points) < 2:
        return '<div style="padding:20px;color:#999">Pas encore assez de données pour un graphique.</div>'
    W, H, g, d, h, b = 640, hauteur, 54, 12, 14, 26
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    x = lambda i: g + i * (W - g - d) / (len(points) - 1)
    y = lambda v: h + (hi - v) * (H - h - b) / (hi - lo)
    chemin = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(points))
    aire = chemin + f" L{x(len(points) - 1):.1f},{H - b} L{x(0):.1f},{H - b} Z"
    grille = ""
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        grille += (f'<line x1="{g}" x2="{W - d}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#8884" stroke-width="0.5"/>'
                   f'<text x="{g - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="11" fill="#999">{v:,.{2 if hi - lo < 10 else 0}f}{unite}</text>')
    etiq = ""
    for i in sorted({0, len(points) // 2, len(points) - 1}):
        etiq += f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="middle" font-size="11" fill="#999">{points[i][0][:10]}</text>'
    mq = ""
    for i, sens in marques:
        c = "#2ecc71" if sens == "achat" else "#ff5c5c"
        yy = y(points[i][1])
        tri = (f"{x(i)-6:.1f},{yy+14:.1f} {x(i)+6:.1f},{yy+14:.1f} {x(i):.1f},{yy+4:.1f}" if sens == "achat"
               else f"{x(i)-6:.1f},{yy-14:.1f} {x(i)+6:.1f},{yy-14:.1f} {x(i):.1f},{yy-4:.1f}")
        mq += f'<polygon points="{tri}" fill="{c}"/>'
    return (f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto">{grille}'
            f'<path d="{aire}" fill="{couleur}22"/><path d="{chemin}" fill="none" stroke="{couleur}" stroke-width="2"/>'
            f'<circle cx="{x(len(points)-1):.1f}" cy="{y(vals[-1]):.1f}" r="4" fill="{couleur}"/>{etiq}{mq}</svg>')


def _page(titre, contenu):
    resize = ("<script>function h(){parent.postMessage({type:'iframe:height',height:document.documentElement.scrollHeight},'*')}"
              "addEventListener('load',h);new ResizeObserver(h).observe(document.body);setTimeout(h,300)</script>")
    return HTMLResponse(
        '<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;font-family:system-ui;color:#ddd;background:#1c1c20}'
        '.c{padding:12px 14px}.t{font-size:15px;font-weight:600;margin-bottom:6px}.k{display:flex;gap:18px;flex-wrap:wrap;margin:6px 0 10px}'
        '.k div{font-size:12px;color:#aaa}.k b{display:block;font-size:18px;color:#eee}table{border-collapse:collapse;width:100%;font-size:13px}'
        'td,th{padding:4px 6px;border-bottom:1px solid #333;text-align:left}.v{color:#2ecc71}.r{color:#ff5c5c}'
        '.n{font-size:11px;color:#888;margin-top:6px}</style></head><body><div class="c">'
        f'<div class="t">{titre}</div>{contenu}<div class="n">Portefeuille FICTIF — aucun argent réel.</div></div>{resize}</body></html>',
        headers={"Content-Disposition": "inline"})


class Tools:
    def __init__(self):
        pass

    async def tableau_de_bord(self) -> str:
        """
        Montre l'état du portefeuille fictif géré par l'IA : valeur, gain ou perte depuis le début, positions en cours,
        frais payés, et un GRAPHIQUE de la valeur dans le temps. À utiliser pour « où en est le bot », « combien on a gagné ».
        """
        try:
            e, hist = await asyncio.to_thread(_get, "/etat"), await asyncio.to_thread(_get, "/historique")
        except Exception as ex:  # noqa: BLE001
            return f"Le bot ne répond pas ({ex}). Dis-le simplement à l'utilisateur."
        v, dep = e.get("valeur_eur", 0), e["capital_depart"]
        gain = v - dep
        pts = [(h["date"], float(h["valeur_eur"])) for h in hist]
        px = e.get("derniers_prix", {})
        lignes_pos = ""
        for s, p in e["positions"].items():
            val = p["unites"] * px.get(s, 0) / px.get("EURUSD=X", 1)
            pv = (px.get(s, 0) / p["prix_moyen"] - 1) * 100 if p.get("prix_moyen") else 0
            lignes_pos += (f"<tr><td>{NOMS.get(s, s)}</td><td>{val:,.2f} €</td><td>{p['prix_moyen']:,.2f}</td>"
                           f"<td>{px.get(s, 0):,.2f}</td><td class=\"{'v' if pv >= 0 else 'r'}\">{pv:+.2f} %</td><td>{p.get('depuis','')}</td></tr>")
        tableau = (f"<table><tr><th>Marché</th><th>Montant</th><th>Prix d'achat</th><th>Prix actuel</th><th>Résultat</th><th>Depuis</th></tr>"
                   f"{lignes_pos}</table>" if lignes_pos else '<div style="color:#999">Aucune position : tout est en espèces.</div>')
        kpi = (f'<div class="k"><div>Valeur<b>{v:,.2f} €</b></div>'
               f'<div>Depuis le début<b class="{"v" if gain >= 0 else "r"}">{gain:+,.2f} € ({gain / dep * 100:+.2f} %)</b></div>'
               f'<div>Espèces<b>{e["especes_eur"]:,.2f} €</b></div><div>Frais payés<b>{e.get("frais_payes_eur", 0):.2f} €</b></div>'
               f'<div>État<b>{"⏸️ en pause" if e.get("pause") else "▶️ actif"}</b></div></div>')
        page = _page(f"📈 Portefeuille fictif de l'IA — créé le {e['cree'][:10]}", kpi + _svg_ligne(pts, unite=" €") + tableau)
        contexte = (f"Tableau de bord affiché à l'utilisateur (graphique inclus). Données : valeur {v:.2f} €, départ {dep} €, "
                    f"résultat {gain:+.2f} € ({gain / dep * 100:+.2f} %), espèces {e['especes_eur']:.2f} €, frais {e.get('frais_payes_eur', 0):.2f} €, "
                    f"positions {json.dumps(e['positions'], ensure_ascii=False)}, prix {json.dumps(px)}, pause={e.get('pause')}, "
                    f"réglages {json.dumps(e['reglages'], ensure_ascii=False)}, {len(pts)} points d'historique. "
                    "Commente en 3-5 phrases simples en français, sans répéter tout le tableau ; explique ce que veulent dire les chiffres "
                    "pour un débutant. Rappelle si utile que c'est fictif et que quelques jours ne suffisent pas pour juger.")
        return page, contexte

    async def graphique_marche(self, marche: str = "or", periode: str = "3mo") -> str:
        """
        Affiche le GRAPHIQUE du prix d'un marché avec les achats (▲ vert) et ventes (▼ rouge) faits par l'IA.
        :param marche: « or », « S&P 500 » ou « EUR/USD ».
        :param periode: 1mo, 3mo, 6mo, 1y ou 2y.
        """
        s = _symbole(marche)
        periode = periode if periode in ("5d", "1mo", "3mo", "6mo", "1y", "2y") else "3mo"
        intervalle = "1h" if periode in ("5d", "1mo") else "1d"
        prix = await asyncio.to_thread(_get, f"/prix?symbole={urllib.parse.quote(s)}&periode={periode}&intervalle={intervalle}")
        ordres = [o for o in await asyncio.to_thread(_get, "/ordres?n=500") if o["symbole"] == s]
        pts = [(p["date"], p["prix"]) for p in prix]
        marques = []
        for o in ordres:
            i = next((k for k, (d, _) in enumerate(pts) if d >= o["date"][:len(d)]), len(pts) - 1)
            marques.append((i, o["sens"]))
        page = _page(f"{NOMS.get(s, s)} — {periode}", _svg_ligne(pts, couleur="#f5b942" if s == "GC=F" else "#4fa3ff", marques=marques))
        ctx = (f"Graphique de {NOMS.get(s, s)} affiché ({periode}). Prix début {pts[0][1] if pts else '?'}, fin {pts[-1][1] if pts else '?'}, "
               f"min {min(v for _, v in pts) if pts else '?'}, max {max(v for _, v in pts) if pts else '?'}. Ordres de l'IA sur ce marché : "
               f"{json.dumps(ordres[-10:], ensure_ascii=False)}. Explique simplement la tendance visible et où l'IA a acheté ou vendu.")
        return page, ctx

    async def derniere_analyse(self, marche: str = "or") -> str:
        """
        Donne le raisonnement complet de l'IA pour sa dernière décision sur un marché (analyse technique, actualités,
        débat acheteur/vendeur, décision finale). À utiliser pour « pourquoi il a acheté/vendu », « qu'est-ce qu'il pense de l'or ».
        :param marche: « or », « S&P 500 » ou « EUR/USD ».
        """
        s = _symbole(marche)
        d = await asyncio.to_thread(_get, f"/decisions?symbole={urllib.parse.quote(s)}&n=1")
        if not d:
            return f"L'IA n'a encore pris aucune décision sur {NOMS.get(s, s)} (la première analyse automatique est à 7 h en semaine)."
        a = d[0]
        coupe = lambda t, n: (t or "")[:n]
        return (f"Dernière analyse de l'IA sur {NOMS.get(s, s)} (date {a.get('date')}, durée {a.get('duree_min')} min). "
                f"DÉCISION : {a.get('decision')}.\n\nANALYSE TECHNIQUE : {coupe(a.get('market_report'), 2500)}\n\n"
                f"ACTUALITÉS : {coupe(a.get('news_report'), 2000)}\n\nPLAN : {coupe(a.get('investment_plan'), 1500)}\n\n"
                f"DÉCISION FINALE : {coupe(a.get('final_trade_decision'), 2000)}\n\n"
                "Explique ce raisonnement à un débutant en français simple : les 2-3 arguments principaux, le niveau de confiance, "
                "et définis brièvement chaque terme technique utilisé (RSI, moyenne mobile, etc.).")

    async def historique_ordres(self, nombre: int = 20) -> str:
        """
        Liste les derniers achats et ventes faits par l'IA dans le portefeuille fictif.
        :param nombre: Nombre d'ordres (20 par défaut).
        """
        o = await asyncio.to_thread(_get, f"/ordres?n={int(nombre)}")
        return json.dumps(o, ensure_ascii=False) if o else "Aucun ordre pour l'instant."

    async def changer_reglages(self, part_max_par_marche: float = None, marches: list[str] = None, pause: bool = None,
                               __event_call__=None) -> str:
        """
        Modifie les réglages du bot fictif (demande confirmation à l'utilisateur). Limites fixes : part par marché entre 5 % et 50 %.
        :param part_max_par_marche: Part maximale du capital par marché, entre 0.05 et 0.5 (0.33 actuellement).
        :param marches: Liste des marchés actifs parmi « or », « S&P 500 », « EUR/USD ».
        :param pause: true pour mettre le bot en pause, false pour le relancer.
        """
        demande = {}
        if part_max_par_marche is not None:
            demande["part_max_par_marche"] = part_max_par_marche
        if marches:
            demande["marches_actifs"] = [_symbole(m) for m in marches]
        if pause is not None:
            demande["pause"] = pause
        if not demande:
            return "Aucun réglage demandé."
        # Sans fenêtre de confirmation possible (appel par l'API, autre client), on refuse au lieu de passer outre
        if not __event_call__ or await __event_call__({"type": "confirmation", "data": {
                "title": "Changer les réglages du bot ?", "message": json.dumps(demande, ensure_ascii=False)}}) is not True:
            return "Annulé : l'utilisateur n'a pas confirmé."
        r = await asyncio.to_thread(_post, "/reglages", demande)
        return json.dumps(r, ensure_ascii=False)

    async def analyser_maintenant(self, __event_call__=None) -> str:
        """
        Lance tout de suite une nouvelle analyse de l'IA sur tous les marchés actifs, puis applique ses décisions
        (environ 15 min par marché ; le chat est plus lent pendant ce temps). Demande confirmation.
        """
        if not __event_call__ or await __event_call__({"type": "confirmation", "data": {
                "title": "Lancer une analyse maintenant ?",
                "message": "L'IA va analyser chaque marché (≈ 15 min chacun) et appliquer ses décisions au portefeuille fictif."}}) is not True:
            return "Annulé : l'utilisateur n'a pas confirmé."
        return json.dumps(await asyncio.to_thread(_post, "/lancer", {}), ensure_ascii=False)
