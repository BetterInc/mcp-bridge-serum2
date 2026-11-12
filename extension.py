"""MCP Bridge Serum 2: full control of Xfer Serum 2 from BetterFL MCP Bridge.

Free extension. BetterFL loads this file as module betterfl_ext_<folder> and calls register(api);
everything FL-related goes through that ExtensionAPI (see BetterFL's bridge.py).

Two routes into a running Serum 2:
  - knobs: its 669 host parameters, set by displayed value (fl_serum_set, fl_serum_recipe);
  - everything else (wavetables, mod matrix, LFO shapes, FX racks, presets): its VST3 state, read
    and loaded live through FL's plugin wrapper (fl_serum_state, fl_serum_state_set,
    fl_serum_preset_load).
"""
import json

api = None  # ExtensionAPI, set by register()


def _presets_dir():
    """Serum 2's preset folder: the "Serum Presets Path" of Serum's own preferences (the user may
    have moved it), else Documents/Xfer/Serum 2 Presets; '' when neither exists."""
    import os
    roots = []
    try:
        prefs = json.loads(api.read_config("serum_prefs") or "{}")
        if prefs.get("Serum Presets Path"):
            roots.append(prefs["Serum Presets Path"])
    except ValueError:
        pass
    docs = api.known_folder("documents")
    if docs:
        roots.append(os.path.join(docs, "Xfer", "Serum 2 Presets"))
    for root in roots:
        presets = os.path.join(root, "Presets")
        if api.list_files(presets, ".SerumPreset", 1):
            return os.path.normpath(presets)
    return ""


# --- Serum 2 state (XferJson container + zstd + CBOR) ------------------------------------------
# A Serum 2 preset and the VST3 processor state share one format:
#   "XferJson\0", u64 json_len, JSON metadata, u32 cbor_len, u32 2, zstd frame of a CBOR map.
# The CBOR codec below re-encodes Serum's output byte-identically (definite lengths, key order
# kept, floats as float32 when exact else float64).

_XFER_MAGIC = b"XferJson\x00"


