# MCP Bridge Serum 2

Free extension for **BetterFL MCP Bridge** that gives an AI assistant full control of Xfer Serum 2
inside FL Studio.

| Tool | What it does |
|---|---|
| `fl_serum_params` | List Serum 2's 669 host parameters with their displayed values |
| `fl_serum_set` | Set knobs and switches by displayed value: `{"Filter 1 Freq": "440 Hz", "A Unison": 4}` |
| `fl_serum_state` | Read the full patch: wavetables, mod matrix, LFO shapes, FX racks, any section as JSON |
| `fl_serum_state_set` | Change anything in the patch by dotted path; loads it live and reads it back |
| `fl_serum_presets` / `fl_serum_preset_load` | Search and load `.SerumPreset` files (factory and user) |
| `fl_serum_recipes` / `fl_serum_recipe` | Genre sound recipes (techno, house, dubstep, trap, ...) |

## Mod sources

`source` takes a name or a code. Codes are Serum's own source table index + 1:
1 Mod Wheel, 2–5 Env 1–4, 6–15 LFO 1–10, 16 Velo, 17 Note#, 18 Aftertouch, 19 Poly Aftertch,
20 Noise OSC, 21–24 NoteOn Rand/Alt, 25–32 Macro 1–8, 33 Pitch Bend, 34–36 MPE X/Y/Z,
37 Release Velo, 38 Fixed, 39–48 LFO 1–10 Y, 49–52 OSC A/B/C/SUB, 53–54 Filter 1/2,
55 Active Voices, 56–57 Voice Mod 1/2, 58 Voice Index, 59 NoteOn Rand (Discrete).

## FX types

0 Distortion, 1 Flanger, 2 Phaser, 3 Chorus, 4 Delay, 5 Compressor, 6 Reverb, 7 EQ, 8 Filter,
9 Hyper/Dimension, 10 Bode, 11 Convolution, 12 Utility, 13 Splitter L/H, 14 Splitter L/M/H,
15 Splitter M/S. A splitter is followed by its bands' effects (`kParamModuleCountN` per band).
Values Serum leaves out of the state are its defaults. Compressor threshold is linear gain (0–1).

## Install

Requires **BetterFL MCP Bridge** (FL Studio plugin) with **extension API 1**. Releases are signed
`.bflx` bundles; the bridge only installs bundles signed with BetterFL's key.

- From the assistant: `fl_extension_install` with `id = "mcp-bridge-serum2"` downloads the latest release
  (`https://github.com/BetterInc/mcp-bridge-serum2/releases/latest/download/mcp-bridge-serum2.bflx` + `.sig`), verifies and loads it.
- The BetterFL MCP Bridge installer zip already contains the latest release.
- Development copy: `powershell -File install.ps1` copies it to `%LOCALAPPDATA%\BetterFL\extensions\mcp-bridge-serum2`; then `fl_reload`.

`fl_extensions` lists the loaded extensions and their tools.

## Releasing

Push a tag `v<version>` matching `manifest.json`'s `version`. GitHub Actions builds `extension.py`
(when there is a `build.py`), packs `mcp-bridge-serum2.bflx` with `tools/pack.py`, signs it with the
`EXTENSION_SIGNING_KEY` repository secret and publishes `mcp-bridge-serum2.bflx` + `mcp-bridge-serum2.bflx.sig` as a release.
The signing key is never stored in this repository.

## License

MIT, see [LICENSE](LICENSE).
