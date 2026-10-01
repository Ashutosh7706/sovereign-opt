"""Create a TLS certificate for LAN/pilot deployment (audit #22). Run from repo root:

    python backend/scripts/make_cert.py --host sovereign.plant.local --ip 10.0.0.5

Writes certs/server.key (owner-only) and certs/server.crt, self-signed for 825 days. For a site
with an internal CA, create a CSR instead (--csr) and have plant IT sign it. Then start with
run.ps1 -Https / run.sh --https (sets SOVEREIGN_HTTPS=1 -> Secure cookies + HSTS).
"""
import argparse
import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ap = argparse.ArgumentParser()
ap.add_argument("--host", action="append", default=[])
ap.add_argument("--ip", action="append", default=[])
ap.add_argument("--out", default="certs")
ap.add_argument("--csr", action="store_true", help="write a CSR for the plant CA instead of self-signing")
a = ap.parse_args()
hosts = a.host or ["localhost"]
ips = a.ip or ["127.0.0.1"]
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
key = ec.generate_private_key(ec.SECP256R1())
(out / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
try:
    os.chmod(out / "server.key", 0o600)
except OSError:
    pass
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0]),
                  x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Sovereign Optimizer (on-prem)")])
san = x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips])
if a.csr:
    csr = x509.CertificateSigningRequestBuilder().subject_name(name).add_extension(san, False).sign(key, hashes.SHA256())
    (out / "server.csr").write_bytes(csr.public_bytes(serialization.Encoding.PEM))
    print(f"wrote {out/'server.key'} and {out/'server.csr'} - send the CSR to the plant CA")
else:
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=825)).add_extension(san, False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True).sign(key, hashes.SHA256()))
    (out / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"wrote {out/'server.key'} and {out/'server.crt'} for {hosts + ips}")
