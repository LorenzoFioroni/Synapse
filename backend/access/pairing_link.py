"""``python server.py --pairing-link``: a one-time pairing link from the terminal.

On a server you reach over SSH there is no browser to open Settings → Devices in. This asks the
Synapse server already running on this computer for a pairing code, the same way Settings does: the
request comes from this computer itself (loopback, no proxy headers), so it is allowed. The link it
prints works once, for a few minutes.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request


class PairingLinkError(Exception):
    pass


def request_pairing(port: int, *, tls: bool = False, timeout: float = 10) -> dict:
    """Ask the server on this computer for a pairing code. Returns ``{"expires", "links": [...]}``."""
    url = f"{'https' if tls else 'http'}://127.0.0.1:{port}/api/devices/pairing"
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    # Our own server on loopback: its certificate is made for its network name, not 127.0.0.1.
    context = ssl._create_unverified_context() if tls else None  # noqa: S323
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read().decode("utf-8")).get("error") or str(e)
        except ValueError:
            message = str(e)
        raise PairingLinkError(message) from e
    except (urllib.error.URLError, OSError) as e:
        raise PairingLinkError(
            f"Synapse is not running on port {port} of this computer. Start it first (in another terminal "
            "or as a service), then run this again; give the same --port (and --cert) as the server."
        ) from e


def describe(res: dict, now: float | None = None) -> str:
    """The text printed for a pairing answer: the first link (the public address when there is one)
    with a QR code, then any other addresses."""
    links = res.get("links") or []
    if not links:
        return "Synapse made a code but knows no address other devices can reach. Start it with --public-url or --lan."
    minutes = max(1, round((float(res.get("expires") or 0) - (now if now is not None else time.time())) / 60))
    first, rest = links[0], links[1:]
    lines = [
        f"Open this link on the device to pair it (it works once, for {minutes} minutes):",
        "",
        f"  {first['url']}",
        "",
    ]
    qr = terminal_qr(first["url"])
    if qr:
        lines += [qr, ""]
    if rest:
        lines.append("Other addresses: " + "  ".join(link["url"] for link in rest))
    lines.append(
        "Then, on that device: Settings → Devices → Add a Passkey, so it (and your other devices) can sign in."
    )
    return "\n".join(lines)


def terminal_qr(text: str) -> str | None:
    try:
        import io

        import segno
    except ImportError:
        return None
    buf = io.StringIO()
    segno.make(text, error="m").terminal(out=buf, compact=True)
    return buf.getvalue().rstrip("\n")
