"""Create a seven-day, local-only development certificate for an iPhone trial."""

import argparse
import ipaddress
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, type=ipaddress.IPv4Address)
    args = parser.parse_args()
    if not args.ip.is_private or args.ip.is_loopback:
        parser.error("Use this computer's private LAN IPv4 address.")
    root = Path(".local/live-camera")
    tls = root / "tls"
    tls.mkdir(parents=True, exist_ok=True)
    if any(tls.iterdir()):
        parser.error(
            "Certificates already exist; keep them or remove the tls folder first."
        )
    os.chmod(tls, 0o700)

    def openssl(*arguments: str) -> None:
        subprocess.run(
            ["openssl", *arguments],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            "7",
            "-subj",
            "/CN=VisitAdvisor local camera trial",
            "-addext",
            "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            str(tls / "ca.key"),
            "-out",
            str(tls / "ca.pem"),
        )
        openssl(
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-subj",
            "/CN=VisitAdvisor camera",
            "-keyout",
            str(tls / "server.key"),
            "-out",
            str(tls / "server.csr"),
        )
        (tls / "extensions.cnf").write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            f"subjectAltName=IP:{args.ip},IP:127.0.0.1,DNS:localhost\n"
        )
        openssl(
            "x509",
            "-req",
            "-in",
            str(tls / "server.csr"),
            "-CA",
            str(tls / "ca.pem"),
            "-CAkey",
            str(tls / "ca.key"),
            "-CAcreateserial",
            "-days",
            "7",
            "-sha256",
            "-extfile",
            str(tls / "extensions.cnf"),
            "-out",
            str(tls / "server.pem"),
        )
        openssl(
            "x509",
            "-in",
            str(tls / "ca.pem"),
            "-outform",
            "DER",
            "-out",
            str(root / "phone-ca.cer"),
        )
        os.chmod(tls / "server.key", 0o600)
        # No further certificates need signing. Retain only the server private key.
        (tls / "ca.key").unlink()
    except (OSError, subprocess.CalledProcessError):
        parser.exit(
            1, "Certificate preparation failed. Check local OpenSSL installation.\n"
        )
    print(f"Phone setup: http://{args.ip}:8002/phone-setup.html")
    print(f"Camera: https://{args.ip}:8443/")


if __name__ == "__main__":
    main()
