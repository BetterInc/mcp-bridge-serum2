"""Pack and sign this BetterFL extension: <id>.bflx + <id>.bflx.sig.

    python tools/pack.py --key <signing_key.pem> [--out dist]
    python tools/pack.py --unsigned [--out dist]          (bundle only, for CI smoke tests)

The key is never stored in this repository. In CI it comes from the EXTENSION_SIGNING_KEY secret,
written to a temporary file for the duration of the job.

Bundle (.bflx): b"BFLX1\\0\\0\\0", u32 file count, then per file [u16 path length][utf-8 path][u32 size][bytes]
(little-endian). Signature (.sig): ECDSA P-256 over SHA-256 of the bundle, raw r||s (64 bytes).
BetterFL MCP Bridge only installs bundles signed by the key whose public half is compiled into it.
Shipped files: manifest.json, extension.py, data/** and LICENSE (sources, build and CI files stay out).
"""
import argparse
import json
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIP = ["manifest.json", "extension.py", "LICENSE"]
SHIP_DIRS = ["data"]
SEGMENT = re.compile(r"^[A-Za-z0-9_\-. ]+$")


def collect():
    files = []
    for name in SHIP:
        if os.path.isfile(os.path.join(ROOT, name)):
            files.append(name)
    for d in SHIP_DIRS:
        for root, dirs, names in os.walk(os.path.join(ROOT, d)):
            dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x != "__pycache__")
            for name in sorted(names):
                if name.startswith(".") or name.endswith(".pyc"):
                    continue
                files.append(os.path.relpath(os.path.join(root, name), ROOT).replace("\\", "/"))
    for rel in files:  # the same rule the plugin applies when installing
        if len(rel) > 200 or not all(s and s not in (".", "..") and not s.startswith(".") and SEGMENT.match(s)
                                     for s in rel.split("/")):
            sys.exit(f"path not allowed in a bundle: {rel}")
    if "manifest.json" not in files or "extension.py" not in files:
        sys.exit("manifest.json and extension.py are required (run build.py first if there is one)")
    return files


def bundle_bytes(files):
    out = bytearray(b"BFLX1\0\0\0" + struct.pack("<I", len(files)))
    for rel in files:
        data = open(os.path.join(ROOT, rel), "rb").read()
        p = rel.encode("utf-8")
        out += struct.pack("<H", len(p)) + p + struct.pack("<I", len(data)) + data
    return bytes(out)


def sign(bundle, key_path):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    with open(key_path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        sys.exit("signing key must be an EC P-256 private key")
    der = key.sign(bundle, ec.ECDSA(hashes.SHA256()))
    key.public_key().verify(der, bundle, ec.ECDSA(hashes.SHA256()))  # raises if anything is off
    r, s = decode_dss_signature(der)
    n = key.public_key().public_numbers()
    print("signed; public key X||Y =", (n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big")).hex())
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", help="PEM file with the EC P-256 signing key")
    g.add_argument("--unsigned", action="store_true", help="write the bundle without a signature")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    a = ap.parse_args()

    manifest = json.load(open(os.path.join(ROOT, "manifest.json"), encoding="utf-8"))
    files = collect()
    bundle = bundle_bytes(files)
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, manifest["id"])
    with open(base + ".bflx", "wb") as f:
        f.write(bundle)
    if a.key:
        with open(base + ".bflx.sig", "wb") as f:
            f.write(sign(bundle, a.key))
    print(f"{manifest['name']} {manifest['version']}: {len(files)} files, {len(bundle)} bytes -> "
          f"{base}.bflx{' (+ .sig)' if a.key else ' (unsigned)'}")


if __name__ == "__main__":
    main()
