"""Signed release packages for air-gapped sites (audit #106). Run from repo root:

    python backend/scripts/release.py keygen keys/          # once, on the vendor's offline signing machine
    python backend/scripts/release.py build --key keys/release_ed25519.pem
    python backend/scripts/release.py verify dist/sovereign-optimizer-0.3.0.zip --pub keys/release_ed25519.pub

build  -> dist/<name>.zip + SHA256SUMS + SHA256SUMS.sig (Ed25519)
verify -> checks the zip hash against SHA256SUMS and the signature against the vendor public key
          (the public key is delivered to the site once, out of band). See docs/UPDATE_PROCEDURE.md.
"""
from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.3.0"
EXCLUDE = {"__pycache__", ".pytest_cache", "data", "dist", "keys", "certs", "node_modules", ".git"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def keygen(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    k = Ed25519PrivateKey.generate()
    (outdir / "release_ed25519.pem").write_bytes(k.private_bytes(serialization.Encoding.PEM,
                                                                  serialization.PrivateFormat.PKCS8,
                                                                  serialization.NoEncryption()))
    (outdir / "release_ed25519.pub").write_bytes(k.public_key().public_bytes(serialization.Encoding.PEM,
                                                                             serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"keys in {outdir} - keep the .pem OFFLINE; give sites only the .pub")


def build(key: Path | None):
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    z = dist / f"sovereign-optimizer-{VERSION}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_file() and not (set(rel.parts) & EXCLUDE) and not p.suffix == ".pyc":
                zf.write(p, f"sovereign-optimizer-{VERSION}/{rel.as_posix()}")
    sums = dist / "SHA256SUMS"
    sums.write_text(f"{sha256(z)}  {z.name}\n")
    if key:
        k = serialization.load_pem_private_key(key.read_bytes(), password=None)
        (dist / "SHA256SUMS.sig").write_bytes(k.sign(sums.read_bytes()))
    print(f"built {z} ({z.stat().st_size / 1e6:.1f} MB){' + signature' if key else ' (UNSIGNED)'}")


def verify(z: Path, pub: Path) -> int:
    sums = z.parent / "SHA256SUMS"
    sig = z.parent / "SHA256SUMS.sig"
    pk = serialization.load_pem_public_key(pub.read_bytes())
    try:
        pk.verify(sig.read_bytes(), sums.read_bytes())
    except (InvalidSignature, FileNotFoundError) as e:
        print(f"SIGNATURE INVALID - do not install ({type(e).__name__})")
        return 1
    expected = {line.split()[1]: line.split()[0] for line in sums.read_text().splitlines() if line.strip()}
    if expected.get(z.name) != sha256(z):
        print("HASH MISMATCH - package altered in transit, do not install")
        return 1
    print(f"OK: {z.name} is signed by the vendor key and intact")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen"); k.add_argument("outdir")
    b = sub.add_parser("build"); b.add_argument("--key")
    v = sub.add_parser("verify"); v.add_argument("zip"); v.add_argument("--pub", required=True)
    a = ap.parse_args()
    if a.cmd == "keygen":
        keygen(Path(a.outdir))
    elif a.cmd == "build":
        build(Path(a.key) if a.key else None)
    else:
        raise SystemExit(verify(Path(a.zip), Path(a.pub)))
