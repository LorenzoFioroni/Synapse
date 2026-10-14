"""Who may use the server.

``guard.py`` checks every request: it must name the server by an accepted host, come from this
computer or a paired device, and (when it changes something) come from the app's own pages.
Pairing of other devices (``--lan``) is in ``devices.py``, stored hashed in ``auth.db``.
``lan.py`` finds this computer's addresses on the network, for the pairing QR code.
"""