def _cbor_decode(b, i=0):
    import struct
    ib = b[i]
    major, ai = ib >> 5, ib & 31
    i += 1
    if major == 7:
        if ai == 20:
            return False, i
        if ai == 21:
            return True, i
        if ai in (22, 23):
            return None, i
        if ai == 25:
            h = struct.unpack_from(">H", b, i)[0]
            s, e, m = h >> 15, (h >> 10) & 31, h & 1023
            v = (m / 1024 * 2 ** -14) if e == 0 else (float("inf") if e == 31 and not m else
                                                     float("nan") if e == 31 else (1 + m / 1024) * 2 ** (e - 15))
            return (-v if s else v), i + 2
        if ai == 26:
            return struct.unpack_from(">f", b, i)[0], i + 4
        if ai == 27:
            return struct.unpack_from(">d", b, i)[0], i + 8
        raise ValueError(f"unsupported CBOR simple value {ai}")
    if ai < 24:
        n = ai
    elif ai in (24, 25, 26, 27):
        size = 1 << (ai - 24)
        n = int.from_bytes(b[i:i + size], "big")
        i += size
    else:
        raise ValueError("indefinite CBOR lengths are not supported")
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major == 2:
        return bytes(b[i:i + n]), i + n
    if major == 3:
        return bytes(b[i:i + n]).decode("utf-8"), i + n
    if major == 4:
        out = []
        for _ in range(n):
            v, i = _cbor_decode(b, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(n):
            k, i = _cbor_decode(b, i)
            v, i = _cbor_decode(b, i)
            out[k] = v
        return out, i
    if major == 6:  # tag: keep the value
        return _cbor_decode(b, i)
    raise ValueError(f"bad CBOR major type {major}")


def _cbor_head(major, n):
    if n < 24:
        return bytes([(major << 5) | n])
    for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < (1 << (8 * size)):
            return bytes([(major << 5) | ai]) + n.to_bytes(size, "big")
    raise ValueError("integer too large")


def _cbor_enc(o, out):
    import math
    import struct
    if o is False:
        out += b"\xf4"
    elif o is True:
        out += b"\xf5"
    elif o is None:
        out += b"\xf6"
    elif isinstance(o, int):
        out += _cbor_head(0, o) if o >= 0 else _cbor_head(1, -1 - o)
    elif isinstance(o, float):
        if math.isnan(o) or (math.isinf(o) or struct.unpack(">f", struct.pack(">f", o))[0] == o):
            out += b"\xfa" + struct.pack(">f", o)
        else:
            out += b"\xfb" + struct.pack(">d", o)
    elif isinstance(o, str):
        data = o.encode("utf-8")
        out += _cbor_head(3, len(data)) + data
    elif isinstance(o, (bytes, bytearray)):
        out += _cbor_head(2, len(o)) + bytes(o)
    elif isinstance(o, (list, tuple)):
        out += _cbor_head(4, len(o))
        for v in o:
            _cbor_enc(v, out)
    elif isinstance(o, dict):
        out += _cbor_head(5, len(o))
        for k, v in o.items():
            _cbor_enc(k, out)
            _cbor_enc(v, out)
    else:
        raise TypeError(f"cannot encode {type(o).__name__} as CBOR")


def xfer_unpack(blob, zstd_decompress):
    """XferJson bytes -> (metadata dict, patch dict)."""
    import struct
    if not blob.startswith(_XFER_MAGIC):
        raise ValueError("not an XferJson (Serum 2) blob")
    n = struct.unpack_from("<Q", blob, len(_XFER_MAGIC))[0]
    start = len(_XFER_MAGIC) + 8
    meta = json.loads(bytes(blob[start:start + n]).decode("utf-8"))
    size, kind = struct.unpack_from("<II", blob, start + n)
    if kind != 2:
        raise ValueError(f"unknown XferJson payload kind {kind}")
    raw = zstd_decompress(bytes(blob[start + n + 8:]))
    if raw is None or len(raw) != size:
        raise ValueError("Serum state: zstd frame did not decompress")
    patch, end = _cbor_decode(raw)
    return meta, patch


def xfer_pack(meta, patch, zstd_compress, level=3):
    """(metadata, patch) -> XferJson bytes with a fresh md5 hash of the zstd frame."""
    import hashlib
    import struct
    raw = bytearray()
    _cbor_enc(patch, raw)
    frame = zstd_compress(bytes(raw), level)
    meta = dict(meta)
    if "hash" in meta:
        meta["hash"] = hashlib.md5(frame).hexdigest()
    js = json.dumps(meta, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _XFER_MAGIC + struct.pack("<Q", len(js)) + js + struct.pack("<II", len(raw), 2) + frame


def _recipes():
    return json.loads(api.read_text("data/serum2_recipes.json"))


def cmd_serum_params(args):
    """Serum 2 parameters with their displayed values; match filters by name."""
    index = api.plugin_channel(args, "Serum 2")
    return {"channel": index, "params": api.plugin_params(index, args.get("match", ""), args.get("limit", 700))}


def cmd_serum_set(args):
    """Set Serum 2 parameters by displayed value (one MCP call; the plugin repeats the steps)."""
    index = api.plugin_channel(args, "Serum 2")
    if args.get("_continue"):
        result = api.set_params_text_step(index)
        unknown = api.step.get("unknown", [])
    else:
        values, unknown = api.resolve_param_names(index, args.get("values") or {})
        if not values:
            raise KeyError(f"no such Serum 2 parameters: {unknown}")
        api.step["unknown"] = unknown
        result = api.set_params_text_step(index, values)
    if result.get("done"):
        result.update(channel=index, unknown=unknown + result.pop("missing", []))
    return result


def cmd_serum_recipes(_):
    """Genres and parts available as Serum 2 recipes."""
    d = _recipes()
    return {g: {p: v[p].get("name") for p in v if isinstance(v[p], dict) and "params" in v[p]}
            for g, v in d.items() if not g.startswith("_")}


def cmd_serum_recipe(args):
    """Apply a genre recipe (e.g. techno/bass) to a Serum 2 channel; returns its pattern and notes."""
    recipe = _recipes()[args["genre"]][args.get("part", "bass")]
    if args.get("_continue"):
        result = cmd_serum_set(args)
    else:
        values = dict(recipe["params"])
        values.update(args.get("overrides") or {})
        result = cmd_serum_set(dict(args, values=values))
    if result.get("done"):
        result.update(name=recipe.get("name"), not_a_parameter=recipe.get("not_a_parameter"),
                      mixer_fx=recipe.get("mixer_fx"), pattern=recipe.get("pattern"))
    return result


def cmd_selftest_codec(_):
    """Self-test of the Serum state codec inside FL: native zstd, md5, CBOR round trip."""
    n = api.native
    patch = {"Env0": {"plainParams": {"kParamDecay": 0.25, "kParamSustain": 0.0}}, "presetName": "t",
             "list": [1, -2, 3.5, True, None, b"\x00\x01"]}
    blob = xfer_pack({"fileType": "SerumPreset", "hash": ""}, patch, n.zstd_compress)
    meta, back = xfer_unpack(blob, n.zstd_decompress)
    return {"roundtrip": back == patch, "bytes": len(blob), "hash": meta.get("hash")}


# --- Serum 2 live state (wrapper SaveRestoreState) -------------------------------------------
# The wrapper state (FLP event 213) is u32 12 + [u32 tag][u64 len][data] sections; section 0x35 is
# the plugin state: u32 1 + sections 1 (flags), 3 (VST3 processor state = XferJson, the whole patch),
# 2 (controller state), 4 (FL's parameter slot map). Loading section 0x35 alone with mode 2 hands
# the new state to the running Serum instance.


def _serum_read(channel):
    """-> (plugin state sections, metadata, patch)."""
    n = api.native
    inner = api.plugin_state_sections(channel)
    proc = next((d for t, d in inner if t == 3), None)
    if not proc or not proc.startswith(_XFER_MAGIC):
        raise RuntimeError("channel's plugin state is not a Serum 2 state")
    meta, patch = xfer_unpack(proc, n.zstd_decompress)
    return inner, meta, patch


def _serum_write(channel, inner, meta, patch, controller=None):
    """Load a patch (section 3) and optionally a new controller state (section 2: preset name,
    macro names and other UI-only data) into the running instance."""
    proc = xfer_pack(meta, patch, api.native.zstd_compress)
    ctl = xfer_pack(controller[0], controller[1], api.native.zstd_compress) if controller else None
    api.plugin_state_load(channel, [[t, proc if t == 3 else ctl if t == 2 and ctl else d] for t, d in inner])


def _serum_controller(inner):
    """(meta, data) of the controller state, or None."""
    ctl = next((d for t, d in inner if t == 2), b"")
    return xfer_unpack(ctl, api.native.zstd_decompress) if ctl.startswith(_XFER_MAGIC) else None


_PRESET_INFO = ("presetName", "presetAuthor", "presetDescription")


def _controller_from_preset(controller, pmeta, preset):
    """Controller state carrying the preset's name, author, description and macro names."""
    cmeta, cdata = dict(controller[0]), dict(controller[1])
    for k in _PRESET_INFO:
        value = pmeta.get(k, preset.get(k, ""))
        cmeta[k] = cdata[k] = value
    cdata["presetHasBeenEdited"] = False
    for k in ("arpBankDisplayName", "clipBankDisplayName"):
        if k in preset:
            cdata[k] = preset[k]
    for i in range(8):
        name = (preset.get(f"Macro{i}") or {}).get("name") or f"Macro {i + 1}"
        cdata[f"Macro{i}"] = dict(cdata.get(f"Macro{i}") or {}, name=name)
    return cmeta, cdata


def _serum_overview(patch):
    oscs = {}
    for i in range(3):
        osc = patch.get(f"Oscillator{i}") or {}
        wt = osc.get(f"WTOsc{i}") or {}
        oscs["ABC"[i]] = {"wavetable": wt.get("relativePathToWT") or wt.get("tableDisplayName"),
                          "params": osc.get("plainParams")}
    mods = {k: {"source": v.get("source"), "source_name": v.get("sourceName"), "dest": f"{v.get('destModuleTypeString')}{v.get('destModuleID')}."
                f"{v.get('destModuleParamName')}", "params": v.get("plainParams")}
            for k, v in patch.items() if k.startswith("ModSlot") and isinstance(v, dict) and "source" in v}
    fx = {k: [[e.get("type")] + [x for x in e if x.startswith("FX")] for e in v.get("FX", [])]
          for k, v in patch.items() if k.startswith("FXRack") and isinstance(v, dict)}
    return {"name": patch.get("presetName"), "oscillators": oscs, "mod_slots": mods, "fx_racks": fx}


# Serum 2's mod source names, in the order of its own name table (Serum2.vst3 .rdata); a mod slot's
# "source" is [table index + 1, 0], 0 meaning no source.
SERUM_SOURCES = [
    "Mod Wheel", "Env 1", "Env 2", "Env 3", "Env 4", "LFO 1", "LFO 2", "LFO 3", "LFO 4", "LFO 5",
    "LFO 6", "LFO 7", "LFO 8", "LFO 9", "LFO 10", "Velo", "Note#", "Aftertouch", "Poly Aftertch",
    "Noise OSC", "NoteOn Rand1", "NoteOn Rand2", "NoteOn Alt.", "NoteOn Alt.2", "Macro 1", "Macro 2",
    "Macro 3", "Macro 4", "Macro 5", "Macro 6", "Macro 7", "Macro 8", "Pitch Bend", "Expr X (Pan)",
    "Expr Y (Timbre)", "Expr Z (Press.)", "Release Velo", "Fixed", "LFO 1 Y", "LFO 2 Y", "LFO 3 Y",
    "LFO 4 Y", "LFO 5 Y", "LFO 6 Y", "LFO 7 Y", "LFO 8 Y", "LFO 9 Y", "LFO 10 Y", "OSC A", "OSC B",
    "OSC C", "SUB OSC", "Filter 1", "Filter 2", "Active Voices", "Voice Mod 1", "Voice Mod 2",
    "Voice Index", "NoteOn Rand (Discrete)",
]


def serum_source(name):
    """'env 2' / 'LFO1' / 'velocity' -> [code, 0] for a mod slot's source."""
    if isinstance(name, list):
        return name
    fold = lambda t: "".join(str(t).lower().split()).replace("velocity", "velo")
    for i, n in enumerate(SERUM_SOURCES):
        if fold(n) == fold(name):
            return [i + 1, 0]
    raise ValueError(f"unknown Serum mod source {name!r}; one of {SERUM_SOURCES}")


def _serum_named_sources(patch):
    for k, v in patch.items():
        if k.startswith("ModSlot") and isinstance(v, dict) and isinstance(v.get("source"), list):
            code = v["source"][0]
            v["sourceName"] = SERUM_SOURCES[code - 1] if 0 < code <= len(SERUM_SOURCES) else None


def _find_preset(name):
    n = api.native
    if name.lower().endswith(".serumpreset") and ("\\" in name or "/" in name):
        return name
    root = _presets_dir()
    if not root:
        raise RuntimeError("Serum 2's preset folder (Documents\\Xfer\\Serum 2 Presets\\Presets) was not found")
    want = "".join(name.lower().split())
    files = n.list_files(root, ".SerumPreset", 100000)
    stem = lambda f: "".join(f.replace("/", "\\").rsplit("\\", 1)[-1][:-12].lower().split())
    exact = [f for f in files if stem(f) == want]
    found = exact or [f for f in files if want in stem(f)]
    if not found:
        raise RuntimeError(f"no Serum 2 preset matching {name!r} in {root}")
    return sorted(found, key=len)[0]


def cmd_serum_preset_load(args):
    """Load a .SerumPreset (by name or path) into a running Serum 2 instance."""
    index = api.plugin_channel(args, "Serum 2")
    n = api.native
    path = _find_preset(str(args["preset"]))
    data = api.read_file(path)
    if not data:
        raise RuntimeError(f"cannot read {path}")
    pmeta, preset = xfer_unpack(data, n.zstd_decompress)
    inner, meta, patch = _serum_read(index)
    for k, v in patch.items():  # keep the plugin-state-only keys (component, ...)
        preset.setdefault(k, v) if not k[:1].isupper() else None
    preset.pop("fileType", None)
    controller = _serum_controller(inner)
    if controller:
        controller = _controller_from_preset(controller, pmeta, preset)
    _serum_write(index, inner, meta, preset, controller)
    inner2, meta2, after = _serum_read(index)
    shown = _serum_controller(inner2)
    return {"channel": index, "preset": path,
            "name": shown[0].get("presetName") if shown else pmeta.get("presetName"),
            "macros": [((shown[1].get(f"Macro{i}") or {}).get("name")) for i in range(8)] if shown else None,
            **{k: v for k, v in _serum_overview(after).items() if k != "name"}}


def cmd_serum_presets(args):
    """List Serum 2 presets whose name contains the query."""
    n = api.native
    root = _presets_dir()
    want = "".join(str(args.get("query", "")).lower().split())
    files = [f for f in n.list_files(root, ".SerumPreset", 100000)
             if want in "".join(f.lower().split())] if root else []
    return {"root": root, "count": len(files), "presets": [f[len(root) + 1:] for f in files[:200]]}

def cmd_serum_state(args):
    """Read the running Serum 2's full state: overview, or the given sections as JSON."""
    index = api.plugin_channel(args, "Serum 2")
    inner, meta, patch = _serum_read(index)
    _serum_named_sources(patch)
    out = {"channel": index, "sections": len(patch), "section_names": list(patch)}
    if args.get("controller"):
        ctl = next((d for t, d in inner if t == 2), b"")
        out["controller"] = {"bytes": len(ctl), "head": ctl[:64].hex()}
        if ctl.startswith(_XFER_MAGIC):
            cmeta, cpatch = xfer_unpack(ctl, api.native.zstd_decompress)
            out["controller"].update(meta=cmeta, data=api.jsonable(cpatch))
    if args.get("sections"):
        out["data"] = {k: api.jsonable(patch.get(k)) for k in args["sections"]}
    else:
        out.update(_serum_overview(patch))
    return out


def cmd_serum_state_set(args):
    """Change anything in the running Serum 2's state (wavetables, mod slots, FX racks, LFO shapes)
    and load it into the live instance. Paths starting with 'controller.' go to the controller
    state (macro names, preset name/author/description)."""
    index = api.plugin_channel(args, "Serum 2")
    inner, meta, patch = _serum_read(index)
    controller = None
    for path, value in (args.get("set") or {}).items():
        if path.startswith("controller."):
            controller = controller or _serum_controller(inner)
            if not controller:
                raise RuntimeError("this Serum 2 has no controller state")
            sub = path[len("controller."):]
            api.path_set(controller[1], sub, value)
            if sub in _PRESET_INFO:
                controller[0][sub] = value
            continue
        if isinstance(value, dict) and "source" in value:
            value = dict(value, source=serum_source(value["source"]))
        elif path.endswith(".source"):
            value = serum_source(value)
        api.path_set(patch, path, value)
    _serum_write(index, inner, meta, patch, controller)
    inner2, meta2, after = _serum_read(index)
    ctl_after = _serum_controller(inner2) if controller else None
    check = {}
    for path in (args.get("set") or {}):
        node = ctl_after[1] if path.startswith("controller.") else after
        for k in path.split(".")[1 if path.startswith("controller.") else 0:]:
            node = node[int(k)] if isinstance(node, list) else (node.get(k) if isinstance(node, dict) else None)
            if node is None:
                break
        check[path] = api.jsonable(node)
    return {"channel": index, "now": check}


def register(core):
    global api
    api = core
    api.tool("fl_serum_preset_load", cmd_serum_preset_load, "Load a Serum 2 preset (.SerumPreset name like "
             "'808 - Diesel', or a full path) into the running Serum 2 on a channel.",
             {"channel": {"type": "integer"}, "preset": {"type": "string"}}, ["preset"])
    api.tool("fl_serum_presets", cmd_serum_presets, "List Serum 2 presets (factory and user) whose path contains "
             "the query.", {"query": {"type": "string"}}, [])
    api.tool("fl_serum_state", cmd_serum_state, "Read the running Serum 2's complete patch (its VST3 state): overview of "
             "oscillator wavetables, mod slots and FX racks, or full JSON of the given sections "
             "(e.g. ['Oscillator0','ModSlot0','FXRack0','LFO0','Env1']).",
             {"channel": {"type": "integer"}, "sections": {"type": "array", "items": {"type": "string"}}},
             [])
    api.tool("fl_serum_state_set", cmd_serum_state_set, "Change anything in the running Serum 2 that is not a knob "
             "(wavetable path, mod matrix slots, FX rack chain, LFO shapes...) by dotted paths into "
             "its patch, e.g. {'Oscillator0.WTOsc0.relativePathToWT': 'Analog/Basic_Cjw.wav'}; "
             "value null deletes; 'controller.' paths set macro names and preset info, e.g. "
             "{'controller.Macro0.name': 'WOBBLE', 'controller.presetName': 'My Bass'}. Loads the new state into the "
             "live instance and reads it back.",
             {"channel": {"type": "integer"}, "set": {"type": "object"}}, ["set"])
    api.tool("fl_selftest_codec", cmd_selftest_codec, "Debug: self-test of the Serum 2 state codec (zstd, md5, CBOR).", {}, [])
    api.tool("fl_serum_params", cmd_serum_params, "List Serum 2 parameters (669) with displayed values. channel: global "
             "channel index (default: last Serum 2 channel); match: name filter like 'Filter 1' or 'Env'.",
             {"channel": {"type": "integer"}, "match": {"type": "string"}, "limit": {"type": "integer"}}, [])
    api.tool("fl_serum_set", cmd_serum_set, "Set any Serum 2 knobs/switches by their displayed value in real units or "
             "labels, in ONE call: values={'A Octave': '-1 oct', 'Filter 1 Type': 'MG Low 24', "
             "'Filter 1 Freq': '220 Hz', 'Env 1 Decay': '140 ms', 'Sub Level': '60%', 'A Unison': 3, "
             "'Env 1 Sustain': '-inf'}. Names are case-insensitive. Returns what Serum now displays.",
             {"channel": {"type": "integer"}, "values": {"type": "object"}}, ["values"], stepped=True)
    api.tool("fl_serum_recipes", cmd_serum_recipes, "List built-in Serum 2 sound recipes per genre (techno, house, "
             "tech_house, drum_and_bass, dubstep, trap, hip_hop, future_bass, trance, pop_edm).", {}, [])
    api.tool("fl_serum_recipe", cmd_serum_recipe, "Program a Serum 2 channel from a genre recipe (part 'bass' or "
             "'lead_or_pad'), with optional overrides like fl_serum_set values. Returns the recipe's "
             "MIDI pattern (beats, semitones from root_midi) and suggested mixer FX.",
             {"channel": {"type": "integer"}, "genre": {"type": "string"}, "part": {"type": "string"},
             "overrides": {"type": "object"}}, ["genre"], stepped=True)
