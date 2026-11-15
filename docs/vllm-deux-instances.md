# Deux vLLM sur un seul GB10 : le bug des ouvriers compilés (résolu)

*13/11/2026 : contournement `--enforce-eager`. 14/11/2026 au soir : résolu en passant à vLLM 0.34.*

**État actuel.** Le chef (Qwen3.8-27B NVFP4, port 8090) et les ouvriers (Nemotron 3.5 Lightning 30B-A3B NVFP4,
port 8092) tournent tous les deux **compilés, avec les graphes CUDA et l'ordonnancement asynchrone**. Les ouvriers
sont sur l'image `vllm/vllm-openai:nightly-615c9d6474a7793a024786e67fbb99791e809d98` (vLLM 0.34.0rc1, torch 2.14,
triton 3.8), sans `--enforce-eager` ni `--compilation-config` (voir [`spark/systemd/vllm-ouvriers.service`](../spark/systemd/vllm-ouvriers.service)).
Mesuré en prod : 75 / 237 / 320 tok/s pour 1 / 8 / 16 agents, et aucun Xid depuis.

**La cause trouvée (14/11).** Ce sont les noyaux de *réduction* générés par Inductor (torch.compile) dans les
ouvriers : RMSNorm, la norme « gated » de Mamba2, `pow`. Le plantage n'arrive que si trois choses sont réunies :
ces noyaux compilés, l'ordonnancement asynchrone, et un deuxième processus (le chef) qui ralentit le GPU.
Chacun des contournements suivants suffisait à le faire disparaître : `--enforce-eager`, `--no-async-scheduling`,
`--compilation-config '{"backend":"eager"}'`, ou repasser `mean`/`rsqrt`/`pow` sur les noyaux d'origine (aten).
Dans vLLM 0.34, le noyau de la norme gated est généré autrement (noyau « persistant ») et le plantage a disparu.

**Règle.** Avant toute nouvelle image vLLM : `charge-blocage.py 240` doit donner TENU deux fois de suite, avec le
chef de prod qui génère en même temps.

**Astuce pour qui teste des correctifs Inductor :** mettre `VLLM_DISABLE_COMPILE_CACHE=1`, sinon vLLM réutilise les
noyaux déjà compilés et le test ne prouve rien.

## Historique de l'enquête (13 et 14/11)

**Le bug.** Quand le chef génère en même temps que les ouvriers, les ouvriers compilés plantent
(Xid 31, MMU fault, surtout `FAULT_PDE ACCESS_TYPE_VIRT_READ`) ou se figent (GPU à 96 %, 15 W), en ~15 s.
Repro : [`spark/bancs/charge-blocage.py`](../spark/bancs/charge-blocage.py) (16 agents sur 8092 + 2 boucles sur le chef).

**Ce qui est en cause : torch.compile des ouvriers, pas les graphes CUDA.**
`--compilation-config '{"cudagraph_mode":"NONE"}'` (graphes coupés, compilation gardée) plante aussi.
Seul `--enforce-eager` (ni compilation ni graphes) tient (testé 4 fois à la charge, plus 2 vraies
sessions OpenCode à 16 agents : 0 Xid).

**Essais .**

| Essai | Résultat |
|---|---|
| a. vitesse ouvriers seuls, graphes vs eager | 1 agent 77 → 62 tok/s (−20 %), 8 agents 247 → 228 (−8 %), 16 agents 332 → 313 (−6 %) |
| b. on change le chef (eager / sans V2 runner / sans MTP), ouvriers compilés | les 3 plantent (21 s, 15 s, 20 s) → le chef n'est pas en cause. Chef eager : 19 tok/s, sans MTP : 12 tok/s |
| c. un seul modèle (Lightning fait aussi le master) | tient, chat 75 tok/s, mais routage FR 16/24 (chef Qwen : 24/24) → abandonné |
| d. vLLM 0.33.0 stable | ouvriers 0.33 + chef nightly : plante (15 s) ; tout en 0.33 : figé (20 s) → pas une régression de la nightly |

Déjà éliminés pendant l'enquête : autres processus GPU, cache de préfixe, KV bf16, backends MoE et
Mamba par défaut, FULL_DECODE_ONLY, petites tailles de capture, cache de compilation séparé,
noyau FlashInfer FP8 (`VLLM_DISABLED_KERNELS`).

**Issue amont.** vLLM #55406 (même modèle, accès mémoire illégal, contournement eager).
Différence : le ticket parle du rejeu des graphes ; chez nous la compilation seule suffit.

**Condition qui avait été fixée pour revenir aux graphes (remplie le 14/11 au soir).** Un correctif de #55406 (ou une nouvelle version de vLLM) ET
`charge-blocage.py 240` donne TENU deux fois de suite avec les ouvriers compilés et le chef de prod
qui génère, puis 0 Xid sur une vraie session OpenCode à 16 agents. Gain attendu : 6 à 8 % en multi-agents.

**Diagnostic du 14/11.** Avec `CUDA_LAUNCH_BLOCKING=1` (chaque ordre GPU attendu), compilation gardée et le chef
qui génère, la même charge **tient** 2 fois sur 2 (débit divisé par 10). Le bug disparaît quand on sérialise les
lancements et n'apparaît que quand un 2ᵉ processus ralentit le GPU : cela ressemble à une course (synchronisation
manquante dans le chemin compilé), pas à un mauvais indice déterministe.

**Coût mesuré d'eager** (ouvriers seuls, réponses de 1500 jetons, [`spark/bancs/vitesse-ouvriers.py`](../spark/bancs/vitesse-ouvriers.py)) :
graphes 77 / 247 / 332 tok/s contre eager 62 / 228 / 313 tok/s pour 1 / 8 / 16 agents.
