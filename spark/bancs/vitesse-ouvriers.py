"""Débit d'écriture des ouvriers : N agents en même temps, réponses longues (1500 jetons imposés),
prompts courts (on mesure l'écriture, pas la lecture). Débit = jetons générés (compteur vLLM) / durée.
Usage : python3 vitesse-ouvriers.py <N> <durée_s>"""
import json, sys, threading, time, urllib.request

N, DUREE = int(sys.argv[1]), int(sys.argv[2])
OUV = "http://127.0.0.1:8092"
stop = threading.Event()
par_demande = []

def agent(i):
    while not stop.is_set():
        corps = {"model": "ouvrier", "max_tokens": 1500, "min_tokens": 1500, "temperature": 0.7,
                 "messages": [{"role": "user", "content": f"Agent {i} : écris un long module Python commenté qui gère une bibliothèque de livres."}]}
        t = time.time()
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(OUV + "/v1/chat/completions", json.dumps(corps).encode(),
                                                                         {"Content-Type": "application/json"}), timeout=900))
            par_demande.append(r["usage"]["completion_tokens"] / (time.time() - t))
        except Exception:
            time.sleep(1)

def generes():
    for l in urllib.request.urlopen(OUV + "/metrics", timeout=10).read().decode().splitlines():
        if l.startswith("vllm:generation_tokens_total{"):
            return float(l.rsplit(" ", 1)[1])

for i in range(N):
    threading.Thread(target=agent, args=(i,), daemon=True).start()
time.sleep(30)                      # mise en route (lecture des prompts, premiers jetons)
g0, t0 = generes(), time.time()
time.sleep(DUREE)
g1, t1 = generes(), time.time()
stop.set()
moy = sum(par_demande) / len(par_demande) if par_demande else 0
print(f"{N} agent(s) : {((g1 - g0) / (t1 - t0)):.1f} tok/s au total | ~{moy:.1f} tok/s par demande terminée ({len(par_demande)} terminées)")
