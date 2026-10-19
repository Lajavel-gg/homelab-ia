#!/usr/bin/env python3
"""Convertit un workflow ComfyUI « UI » (sans sous-graphe) au format API, avec /object_info."""
import json, sys

SKIP = {"Note", "MarkdownNote", "PrimitiveNode", "Reroute"}

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO", "COLOR", "LOAD_3D"}


def is_widget(t, opts):
    if opts.get("forceInput"):
        return False
    return isinstance(t, list) or t in WIDGET_TYPES or str(t).startswith("COMFY_DYNAMICCOMBO")


def consume(spec_inputs, vals, i, inputs, prefix=""):
    """Assigne les widgets_values aux entrées (combos dynamiques : valeur puis sous-entrées de l'option choisie)."""
    for sec in ("required", "optional"):
        for name, v in spec_inputs.get(sec, {}).items():
            t = v[0]
            opts = v[1] if len(v) > 1 and isinstance(v[1], dict) else {}
            if not is_widget(t, opts) or i >= len(vals):
                continue
            val = vals[i]; i += 1
            inputs[prefix + name] = val
            if str(t).startswith("COMFY_DYNAMICCOMBO"):
                opt = next((o for o in opts.get("options", []) if o["key"] == val), None)
                if opt:
                    i = consume(opt.get("inputs", {}), vals, i, inputs, prefix + name + ".")
                continue
            if i < len(vals) and (opts.get("control_after_generate") or name in ("seed", "noise_seed")) and vals[i] in ("fixed", "randomize", "increment", "decrement"):
                i += 1
            if i < len(vals) and (opts.get("image_upload") or name == "image") and vals[i] == "image":
                i += 1
    return i


def convert(ui, oi):
    links = {l[0]: l for l in ui["links"]}  # id, from, from_slot, to, to_slot, type
    api = {}
    for n in ui["nodes"]:
        if n["type"] in SKIP or n.get("mode") in (2, 4):
            continue
        spec = oi[n["type"]]
        inputs = {}
        wv = n.get("widgets_values")
        if isinstance(wv, dict):
            inputs.update(wv)
        else:
            consume(spec["input"], list(wv or []), 0, inputs)
        for inp in n.get("inputs", []):
            if inp.get("link") is not None:
                l = links[inp["link"]]
                inputs[inp["name"]] = [str(l[1]), l[2]]
        api[str(n["id"])] = {"class_type": n["type"], "inputs": inputs, "_meta": {"title": n.get("title", n["type"])}}
    return api

if __name__ == "__main__":
    ui = json.load(open(sys.argv[1])); oi = json.load(open(sys.argv[2]))
    print(json.dumps(convert(ui, oi), indent=1, ensure_ascii=False))
