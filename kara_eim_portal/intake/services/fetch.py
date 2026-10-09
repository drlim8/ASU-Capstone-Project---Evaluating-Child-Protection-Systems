"""Guarded URL fetching for batch intake.

Security-sensitive: fetches user-supplied URLs server-side. Every hop
(including redirects) is checked against an SSRF guard. This module
deliberately does not import Django; callers pass settings values in.

Known residual risk: DNS rebinding between the address check and the
actual connect (requests re-resolves the name). Mitigate at the network
layer (egress filtering) if the deployment needs stronger guarantees.
"""
import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import urljoin, urlparse

import requests
import urllib3

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
CHUNK_SIZE = 64 * 1024
# Bot-protection services answer automated clients with a challenge page instead of content.
CHALLENGE_HEADERS = {"x-amzn-waf-action": "challenge", "cf-mitigated": "challenge"}
BOT_BLOCKED_MESSAGE = (
    "This site blocked the automated download (bot protection). "
    "Open the link in your browser, save the PDF or page, and upload it instead."
)
EMPTY_RESPONSE_MESSAGE = "The server returned an empty response."


def _is_bot_challenge(status: int, headers) -> bool:
    lowered = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    if any(lowered.get(name) == value for name, value in CHALLENGE_HEADERS.items()):
        return True
    # 202 Accepted means "not ready yet" — never a finished document.
    return status == 202


class FetchError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, http_status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.http_status = http_status


class BlockedURLError(FetchError):
    def __init__(self, message: str, *, http_status: int | None = None):
        super().__init__(message, retryable=False, http_status=http_status)


@dataclass
class FetchResult:
    final_url: str
    http_status: int
    content_type: str
    size_bytes: int


def _is_blocked_ip(ip) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


def assert_public_url(url: str) -> None:
    """Raise BlockedURLError unless url is http(s) and every resolved address is public."""
    if any(ch == "\\" or ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise BlockedURLError("URL contains forbidden characters")
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise BlockedURLError("Malformed URL") from exc
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError(f"Unsupported URL scheme: {parsed.scheme!r}")
    if not host:
        raise BlockedURLError("URL has no host")
    # Parser-mismatch defence: urllib3 (used by requests) must agree on the host.
    try:
        other = urllib3.util.parse_url(url).host
    except Exception as exc:
        raise BlockedURLError("Malformed URL") from exc
    if (other or "").strip("[]").lower() != host.lower():
        raise BlockedURLError("URL host is ambiguous")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except UnicodeError as exc:
        raise BlockedURLError(f"Invalid host name {host!r}") from exc
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve host {host!r}", retryable=True) from exc
    if not infos:
        raise FetchError(f"Could not resolve host {host!r}", retryable=True)
    for info in infos:
        address = info[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise BlockedURLError(f"Unparseable address for {host!r}")
        if _is_blocked_ip(ip):
            raise BlockedURLError(f"Host {host!r} resolves to a non-public address")


def fetch_to_file(
    url: str,
    dest: BinaryIO,
    *,
    max_bytes: int,
    timeout: float,
    user_agent: str,
    max_redirects: int = 5,
    session: requests.Session | None = None,
) -> FetchResult:
    session = session or requests.Session()
    current = url
    for hop in range(max_redirects + 1):
        try:
            current = requests.Request("GET", current).prepare().url
        except (requests.RequestException, ValueError) as exc:
            raise BlockedURLError("Malformed URL") from exc
        assert_public_url(current)
        try:
            response = session.get(
                current,
                headers={"User-Agent": user_agent},
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise FetchError(f"Network error fetching URL: {exc}", retryable=True) from exc
        except requests.RequestException as exc:
            raise FetchError(f"Request failed: {exc}", retryable=False) from exc

        try:
            status = response.status_code
            if status in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                if not location:
                    raise FetchError("Redirect without Location header", http_status=status)
                if hop >= max_redirects:
                    raise FetchError("Too many redirects", http_status=status)
                try:
                    current = urljoin(current, location)
                except ValueError as exc:
                    raise BlockedURLError("Malformed redirect URL") from exc
                continue
            if status >= 500:
                raise FetchError(f"Server error {status}", retryable=True, http_status=status)
            if status >= 300:
                raise FetchError(f"HTTP {status}", retryable=False, http_status=status)
            if _is_bot_challenge(status, response.headers):
                raise FetchError(BOT_BLOCKED_MESSAGE, http_status=status)

            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except (TypeError, ValueError):
                    declared = None
                if declared is not None and declared > max_bytes:
                    raise FetchError(f"File exceeds size limit of {max_bytes} bytes", http_status=status)

            written = 0
            try:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise FetchError(
                            f"File exceeds size limit of {max_bytes} bytes", http_status=status
                        )
                    dest.write(chunk)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
                raise FetchError(f"Network error while downloading: {exc}", retryable=True) from exc
            except requests.RequestException as exc:
                raise FetchError(f"Download failed: {exc}", retryable=False) from exc
            if written == 0:
                raise FetchError(EMPTY_RESPONSE_MESSAGE, http_status=status)
            return FetchResult(
                final_url=current,
                http_status=status,
                content_type=response.headers.get("Content-Type", ""),
                size_bytes=written,
            )
        finally:
            response.close()
    raise FetchError("Too many redirects")  # pragma: no cover


def fetch_bytes(url: str, *, max_bytes: int, timeout: float, user_agent: str) -> tuple[bytes, str]:
    from io import BytesIO

    buf = BytesIO()
    result = fetch_to_file(
        url, buf, max_bytes=max_bytes, timeout=timeout, user_agent=user_agent
    )
    return buf.getvalue(), result.content_type


class HostThrottle:
    """Ensures consecutive requests to the same hostname are at least `delay` seconds apart."""

    def __init__(self, delay: float, clock=time.monotonic, sleep=time.sleep):
        self.delay = delay
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        with self._lock:
            last = self._last.get(host)
            if last is not None:
                remaining = self.delay - (self._clock() - last)
                if remaining > 0:
                    self._sleep(remaining)
            self._last[host] = self._clock()
