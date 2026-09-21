import pathlib as pl
import socket

import pytest

from marven_local import security
from marven_local.security import (
    SecurityValidationError,
    extract_fenced_updates,
    extract_readable_text,
    resolve_path_within,
    validate_identifier,
)


def test_extract_readable_text_ignores_active_content():
    html = "<title>Marven</title><script>steal()</script><style>.x{}</style><p>Hello &amp; welcome</p>"
    assert extract_readable_text(html) == "Title: Marven\n\nMarven Hello & welcome"


def test_extract_fenced_updates_parses_without_regex_backtracking():
    text = "before\n```python file: src/app.py\nprint('safe')\n```\nafter"
    assert list(extract_fenced_updates(text)) == [("src/app.py", "print('safe')")]


def test_extract_fenced_updates_ignores_unclosed_fence():
    assert list(extract_fenced_updates("```file: src/app.py\nmissing close")) == []


def test_validate_identifier_accepts_path_safe_values():
    assert validate_identifier("session_ABC-123") == "session_ABC-123"


@pytest.mark.parametrize("value", ["", "../escape", "has/slash", "has space", "é", "x" * 129])
def test_validate_identifier_rejects_unsafe_values(value):
    with pytest.raises(SecurityValidationError):
        validate_identifier(value)


def test_resolve_path_within_accepts_descendants(tmp_path: pl.Path):
    root = tmp_path / "root"
    root.mkdir()
    assert resolve_path_within(root, "folder/file.txt") == root / "folder" / "file.txt"


def test_resolve_path_within_rejects_parent_and_sibling_prefix(tmp_path: pl.Path):
    root = tmp_path / "root"
    sibling = tmp_path / "root-escape"
    root.mkdir()
    sibling.mkdir()
    with pytest.raises(SecurityValidationError):
        resolve_path_within(root, "../root-escape/file.txt")
    with pytest.raises(SecurityValidationError):
        resolve_path_within(root, sibling / "file.txt")


def test_resolve_path_within_rejects_symlink_escape(tmp_path: pl.Path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SecurityValidationError):
        resolve_path_within(root, "link/secret.txt")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_url_validation_blocks_non_public_addresses(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(family, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    with pytest.raises(SecurityValidationError):
        security._validated_url("https://example.test/data")


def test_url_validation_accepts_public_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    parsed, addresses = security._validated_url("https://example.test/path?q=1#fragment")
    assert parsed.scheme == "https"
    assert parsed.fragment == ""
    assert addresses == ("93.184.216.34",)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "https://user:pass@example.com", "http://example.com:8080"],
)
def test_url_validation_rejects_unsafe_url_forms(monkeypatch, url):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    with pytest.raises(SecurityValidationError):
        security._validated_url(url)


def test_redirect_target_is_revalidated(monkeypatch):
    def fake_getaddrinfo(host, port, **kwargs):
        address = "93.184.216.34" if host == "public.example" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    class FakeConnection:
        def close(self):
            pass

    class FakeResponse:
        status = 302
        headers = {"Location": "http://127.0.0.1/private"}

        def close(self):
            pass

    opened = []

    def fake_open(parsed, addresses, method, headers, timeout):
        opened.append((parsed.hostname, addresses))
        return FakeConnection(), FakeResponse()

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(security, "_open_pinned", fake_open)
    with pytest.raises(SecurityValidationError):
        security.open_public_http_url("https://public.example/start")
    assert opened == [("public.example", ("93.184.216.34",))]
