"""Passkeys: signing a device in without a pairing code from this computer.

Pairing needs the computer running Synapse at hand to show a code. A passkey does not: once one
has been added (Settings → Devices, on this computer or on a device that is already signed in), any
device that has it (synced through iCloud Keychain or Google Password Manager, on a security key,
or a phone nearby) signs in from the "This device isn't paired" page. Signing in makes a new
device, with the same cookie pairing gives (``devices.py``).

Browsers only offer passkeys to pages they consider secure, and tie each passkey to a host *name*
(its relying party ID). So passkeys work only when Synapse is served over HTTPS
(``server.py --cert … --key …``) with a certificate the devices trust, and opened by a name such
as ``mac.local`` rather than an IP address. A passkey made on one name does not work on another.

The public keys are stored in ``auth.db`` (table ``passkeys``); they are not secrets. Challenges
are kept in memory for a few minutes and work once.
"""

from __future__ import annotations

import builtins
import secrets
import time
from collections import OrderedDict
from pathlib import Path

from . import lan, webauthn
from .authdb import AuthTable, now_iso
from .devices import NAME_MAX, Devices
from .webauthn import WebAuthnError, b64url

CHALLENGE_TTL = 5 * 60  # seconds the browser has to answer
MAX_CHALLENGES = 64  # pending at once; the oldest goes first (anyone may ask for a sign-in challenge)
RP_NAME = "Synapse"

SCHEMA = """
CREATE TABLE IF NOT EXISTS passkeys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    credential_id TEXT NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    alg INTEGER NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    rp_id TEXT NOT NULL,
    created TEXT NOT NULL,
    last_used TEXT,
    last_addr TEXT
);
"""

PasskeyError = WebAuthnError


def relying_party(host: str, scheme: str) -> tuple[str, str]:
    """The relying party ID and origin for a page at ``scheme://host``, or PasskeyError (409) saying
    why passkeys do not work there."""
    name = lan.host_name(host)
    if scheme != "https":
        raise PasskeyError(
            "Passkeys need a secure connection (HTTPS): start Synapse with --cert and --key (see the README, "
            "“Signing in with a passkey”) and open it with https://.",
            409,
        )
    if lan.is_ip_literal(name):
        raise PasskeyError(
            "Passkeys need Synapse opened by its name (such as https://your-computer.local), not by an IP address.",
            409,
        )
    return name, f"https://{host.strip().lower()}"


def public(row: dict) -> dict:
    """A passkey as the API shows it."""
    return {k: row.get(k) for k in ("id", "name", "rp_id", "created", "last_used", "last_addr")}


