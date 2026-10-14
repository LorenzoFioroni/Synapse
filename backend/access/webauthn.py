"""Checking what a browser's WebAuthn calls return (passkeys), with no network and no account.

Only what Synapse needs from the standard (https://www.w3.org/TR/webauthn-2/):

* registration (``navigator.credentials.create``) with ``attestation: "none"``: the attestation
  statement is not checked, because there is no list of trusted authenticator makers to check it
  against. A passkey is only added by someone already signed in, so it proves nothing anyway.
* sign-in (``navigator.credentials.get``): the signature over the authenticator data and the
  client data, with the public key saved at registration.

The public key arrives as a COSE key inside CBOR, so this module has a small CBOR decoder for the
subset WebAuthn uses (definite lengths only). Signatures are checked with the ``cryptography``
package (ES256, EdDSA and RS256), imported only when needed.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

ES256, EDDSA, RS256 = -7, -8, -257
ALGORITHMS = (ES256, EDDSA, RS256)  # what Synapse asks authenticators for, preferred first

# authenticator data flags
USER_PRESENT, USER_VERIFIED, ATTESTED_DATA = 0x01, 0x04, 0x40


class WebAuthnError(Exception):
    """The browser's answer does not check out; the message can be shown to the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ----------------------------------------------------------------------------- encoding
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_b64url(text: Any) -> bytes:
    if not isinstance(text, str):
        raise WebAuthnError("The passkey answer is incomplete.")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as e:
        raise WebAuthnError("The passkey answer is not readable.") from e


def cbor_decode(data: bytes) -> tuple[Any, int]:
    """The first CBOR item in ``data`` and where it ends."""
    return _cbor_item(data, 0, 0)


def _cbor_item(b: bytes, i: int, depth: int) -> tuple[Any, int]:
    if depth > 16 or i >= len(b):
        raise WebAuthnError("The passkey data is not valid.")
    major, info = b[i] >> 5, b[i] & 31
    i += 1
    if info < 24:
        n = info
    elif info <= 27:
        size = 1 << (info - 24)
        if i + size > len(b):
            raise WebAuthnError("The passkey data is not valid.")
        n = int.from_bytes(b[i : i + size], "big")
        i += size
    else:
        raise WebAuthnError("The passkey data is not valid.")  # indefinite lengths: not in WebAuthn
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major in (2, 3):
        if i + n > len(b):
            raise WebAuthnError("The passkey data is not valid.")
        chunk = b[i : i + n]
        return (bytes(chunk) if major == 2 else chunk.decode("utf-8", "replace")), i + n
    if major == 4:
        items = []
        for _ in range(n):
            v, i = _cbor_item(b, i, depth + 1)
            items.append(v)
        return items, i
    if major == 5:
        out = {}
        for _ in range(n):
            k, i = _cbor_item(b, i, depth + 1)
            v, i = _cbor_item(b, i, depth + 1)
            if isinstance(k, (list, dict)):
                raise WebAuthnError("The passkey data is not valid.")
            out[k] = v
        return out, i
    if major == 6:  # a tag: keep the value
        return _cbor_item(b, i, depth + 1)
    if info < 24 and n in (20, 21, 22, 23):
        return {20: False, 21: True, 22: None, 23: None}[n], i
    raise WebAuthnError("The passkey data is not valid.")


# ----------------------------------------------------------------------------- keys
def _crypto():
    try:
        from cryptography import exceptions
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    except ImportError as e:
        raise WebAuthnError(
            "Passkeys need the cryptography package. Run  pip install cryptography  and restart Synapse.", 501
        ) from e
    return exceptions, hashes, serialization, ec, ed25519, padding, rsa


def cose_to_der(cose: Any) -> tuple[bytes, int]:
    """A COSE public key → (SubjectPublicKeyInfo DER, COSE algorithm)."""
    _, _, serialization, ec, ed25519, _, rsa = _crypto()
    if not isinstance(cose, dict):
        raise WebAuthnError("The passkey's public key is missing.")
    kty, alg = cose.get(1), cose.get(3)
    key: Any
    try:
        if kty == 2 and alg == ES256 and cose.get(-1) == 1:  # EC2 on P-256
            x, y = cose[-2], cose[-3]
            key = ec.EllipticCurvePublicNumbers(
                int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
            ).public_key()
        elif kty == 1 and alg == EDDSA and cose.get(-1) == 6:  # OKP, Ed25519
            key = ed25519.Ed25519PublicKey.from_public_bytes(cose[-2])
        elif kty == 3 and alg == RS256:
            key = rsa.RSAPublicNumbers(int.from_bytes(cose[-2], "big"), int.from_bytes(cose[-1], "big")).public_key()
        else:
            raise WebAuthnError("This passkey uses a kind of key Synapse does not support.")
    except (KeyError, TypeError, ValueError) as e:
        raise WebAuthnError("The passkey's public key is not valid.") from e
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return der, alg


