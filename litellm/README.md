# LiteLLM : une seule porte vers les IA du Spark

Une petite VM Proxmox (2 cœurs, 4 Go) fait passer **toutes** les demandes d'IA des autres machines
(appli de chat, agent Hermes, OpenCode, bot mail…) vers le Spark.

```
VM appli ──┐
VM Hermes ─┤                 HTTPS (4443)          porte réservée du Caddy
VM OpenCode┼──> LiteLLM (VM) ─────────────> Spark : /chef     -> vLLM chef (8090)
bot mail ──┘   une clé par VM                      /ouvriers -> vLLM ouvriers (8092)
```

Pourquoi :
- **Une clé par machine**, qu'on peut couper en un clic si une VM est compromise, sans toucher aux autres.
- **Une seule porte** sur le Spark (celle de LiteLLM) au lieu d'une par VM.
- **Journal** : quelle VM a utilisé quel modèle, et quand ; limites par clé possibles.
- **Changer de modèle à un seul endroit** (`config.yaml`) : les VM demandent `chef` ou `ouvrier`.

Sécurité :
- LiteLLM n'écoute que sur `127.0.0.1:4000` ; le réseau ne voit que **Caddy en HTTPS sur 4443**
  ([`Caddyfile`](Caddyfile)), avec un certificat signé par la CA locale déjà connue des VM.
- Pare-feu Proxmox de la VM : entrée 4443 seulement depuis les VM clientes, sortie seulement vers le Spark
  (porte réservée) et Internet pour les mises à jour.
- Télémétrie coupée, secrets dans `.env` (voir [`.env.example`](.env.example)).

Créer une clé pour une VM (depuis la VM LiteLLM) :

```sh
curl -s http://127.0.0.1:4000/key/generate -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' -d '{"key_alias":"hermes","models":["chef","ouvrier"]}'
```
