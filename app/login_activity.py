"""
Real (best-effort, honest) answers to "who's logging in and from
where" — powers the Settings page's "Recent sign-ins" list.

Two deliberately separate concerns:

  - parse_device(user_agent): synchronous, local, coarse regex-based
    OS/browser detection. No network call involved, so it can't fail
    slowly or at all in a way that matters.

  - resolve_location(ip_address): a real external IP-geolocation call
    (ip-api.com's free, keyless, non-commercial-use endpoint), wrapped
    in a short timeout and broad exception handling. Private/loopback
    IPs (the overwhelming majority of logins during local development)
    are recognized locally and skipped entirely — there's no point
    spending a network call and a timeout window geolocating
    127.0.0.1.

If resolve_location can't produce a real answer for any reason, it
returns None rather than a fabricated guess — same honesty principle
orchestration.py already applies to citations/confidence. The caller
(routers/auth.py) stores that as a null location_label, and the
Settings page shows "Unknown location," not a guess dressed up as fact.

This is also why the actual lookup runs as a FastAPI BackgroundTask,
not inline in the login request: a third-party HTTP call has no
business adding latency (or a new failure mode) to the login path
itself. Login succeeds and returns a token immediately; the location
fills in a moment later, or not at all.
"""

import ipaddress

import httpx

GEO_LOOKUP_TIMEOUT_SECONDS = 2.5
GEO_LOOKUP_URL = "http://ip-api.com/json/{ip}"


def parse_device(user_agent: str | None) -> str:
    if not user_agent:
        return "Unknown device"

    ua = user_agent

    if "iPhone" in ua:
        os_label = "iPhone"
    elif "iPad" in ua:
        os_label = "iPad"
    elif "Android" in ua:
        os_label = "Android"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_label = "Mac"
    elif "Windows" in ua:
        os_label = "Windows"
    elif "Linux" in ua:
        os_label = "Linux"
    else:
        os_label = "Unknown OS"

    # Order matters: Edge and Opera both contain "Chrome" in their real
    # UA string, and Chrome's UA also contains "Safari" — so the more
    # specific/distinguishing tokens have to be checked first, or every
    # non-Chrome, non-Firefox browser would misreport as Chrome.
    if "Edg/" in ua:
        browser_label = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser_label = "Opera"
    elif "Firefox/" in ua:
        browser_label = "Firefox"
    elif "Chrome/" in ua:
        browser_label = "Chrome"
    elif "Safari/" in ua:
        browser_label = "Safari"
    else:
        browser_label = "Unknown browser"

    return f"{browser_label} on {os_label}"


def get_client_ip(headers: dict, fallback: str | None) -> str:
    """Render (like most hosts) sits behind a proxy, so the connecting
    socket's address is the proxy's, not the real visitor's — the real
    client IP is in X-Forwarded-For, first entry, if present."""
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return fallback or "unknown"


def _is_private_or_unresolvable(ip_address: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_address)
    except ValueError:
        return True  # not even a parseable IP — nothing to look up
    return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local


def resolve_location(ip_address: str) -> str | None:
    if not ip_address or _is_private_or_unresolvable(ip_address):
        return None

    try:
        response = httpx.get(
            GEO_LOOKUP_URL.format(ip=ip_address),
            params={"fields": "status,city,regionName,country"},
            timeout=GEO_LOOKUP_TIMEOUT_SECONDS,
        )
        data = response.json()
    except Exception:
        return None

    if data.get("status") != "success":
        return None

    parts = [p for p in [data.get("city"), data.get("regionName"), data.get("country")] if p]
    return ", ".join(parts) if parts else None