def verify_signature(public_key_der: bytes, alg: int, signature: bytes, data: bytes) -> bool:
    exceptions, hashes, serialization, ec, _, padding, _ = _crypto()
    try:
        key: Any = serialization.load_der_public_key(public_key_der)
        if alg == ES256:
            key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        elif alg == EDDSA:
            key.verify(signature, data)
        elif alg == RS256:
            key.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False
    except (exceptions.InvalidSignature, ValueError, TypeError):
        return False
    return True


# ----------------------------------------------------------------------------- ceremonies
@dataclass
class AuthData:
    rp_id_hash: bytes
    flags: int
    sign_count: int
    credential_id: bytes | None = None
    public_key: Any = None  # the COSE key, when the authenticator added one (registration)


def parse_auth_data(ad: bytes) -> AuthData:
    if len(ad) < 37:
        raise WebAuthnError("The passkey data is not valid.")
    out = AuthData(ad[:32], ad[32], int.from_bytes(ad[33:37], "big"))
    if out.flags & ATTESTED_DATA:
        if len(ad) < 55:
            raise WebAuthnError("The passkey data is not valid.")
        n = int.from_bytes(ad[53:55], "big")
        if len(ad) < 55 + n:
            raise WebAuthnError("The passkey data is not valid.")
        out.credential_id = ad[55 : 55 + n]
        out.public_key, _ = cbor_decode(ad[55 + n :])
    return out


def client_data(raw: bytes, kind: str, origin: str) -> dict:
    """The parsed client data, checked for its type and origin. The caller checks the challenge."""
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise WebAuthnError("The passkey answer is not readable.") from e
    if not isinstance(data, dict) or data.get("type") != kind:
        raise WebAuthnError("The passkey answer is not for this request.")
    if data.get("origin") != origin or data.get("crossOrigin") is True:
        raise WebAuthnError("The passkey answer came from another site.")
    if not isinstance(data.get("challenge"), str):
        raise WebAuthnError("The passkey answer is incomplete.")
    return data


def check_auth_data(ad: AuthData, rp_id: str) -> None:
    if ad.rp_id_hash != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise WebAuthnError("This passkey belongs to another site.")
    if not ad.flags & USER_PRESENT:
        raise WebAuthnError("The passkey was not confirmed on the device.")


@dataclass
class NewCredential:
    credential_id: bytes
    public_key: bytes  # SubjectPublicKeyInfo DER
    alg: int
    sign_count: int


def verify_registration(response: dict, rp_id: str, origin: str) -> tuple[str, NewCredential]:
    """Check a ``credentials.create`` answer. Returns (its challenge, the new credential)."""
    raw_client = from_b64url(response.get("clientDataJSON"))
    challenge = client_data(raw_client, "webauthn.create", origin)["challenge"]
    att, _ = cbor_decode(from_b64url(response.get("attestationObject")))
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise WebAuthnError("The passkey answer is incomplete.")
    ad = parse_auth_data(att["authData"])
    check_auth_data(ad, rp_id)
    if ad.credential_id is None or ad.public_key is None:
        raise WebAuthnError("The passkey answer is incomplete.")
    if response.get("id") is not None and from_b64url(response.get("id")) != ad.credential_id:
        raise WebAuthnError("The passkey answer is not consistent.")
    der, alg = cose_to_der(ad.public_key)
    return challenge, NewCredential(ad.credential_id, der, alg, ad.sign_count)


@dataclass
class Assertion:
    challenge: str
    raw_client: bytes
    raw_auth: bytes
    auth: AuthData
    signature: bytes


def read_assertion(response: dict, origin: str) -> Assertion:
    """Parse a ``credentials.get`` answer and check its client data; the signature is checked by
    ``verify_assertion`` once the passkey it names is known."""
    raw_client = from_b64url(response.get("clientDataJSON"))
    challenge = client_data(raw_client, "webauthn.get", origin)["challenge"]
    raw_auth = from_b64url(response.get("authenticatorData"))
    return Assertion(challenge, raw_client, raw_auth, parse_auth_data(raw_auth), from_b64url(response.get("signature")))


def verify_assertion(a: Assertion, rp_id: str, public_key: bytes, alg: int, stored_count: int) -> int:
    """Check the signature with the saved key. Returns the new signature counter."""
    check_auth_data(a.auth, rp_id)
    signed = a.raw_auth + hashlib.sha256(a.raw_client).digest()
    if not verify_signature(public_key, alg, a.signature, signed):
        raise WebAuthnError("The passkey's signature is not valid.", 401)
    # A counter that does not go up means a copied authenticator. Synced passkeys always say 0.
    if (a.auth.sign_count or stored_count) and a.auth.sign_count <= stored_count:
        raise WebAuthnError(
            "This passkey's counter went backwards; it may have been copied. Remove it and add it again.", 401
        )
    return a.auth.sign_count
