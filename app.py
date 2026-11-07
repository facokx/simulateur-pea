"""Simulateur PEA - serveur local gratuit.
Données : Yahoo Finance (via yfinance, gratuit) + Google Actualités (flux RSS, gratuit).
Lancer : python app.py  puis ouvrir http://127.0.0.1:5000
"""
import math
import urllib.parse
from datetime import datetime, date

import feedparser
import yfinance as yf
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder=".")

# Pays de l'UE + EEE : condition de siège pour l'éligibilité PEA
PEA_PAYS = {
    "France", "Germany", "Italy", "Spain", "Netherlands", "Belgium", "Luxembourg",
    "Portugal", "Ireland", "Austria", "Finland", "Sweden", "Denmark", "Greece",
    "Poland", "Czech Republic", "Czechia", "Hungary", "Slovakia", "Slovenia", "Croatia",
    "Romania", "Bulgaria", "Estonia", "Latvia", "Lithuania", "Cyprus", "Malta",
    "Norway", "Iceland", "Liechtenstein",
}

# Mots-clés géopolitiques / environnementaux par secteur (pour les flux d'actualités)
THEMES_SECTEUR = {
    "Energy": "pétrole gaz OPEP sanctions transition énergétique",
    "Utilities": "prix électricité gaz Europe régulation climat",
    "Basic Materials": "matières premières Chine droits de douane métaux",
    "Industrials": "défense aéronautique chaînes d'approvisionnement droits de douane",
    "Consumer Cyclical": "consommation Chine luxe automobile droits de douane",
    "Consumer Defensive": "inflation alimentaire matières premières agricoles",
    "Technology": "semi-conducteurs Chine export restrictions Taïwan",
    "Healthcare": "médicaments prix régulation FDA EMA",
    "Financial Services": "taux BCE banques régulation",
    "Communication Services": "télécoms régulation 5G Europe",
    "Real Estate": "immobilier taux BCE",
}


def propre(v):
    """Rend une valeur sérialisable en JSON."""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "item"):
        try:
            return propre(v.item())
        except Exception:
            pass
    return v


def actualites(requete, n=8):
    url = "https://news.google.com/rss/search?q={}&hl=fr&gl=FR&ceid=FR:fr".format(
        urllib.parse.quote(requete))
    flux = feedparser.parse(url)
    return [{"titre": e.get("title"), "lien": e.get("link"), "date": e.get("published", "")}
            for e in flux.entries[:n]]


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/recherche")
def recherche():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        res = yf.Search(q, max_results=8).quotes
    except Exception as e:
        return jsonify({"erreur": str(e)}), 502
    return jsonify([{"ticker": r.get("symbol"), "nom": r.get("shortname") or r.get("longname"),
                     "bourse": r.get("exchDisp")} for r in res if r.get("quoteType") == "EQUITY"])


@app.route("/api/societe/<ticker>")
def societe(ticker):
    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception as e:
        return jsonify({"erreur": "Données introuvables : " + str(e)}), 502

    nom = info.get("longName") or info.get("shortName") or ticker
    pays = info.get("country")
    secteur = info.get("sector")

    # Historique 1 an pour le graphique de cours
    hist = []
    try:
        h = t.history(period="1y", interval="1wk")
        hist = [{"d": i.strftime("%Y-%m-%d"), "c": round(float(c), 2)} for i, c in h["Close"].items()]
    except Exception:
        pass

    # Dividendes versés (5 ans) -> calendrier habituel
    divs = []
    try:
        d = t.dividends
        divs = [{"date": i.strftime("%Y-%m-%d"), "montant": round(float(v), 4)} for i, v in d.items()][-12:]
    except Exception:
        pass

    # Agenda : résultats, détachement, paiement
    agenda = []
    try:
        cal = t.calendar or {}
        for cle, lib in [("Earnings Date", "Publication de résultats"),
                         ("Ex-Dividend Date", "Détachement du dividende"),
                         ("Dividend Date", "Paiement du dividende")]:
            v = cal.get(cle)
            for x in (v if isinstance(v, list) else [v]):
                if x:
                    agenda.append({"date": propre(x), "evenement": lib})
    except Exception:
        pass

    # Saisonnalité réelle : chiffre d'affaires trimestriel
    trimestres = []
    try:
        q = t.quarterly_income_stmt
        if q is not None and "Total Revenue" in q.index:
            for col, v in q.loc["Total Revenue"].items():
                if v == v:
                    trimestres.append({"trimestre": col.strftime("%Y-%m"), "ca": float(v)})
            trimestres.sort(key=lambda x: x["trimestre"])
    except Exception:
        pass

    # Actualités
    try:
        news_societe = actualites('"{}" bourse'.format(nom.split(" SE")[0].split(" S.A")[0]))
    except Exception:
        news_societe = []
    try:
        theme = THEMES_SECTEUR.get(secteur, secteur or "")
        news_geo = actualites("{} géopolitique {}".format(info.get("industry", ""), theme), 6)
        news_env = actualites("{} climat environnement réglementation".format(info.get("industry") or secteur or nom), 5)
    except Exception:
        news_geo, news_env = [], []

    data = {
        "ticker": ticker,
        "nom": nom,
        "bourse": info.get("fullExchangeName") or info.get("exchange"),
        "devise": info.get("currency"),
        "pays": pays,
        "pea": {
            "eligible": pays in PEA_PAYS,
            "raison": ("Siège en {} (UE/EEE)".format(pays) if pays in PEA_PAYS
                       else "Siège hors UE/EEE ({})".format(pays or "inconnu")),
        },
        "cours": propre(info.get("currentPrice") or info.get("regularMarketPrice")),
        "variation_jour": propre(info.get("regularMarketChangePercent")),
        "plus_haut_52s": propre(info.get("fiftyTwoWeekHigh")),
        "plus_bas_52s": propre(info.get("fiftyTwoWeekLow")),
        "capitalisation": propre(info.get("marketCap")),
        "per": propre(info.get("trailingPE")),
        "secteur": secteur,
        "industrie": info.get("industry"),
        "activite": info.get("longBusinessSummary"),
        "salaries": propre(info.get("fullTimeEmployees")),
        "site": info.get("website"),
        "rendement": propre(info.get("dividendYield")),
        "dividende_annuel": propre(info.get("dividendRate")),
        "croissance_ca": propre(info.get("revenueGrowth")),
        "historique": hist,
        "dividendes": divs,
        "agenda": agenda,
        "trimestres": trimestres,
        "actus": news_societe,
        "actus_geo": news_geo,
        "actus_env": news_env,
        "maj": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    return jsonify(data)


@app.route("/manifest.json")
def manifest():
    return send_from_directory(".", "manifest.json")


@app.route("/icone.png")
def icone():
    return send_from_directory(".", "icone.png")


def ip_locale():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "IP-introuvable"
    finally:
        s.close()


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print("Simulateur PEA")
    print("  Sur cet ordinateur : http://127.0.0.1:%d" % port)
    print("  Sur votre téléphone (même Wi-Fi) : http://%s:%d" % (ip_locale(), port))
    app.run(host="0.0.0.0", port=port, debug=False)
