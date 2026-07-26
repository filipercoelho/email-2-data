#!/usr/bin/env python3
"""Container HEALTHCHECK probe — scheme-agnostic (ADR-039).

The image previously probed ``http://127.0.0.1:8042/healthz`` from an inline one-liner. Once TLS
became an option that probe would fail against an HTTPS listener, marking the container unhealthy —
and because ``intake-bot`` declares ``depends_on: email2data healthy``, an unhealthy webapp also
stops the Telegram worker. A wrong healthcheck would therefore have taken down a service that has
nothing to do with TLS.

So: try HTTPS first, fall back to HTTP, and exit 0 if either answers 200.

Certificate verification is deliberately disabled. The deployment uses a self-signed certificate
(bin/make-cert.sh) and this probe runs *inside* the container against 127.0.0.1 — there is no
meaningful identity to verify against loopback, and requiring a trusted chain here would only make
the healthcheck fail for a reason unrelated to the app's health.

``/healthz`` is exempt from the auth gate for exactly this reason; ``tests/test_auth_gate.py``
pins that so it cannot regress into a 401.
"""

from __future__ import annotations

import os
import ssl
import sys
import urllib.error
import urllib.request

PORT = os.getenv("EMAIL2DATA_PORT", "8042")
TIMEOUT = float(os.getenv("EMAIL2DATA_HEALTHCHECK_TIMEOUT", "3"))


def _probe(url: str, context: ssl.SSLContext | None) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT, context=context) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main() -> int:
    unverified = ssl._create_unverified_context()
    for url, context in ((f"https://127.0.0.1:{PORT}/healthz", unverified),
                         (f"http://127.0.0.1:{PORT}/healthz", None)):
        if _probe(url, context):
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
