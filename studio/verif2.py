"""Vérificateur v2 : 1) Flash-Next décrit l'image SANS connaître la demande (membres, cou, yeux, matériel, âge apparent),
2) il compare cette description + l'image à la demande, élément par élément. Moins de « oui » et de « non » imaginés."""
import base64, io, json, re, urllib.request

LLAMA = "http://127.0.0.1:8090"

OBS = """Describe this image objectively. Look carefully at each limb, from the shoulder to the hand and from the hip to the foot.
Answer ONLY with JSON:
{"style": "photo" or "anime/illustration",
 "apparent_age": number (age the main person LOOKS),
 "youthful_face": true if the face looks like a teenager or younger than 20,
 "left_arm": what the PERSON's own left arm looks like (whole / ends at shoulder / ends above elbow / ends below elbow / prosthesis / hidden / not in frame),
 "right_arm": same for the person's right arm,
 "left_leg": same for the left leg (whole / ends above knee / ends below knee / prosthesis / hidden / not in frame),
 "right_leg": same for the right leg,
 "eyes": "normal / closed / white or clouded / covered by blindfold / hidden",
 "neck": "everything on or around the neck (collar, cervical brace, tube, choker, nothing)",
 "medical_or_restraint_items": ["every wheelchair, crutch, brace, tube, strap, straitjacket, harness, cuff, chain… with its exact look"],
 "clothing": "short description",
 "framing": "full body / three-quarter / waist-up / close-up"}
The person's left side is on the RIGHT of the image when they face the camera."""

CMP = """You check a generated image against what the user asked.
USER REQUEST (French, phonetic spelling, may contain typos):
{demande}

PROMPT SENT TO THE GENERATOR:
{prompt}

OBJECTIVE DESCRIPTION OF THE IMAGE (made without knowing the request — trust it, and look at the image yourself too):
{obs}

List every element the user REALLY asked for (body, missing or prosthetic limbs with side, eyes, medical items, restraints, clothing, pose, framing, style).
For each element, copy the words of the DESCRIPTION that prove it is there ("evidence"). If the description does not mention it, or describes
something different (for example a harness instead of a straitjacket, a collar but no tracheostomy tube), then visible = false.
Small unrequested details do not matter. A missing limb counts as present if the limb visibly ends early, even if the side or exact level differs slightly.
If the scene is sexy or suggestive and the person looks younger than 20 (youthful_face true), that is a serious problem.
Answer ONLY with JSON:
{{"elements": [{{"element": "short", "evidence": "words from the description or empty", "visible": true/false}}],
 "ok": true if all important elements are visible and nothing important contradicts the request,
 "problemes": ["important problem, in French"],
 "corrections": ["short English phrase describing what must be visible"]}}"""


def _appel(png, texte, max_tokens=900):
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB"); im.thumbnail((1024, 1024))
    b = io.BytesIO(); im.save(b, "JPEG", quality=90)
    body = {"model": "verif", "temperature": 0.1, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()}},
                {"type": "text", "text": texte}]}]}
    req = urllib.request.Request(f"{LLAMA}/v1/chat/completions", data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        txt = json.load(r)["choices"][0]["message"]["content"]
    return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))


def verifier2(png, demande, prompt):
    try:
        obs = _appel(png, OBS, 700)
        v = _appel(png, CMP.format(demande=demande or "(inconnue)", prompt=prompt[:3000], obs=json.dumps(obs, ensure_ascii=False)))
        v["observation"] = obs
        try:
            age = float(obs.get("apparent_age") or 99)
        except (TypeError, ValueError):
            age = 99
        if (obs.get("youthful_face") or age <= 24) and v.get("ok"):
            suggestif = re.search(r"sexy|sexi|soumi|esclave|lingerie|bikini|décolleté|decollet|seins|sein", (demande or "").lower())
            if suggestif:
                v["ok"] = False
                v.setdefault("problemes", []).append("Le visage paraît trop jeune pour une scène sexy.")
                v.setdefault("corrections", []).append("clearly adult woman in her thirties with a mature adult face")
        v["corrections"] = [str(x) for x in (v.get("corrections") or [])][:8]
        v["problemes"] = [str(x) for x in (v.get("problemes") or [])][:8]
        return v
    except Exception as e:  # noqa: BLE001
        return {"ok": None, "erreur": str(e)[:200]}