class Passkeys(AuthTable):
    SCHEMA = SCHEMA

    def __init__(self, path: Path, devices: Devices, clock=time.time):
        super().__init__(path)
        self.devices = devices
        self.clock = clock
        # challenge → (what for, relying party ID, origin, passkey name, expiry)
        self._challenges: OrderedDict[str, tuple[str, str, str, str, float]] = OrderedDict()

    # -- challenges ------------------------------------------------------------
    def _challenge(self, purpose: str, rp_id: str, origin: str, name: str = "") -> str:
        challenge = b64url(secrets.token_bytes(32))
        now = self.clock()
        with self._lock:
            for c, entry in list(self._challenges.items()):
                if entry[4] < now:
                    del self._challenges[c]
            while len(self._challenges) >= MAX_CHALLENGES:
                self._challenges.popitem(last=False)
            self._challenges[challenge] = (purpose, rp_id, origin, name, now + CHALLENGE_TTL)
        return challenge

    def _take(self, challenge: str, purpose: str, rp_id: str, origin: str) -> str:
        """Use up a challenge this server gave out for this purpose and site. Returns the name
        given with it."""
        with self._lock:
            entry = self._challenges.pop(challenge, None)
        if entry is None or entry[4] < self.clock():
            raise PasskeyError("This request has expired. Try again.")
        if entry[:3] != (purpose, rp_id, origin):
            raise PasskeyError("The passkey answer is not for this request.")
        return entry[3]

    # -- adding --------------------------------------------------------------
    def registration_options(self, rp_id: str, origin: str, name: str) -> dict:
        """What ``navigator.credentials.create`` needs (binary values as base64url)."""
        name = (name or "").strip()[:NAME_MAX] or "Synapse"
        with self._lock:
            known = self._conn().execute("SELECT credential_id FROM passkeys WHERE rp_id = ?", (rp_id,)).fetchall()
        return {
            "challenge": self._challenge("register", rp_id, origin, name),
            "rp": {"id": rp_id, "name": RP_NAME},
            # A new user handle every time: a password manager replaces a passkey that has the same
            # site and handle, which would silently break the passkey already saved here.
            "user": {"id": b64url(secrets.token_bytes(16)), "name": name, "displayName": name},
            "pubKeyCredParams": [{"type": "public-key", "alg": a} for a in webauthn.ALGORITHMS],
            "authenticatorSelection": {
                "residentKey": "required",  # discoverable: signing in needs no user name
                "requireResidentKey": True,
                "userVerification": "preferred",
            },
            "attestation": "none",
            "excludeCredentials": [{"type": "public-key", "id": r["credential_id"]} for r in known],
            "timeout": CHALLENGE_TTL * 1000,
        }

    def register(self, rp_id: str, origin: str, response: dict, addr: str = "") -> dict:
        challenge, cred = webauthn.verify_registration(response, rp_id, origin)
        name = self._take(challenge, "register", rp_id, origin)
        pk_id = secrets.token_hex(8)
        with self._lock:
            db = self._conn()
            if db.execute("SELECT 1 FROM passkeys WHERE credential_id = ?", (b64url(cred.credential_id),)).fetchone():
                raise PasskeyError("This passkey has already been added.", 409)
            db.execute(
                "INSERT INTO passkeys (id, name, credential_id, public_key, alg, sign_count, rp_id, created, last_addr) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pk_id,
                    name,
                    b64url(cred.credential_id),
                    cred.public_key,
                    cred.alg,
                    cred.sign_count,
                    rp_id,
                    now_iso(),
                    addr,
                ),
            )
            db.commit()
            row = db.execute("SELECT * FROM passkeys WHERE id = ?", (pk_id,)).fetchone()
        return public(dict(row))

    # -- signing in ----------------------------------------------------------
    def login_options(self, rp_id: str, origin: str, purpose: str = "login") -> dict:
        """What ``navigator.credentials.get`` needs. No list of passkeys: the browser offers the
        ones it has for this site, and an unpaired device learns nothing about them. ``purpose`` is
        ``login`` (signing a device in) or ``confirm`` (a signed-in device proving it again before an
        administrative action, see confirm.py)."""
        return {
            "challenge": self._challenge(purpose, rp_id, origin),
            "rpId": rp_id,
            "allowCredentials": [],
            "userVerification": "preferred",
            "timeout": CHALLENGE_TTL * 1000,
        }

    def _verify(self, purpose: str, rp_id: str, origin: str, response: dict, addr: str) -> dict:
        """Check a passkey answer to a challenge given out for ``purpose``; returns the passkey's row."""
        assertion = webauthn.read_assertion(response, origin)
        self._take(assertion.challenge, purpose, rp_id, origin)
        cred_id = b64url(webauthn.from_b64url(response.get("id")))
        with self._lock:
            row = (
                self._conn()
                .execute("SELECT * FROM passkeys WHERE credential_id = ? AND rp_id = ?", (cred_id, rp_id))
                .fetchone()
            )
        if row is None:
            raise PasskeyError(
                "Synapse does not know this passkey. It may have been removed, or made for another address.", 401
            )
        count = webauthn.verify_assertion(assertion, rp_id, row["public_key"], row["alg"], row["sign_count"])
        with self._lock:
            self._conn().execute(
                "UPDATE passkeys SET sign_count = ?, last_used = ?, last_addr = ? WHERE id = ?",
                (count, now_iso(), addr, row["id"]),
            )
            self._conn().commit()
        return dict(row)

    def sign_in(
        self, rp_id: str, origin: str, response: dict, name: str, user_agent: str, addr: str
    ) -> tuple[dict, str, dict]:
        """Check a passkey sign-in and make a device for it. Returns (device, token, passkey)."""
        row = self._verify("login", rp_id, origin, response, addr)
        device, token = self.devices.add(name, user_agent, addr)
        return device, token, public(row)

    def confirm(self, rp_id: str, origin: str, response: dict, addr: str) -> dict:
        """Check a confirmation (see ``login_options``). Returns the passkey used."""
        return public(self._verify("confirm", rp_id, origin, response, addr))

    def count(self, rp_id: str) -> int:
        """How many passkeys work at this site."""
        with self._lock:
            return self._conn().execute("SELECT COUNT(*) FROM passkeys WHERE rp_id = ?", (rp_id,)).fetchone()[0]

    # -- managing ------------------------------------------------------------
    def list(self) -> builtins.list[dict]:
        with self._lock:
            rows = self._conn().execute("SELECT * FROM passkeys ORDER BY created").fetchall()
        return [public(dict(r)) for r in rows]

    def remove(self, pk_id: str) -> bool:
        with self._lock:
            cur = self._conn().execute("DELETE FROM passkeys WHERE id = ?", (pk_id,))
            self._conn().commit()
            return cur.rowcount > 0
