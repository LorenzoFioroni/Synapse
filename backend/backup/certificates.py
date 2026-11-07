"""Validate uploaded trust bundles before they become part of a backup connection."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from .errors import BackupError

MAX_CA_BYTES = 1024 * 1024


@dataclass(frozen=True)
class TrustedCertificate:
    pem: str
    sha256: str
    count: int

    @property
    def summary(self) -> dict:
        return {"sha256": self.sha256, "count": self.count}


def parse_ca_certificate(value: object) -> TrustedCertificate:
    if not isinstance(value, str) or not value or len(value) > MAX_CA_BYTES:
        raise BackupError("Upload a PEM certificate or certificate bundle up to 1 MiB.")
    try:
        encoded = value.encode("ascii")
        # Only certificates belong in a trust bundle; in particular, never retain private keys.
        if re.search(r"-----BEGIN (?!CERTIFICATE-----)", value):
            raise ValueError
        certificates = x509.load_pem_x509_certificates(encoded)
        pem = b"".join(cert.public_bytes(Encoding.PEM) for cert in certificates)
    except (ValueError, UnicodeError) as e:
        raise BackupError("The file must contain valid PEM certificates only, without private keys.") from e
    return TrustedCertificate(pem.decode("ascii"), hashlib.sha256(pem).hexdigest(), len(certificates))
