import asyncio

import pytest

from canvas_mcp.domain.errors import ConfigurationError, DownloadRejectedError, NetworkPolicyError
from canvas_mcp.infrastructure.canvas.file_mapping import file_metadata
from canvas_mcp.infrastructure.config.environment import load_settings
from canvas_mcp.infrastructure.files.policy import BytePolicy, metadata_policy
from canvas_mcp.infrastructure.files.download import DownloadBackend
from canvas_mcp.domain.models import FileReference
from conftest import ORIGIN


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("malware.exe", "application/pdf"),
        ("test.html", "text/html"),
        ("test.svg", "image/svg+xml"),
        ("test.docm", "application/octet-stream"),
        ("test.tar", "application/octet-stream"),
        ("test.pdf", "text/html"),
        ("foo.pdf::$DATA", "application/pdf"),
    ],
)
def test_conservative_format_allowlist(filename, mime):
    metadata = file_metadata(
        {"id": 10, "display_name": filename, "filename": filename, "content-type": mime},
        FileReference("10", "8"),
    )
    with pytest.raises(DownloadRejectedError):
        metadata_policy(metadata, 100)


@pytest.mark.parametrize(
    "classification,data",
    [
        ("untrusted_text", b"MZevil"),
        ("untrusted_text", b"\x7fELFfoo"),
        ("untrusted_text", b"<html>hi"),
        ("untrusted_document", b"MZevil"),
        ("office_candidate", b"not a zip"),
        ("untrusted_text", b"\xff"),
        ("untrusted_text", b"\x00"),
        ("untrusted_text", b"\xef"),
    ],
)
def test_prefix_and_text_evidence_rejects_disagreement(classification, data):
    policy = BytePolicy(classification)
    with pytest.raises(DownloadRejectedError):
        policy.update(data)
        policy.finish()


@pytest.mark.parametrize(
    "classification,data",
    [
        ("untrusted_document", b"%PDF-inert"),
        ("office_candidate", b"PK\x03\x04inert"),
        ("opaque_archive", b"PK\x05\x06"),
        ("untrusted_text", b"print('do not execute')"),
    ],
)
def test_only_prefix_evidence_no_parser_or_archive_access(classification, data, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no extraction")

    monkeypatch.setattr("zipfile.ZipFile", forbidden)
    policy = BytePolicy(classification)
    policy.update(data)
    policy.finish()


@pytest.mark.parametrize(
    "key,value",
    [
        ("DOWNLOAD_TIMEOUT", "0"),
        ("DOWNLOAD_TIMEOUT", "nan"),
        ("DOWNLOAD_TIMEOUT", "301"),
        ("MAX_DOWNLOAD_REDIRECTS", "4"),
        ("MAX_DOWNLOAD_REDIRECTS", "-1"),
        ("MAX_DOWNLOAD_BYTES", "0"),
        ("MAX_DOWNLOAD_BYTES", "262144001"),
    ],
)
def test_file_config_bounds(key, value):
    with pytest.raises(ConfigurationError):
        load_settings({"CANVAS_BASE_URL": ORIGIN, key: value})


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["8.8.8.8", "10.0.0.1"],
        ["169.254.169.254"],
        ["::ffff:8.8.8.8"],
        ["::1"],
        ["224.0.0.1"],
        ["0.0.0.0"],
    ],
)
def test_cdn_dns_all_answers_checked_at_connect(addresses, monkeypatch):
    async def run():
        async def resolve(*args, **kwargs):
            return [(2, 1, 6, "", (address, 443)) for address in addresses]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        backend = DownloadBackend(frozenset(("https://cdn.example",)))
        with pytest.raises(NetworkPolicyError):
            await backend.connect_tcp("cdn.example", 443, 1)

    asyncio.run(run())


def test_validated_cdn_dns_dials_ip_not_hostname(monkeypatch):
    async def run():
        calls = []

        async def resolve(*args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 443))]

        async def connect(host, port, **kwargs):
            calls.append((host, port))
            return object()

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        backend = DownloadBackend(frozenset(("https://cdn.example",)))
        monkeypatch.setattr(backend._backends["cdn.example"]._backend, "connect_tcp", connect)
        await backend.connect_tcp("cdn.example", 443, 1)
        assert calls == [("8.8.8.8", 443)]

    asyncio.run(run())
