"""Security boundaries shared by Marven's web and filesystem features."""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional, Tuple, Union
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit


class SecurityValidationError(ValueError):
    """Raised when untrusted input crosses a configured security boundary."""


class _ReadableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self.title_parts = []
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif tag == "title" and self._ignored_depth == 0:
            self._in_title = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._ignored_depth or not data:
            return
        self.text_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)


def extract_readable_text(html: object, max_chars: int = 100_000) -> str:
    """Extract readable HTML text without a backtracking or filtering regex."""
    if not isinstance(html, str) or not html:
        return ""
    parser = _ReadableHTMLParser()
    parser.feed(html)
    parser.close()
    title = " ".join(" ".join(parser.title_parts).split())
    body = " ".join(" ".join(parser.text_parts).split())
    text = (f"Title: {title}\n\n" + body) if title else body
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated]..."
    return text


def extract_fenced_updates(text: object) -> Iterator[Tuple[str, str]]:
    """Parse ``file:`` and ``path=`` code fences in linear time."""
    lines = text.splitlines() if isinstance(text, str) else []
    index = 0
    while index < len(lines):
        opening = lines[index].strip()
        index += 1
        if not opening.startswith("```"):
            continue
        header = opening[3:].strip()
        lower_header = header.lower()
        rel = None
        for marker in ("file:", "file=", "path:", "path="):
            marker_index = lower_header.find(marker)
            if marker_index >= 0:
                candidate = header[marker_index + len(marker):].strip()
                if candidate and not any(ch.isspace() or ch == "`" for ch in candidate):
                    rel = candidate
                break
        if rel is None:
            continue
        content_lines = []
        while index < len(lines) and lines[index].strip() != "```":
            content_lines.append(lines[index])
            index += 1
        if index < len(lines):
            index += 1
            yield rel, "\n".join(content_lines)


def validate_identifier(value: object, field: str = "identifier", max_length: int = 128) -> str:
    """Return a path-safe identifier containing only ASCII letters, digits, `_`, or `-`."""
    if not isinstance(value, str):
        raise SecurityValidationError(f"Invalid {field}")
    candidate = value.strip()
    if not candidate or len(candidate) > max_length:
        raise SecurityValidationError(f"Invalid {field}")
    if not all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in candidate):
        raise SecurityValidationError(f"Invalid {field}")
    return candidate


def resolve_path_within(root: Union[str, Path], user_path: Union[str, Path]) -> Path:
    """Resolve ``user_path`` and require it to remain inside ``root``.

    Absolute paths are accepted only when they already point inside the root. Resolving
    before the containment check also prevents escapes through existing symlinks.
    """
    root_string = os.path.realpath(os.path.abspath(os.fspath(root)))
    raw = os.fspath(user_path)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise SecurityValidationError("Invalid path")
    candidate = os.path.realpath(os.path.abspath(os.path.join(root_string, raw)))
    if candidate == root_string:
        return Path(root_string)
    root_prefix = root_string if root_string.endswith(os.sep) else root_string + os.sep
    if not candidate.startswith(root_prefix):
        raise SecurityValidationError("Path outside allowed directory")
    return Path(candidate)


def _host_header(host: str, port: int, scheme: str) -> str:
    display_host = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    return display_host if port == default_port else f"{display_host}:{port}"


def _validated_url(url: object) -> Tuple[SplitResult, Tuple[str, ...]]:
    if not isinstance(url, str) or not url.strip() or len(url) > 8_192:
        raise SecurityValidationError("Invalid URL")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise SecurityValidationError("Invalid URL") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise SecurityValidationError("Only HTTP and HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise SecurityValidationError("URL credentials are not allowed")
    port = port or (443 if scheme == "https" else 80)
    if port not in {80, 443}:
        raise SecurityValidationError("Only standard HTTP and HTTPS ports are allowed")

    try:
        host = parsed.hostname.encode("idna").decode("ascii")
        address_info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise SecurityValidationError("URL host could not be resolved") from exc

    addresses = tuple(dict.fromkeys(item[4][0] for item in address_info))
    if not addresses:
        raise SecurityValidationError("URL host could not be resolved")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise SecurityValidationError("URL host resolved to an invalid address") from exc
        if not ip.is_global:
            raise SecurityValidationError("Private or non-public network addresses are not allowed")

    normalized = SplitResult(scheme, parsed.netloc, parsed.path, parsed.query, "")
    return normalized, addresses


class PublicHTTPResponse:
    """Small context-managed response wrapper that owns its pinned connection."""

    def __init__(self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse, url: str):
        self._connection = connection
        self._response = response
        self.url = url
        self.status = response.status
        self.headers = response.headers

    def read(self, amount: Optional[int] = None) -> bytes:
        return self._response.read(amount)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()

    def __enter__(self) -> "PublicHTTPResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _open_pinned(
    parsed: SplitResult,
    addresses: Iterable[str],
    method: str,
    headers: Dict[str, str],
    timeout: float,
) -> Tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    last_error: Optional[OSError] = None

    for address in addresses:
        connection = http.client.HTTPConnection(host, port=port, timeout=timeout)
        try:
            raw_socket = socket.create_connection((address, port), timeout=timeout)
            if parsed.scheme == "https":
                context = ssl.create_default_context()
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                connection.sock = context.wrap_socket(raw_socket, server_hostname=host)
            else:
                connection.sock = raw_socket
            request_target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            safe_headers = {
                key: value
                for key, value in headers.items()
                if key.lower() not in {"host", "connection", "content-length", "transfer-encoding"}
            }
            safe_headers["Host"] = _host_header(host, port, parsed.scheme)
            safe_headers["Connection"] = "close"
            connection.request(method, request_target, headers=safe_headers)
            return connection, connection.getresponse()
        except OSError as exc:
            last_error = exc
            connection.close()

    if last_error is not None:
        raise last_error
    raise OSError("No public address was available")


def open_public_http_url(
    url: object,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 20,
    max_redirects: int = 5,
) -> PublicHTTPResponse:
    """Open a public HTTP(S) URL while blocking SSRF and DNS-rebinding escapes.

    Every redirect target is revalidated, and the connection is made to the exact IP
    address that was checked rather than performing a second DNS lookup.
    """
    request_method = method.upper()
    if request_method not in {"GET", "HEAD"}:
        raise SecurityValidationError("Unsupported HTTP method")
    if not isinstance(max_redirects, int) or max_redirects < 0 or max_redirects > 10:
        raise SecurityValidationError("Invalid redirect limit")

    current_url = str(url)
    request_headers = dict(headers or {})
    for redirect_count in range(max_redirects + 1):
        parsed, addresses = _validated_url(current_url)
        connection, response = _open_pinned(parsed, addresses, request_method, request_headers, timeout)
        normalized_url = urlunsplit(parsed)
        if response.status not in {301, 302, 303, 307, 308}:
            return PublicHTTPResponse(connection, response, normalized_url)

        location = response.headers.get("Location")
        response.close()
        connection.close()
        if not location:
            raise SecurityValidationError("Redirect response did not include a location")
        if redirect_count == max_redirects:
            raise SecurityValidationError("Too many redirects")
        current_url = urljoin(normalized_url, location)

    raise SecurityValidationError("Too many redirects")
