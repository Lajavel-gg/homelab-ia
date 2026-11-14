"""Charge courte qui imite le blocage : 16 « agents » (longs prompts à début commun) sur les ouvriers
+ 2 demandes en boucle sur le chef. Détecteur : des demandes tournent mais le compteur de jetons
générés ne bouge plus pendant 45 s → FIGÉ. Affiche TENU, FIGÉ ou PLANTÉ.
Usage : python3 charge-blocage.py <durée_s> [sans-chef]"""
import json, os, sys, threading, time, urllib.request, random

DUREE = int(sys.argv[1]) if len(sys.argv) > 1 else 240
AVEC_CHEF = "sans-chef" not in sys.argv
OUV, CHEF = "http://127.0.0.1:8092", os.environ.get("CHEF_URL", "http://127.0.0.1:8090")
MODELE_CHEF = os.environ.get("MODELE_CHEF", "chef")  # essai c : le « chef » est le même serveur que les ouvriers
fin = time.time() + DUREE
stop = threading.Event()

COMMUN = ("Tu es un agent de code. Règles du projet :\n" +
          "\n".join(f"- Règle {i} : écris des fonctions courtes, testées, avec des noms clairs en français (module {i})." for i in range(450)))

def post(url, corps, timeout=600):
    r = urllib.request.Request(url + "/v1/chat/completions", json.dumps(corps).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))

def agent(n):
    while not stop.is_set() and time.time() < fin:
        tache = f"Tâche {n}-{random.randint(0, 10**9)} : " + " ".join(f"élément{random.randint(0, 99999)}" for _ in range(1500))
        try:
            post(OUV, {"model": "ouvrier", "max_tokens": 400, "temperature": 0.7,
                       "messages": [{"role": "system", "content": COMMUN}, {"role": "user", "content": tache + "\nÉcris une fonction Python qui trie ces éléments."}]})
        except Exception:
            time.sleep(2)

def chef():
    while not stop.is_set() and time.time() < fin:
        try:
            post(CHEF, {"model": MODELE_CHEF, "max_tokens": 600, "messages": [{"role": "user", "content": f"Explique en 400 mots le cycle de l'eau ({random.random()})."}]})
        except Exception:
            time.sleep(2)

def metrique():
    t = urllib.request.urlopen(OUV + "/metrics", timeout=10).read().decode()
    v = {}
    for l in t.splitlines():
        for k in ("num_requests_running", "generation_tokens_total"):
            if l.startswith(f"vllm:{k}{{"):
                v[k] = float(l.rsplit(" ", 1)[1])
    return v

for i in range(16):
    threading.Thread(target=agent, args=(i,), daemon=True).start()
if AVEC_CHEF:
    for _ in range(2):
        threading.Thread(target=chef, daemon=True).start()

debut, dernier, immobile, resultat = time.time(), None, 0, "TENU"
while time.time() < fin:
    time.sleep(5)
    try:
        m = metrique()
    except Exception:
        resultat = f"PLANTÉ à {time.time()-debut:.0f} s"; break
    g = m.get("generation_tokens_total", 0)
    if m.get("num_requests_running", 0) > 0 and dernier is not None and g == dernier:
        immobile += 5
        if immobile >= 45:
            resultat = f"FIGÉ à {time.time()-debut-45:.0f} s"; break
    else:
        immobile = 0
    dernier = g
stop.set()
print(f"{resultat} | jetons générés par les ouvriers : {int(dernier or 0)}")
