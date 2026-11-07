from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import os
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from websockets.asyncio.server import ServerConnection, serve

from app.core.clients.native_egress import (
    NativeEgressRequest,
    NativeEgressTransportError,
    NativeWebSocketRequest,
    SubprocessNativeEgressClient,
)

pytestmark = pytest.mark.integration


def _new_ca(common_name: str = "Ephemeral native TLS test CA") -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


@dataclass
class _TlsFixture:
    ca_key: rsa.RSAPrivateKey
    ca: x509.Certificate
    ca_file: Path

    def context(self, failure: str | None = None) -> ssl.SSLContext:
        issuer_key, issuer = (
            _new_ca("Untrusted native TLS fixture CA") if failure == "unknown_issuer" else (self.ca_key, self.ca)
        )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(UTC)
        alternative_name = (
            x509.DNSName("other.invalid")
            if failure == "name_mismatch"
            else x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
        )
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Loopback native TLS fixture")]))
            .issuer_name(issuer.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(hours=2))
            .not_valid_after(now - timedelta(hours=1) if failure == "expired" else now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
            .add_extension(x509.SubjectAlternativeName([alternative_name]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(issuer_key, hashes.SHA256())
        )
        # Fixture private keys stay in memory and anonymous pipes on the supported Unix hosts.
        certificate_read, certificate_write = os.pipe()
        key_read, key_write = os.pipe()
        try:
            with os.fdopen(certificate_write, "wb") as output:
                output.write(certificate.public_bytes(serialization.Encoding.PEM))
            with os.fdopen(key_write, "wb") as output:
                output.write(
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(f"/dev/fd/{certificate_read}", f"/dev/fd/{key_read}")
            context.set_alpn_protocols(["http/1.1"])
            return context
        finally:
            os.close(certificate_read)
            os.close(key_read)


@pytest.fixture
def tls_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _TlsFixture:
    key, certificate = _new_ca()
    ca_file = tmp_path / "public-ca.pem"
    ca_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    ca_dir = tmp_path / "empty-ca-dir"
    ca_dir.mkdir()
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    monkeypatch.setenv("SSL_CERT_DIR", str(ca_dir))
    return _TlsFixture(key, certificate, ca_file)


@pytest.fixture
async def native_helper(
    record_property: Callable[[str, object], None], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SubprocessNativeEgressClient]:
    configured = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not configured:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run actual-helper TLS regressions")
    binary = Path(configured).resolve()
    assert binary.is_file() and os.access(binary, os.X_OK), "native test binary must be executable"
    record_property("native_helper_binary", str(binary))
    record_property("native_helper_sha256", hashlib.sha256(binary.read_bytes()).hexdigest())
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    client = SubprocessNativeEgressClient(binary)
    try:
        yield client
    finally:
        await client.aclose()


@asynccontextmanager
async def _tcp_server(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
    context: ssl.SSLContext | None = None,
) -> AsyncIterator[int]:
    tasks: set[asyncio.Task[None]] = set()
    writers: set[asyncio.StreamWriter] = set()
    errors: list[Exception] = []

    async def dispatch(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        writers.add(writer)
        try:
            await handler(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as error:
            errors.append(error)
        finally:
            writer.close()
            try:
                with contextlib.suppress(ConnectionError):
                    await writer.wait_closed()
            finally:
                tasks.discard(task)
                writers.discard(writer)

    server = await asyncio.start_server(dispatch, "127.0.0.1", 0, ssl=context)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        for writer in tuple(writers):
            writer.close()
        for task in tuple(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()
    assert not errors


@asynccontextmanager
async def _origin(transport: str, context: ssl.SSLContext) -> AsyncIterator[tuple[str, list[str]]]:
    accepted: list[str] = []
    if transport == "wss":

        async def echo(websocket: ServerConnection) -> None:
            accepted.append("wss")
            async for message in websocket:
                await websocket.send(message)

        async with serve(echo, "127.0.0.1", 0, ssl=context, close_timeout=1) as server:
            yield f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/fixture", accepted
    else:

        async def reply(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            accepted.append("https")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n\r\ntrusted-body")
            await writer.drain()

        async with _tcp_server(reply, context) as port:
            yield f"https://127.0.0.1:{port}/fixture", accepted


async def _exchange(client: SubprocessNativeEgressClient, transport: str, url: str) -> None:
    if transport == "https":
        response = await client.request(
            NativeEgressRequest("GET", url, {}, timeout_seconds=3, connect_timeout_seconds=2)
        )
        try:
            assert response.status == 200
            assert await response.read() == b"trusted-body"
        finally:
            await response.aclose()
    else:
        websocket = await client.websocket(NativeWebSocketRequest(url, {}, 2, 65536))
        try:
            await websocket.send_text("trusted-body")
            message = await websocket.receive()
            assert message.kind == "text" and message.text == "trusted-body"
        finally:
            await websocket.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["https", "wss"])
@pytest.mark.parametrize(("failure", "verify_code"), [("unknown_issuer", 20), ("expired", 10), ("name_mismatch", 64)])
async def test_native_certificate_failure_is_typed_and_non_retryable(
    native_helper: SubprocessNativeEgressClient,
    tls_fixture: _TlsFixture,
    transport: str,
    failure: str,
    verify_code: int,
) -> None:
    async with _origin(transport, tls_fixture.context(failure)) as (url, accepted):
        address = urlsplit(url)
        with pytest.raises(ssl.SSLCertVerificationError) as fixture_error:
            await asyncio.open_connection(
                "127.0.0.1", address.port, ssl=ssl.create_default_context(cafile=str(tls_fixture.ca_file))
            )
        assert fixture_error.value.verify_code == verify_code
        with pytest.raises(NativeEgressTransportError) as caught:
            await _exchange(native_helper, transport, url)
        assert caught.value.failure_phase == "connect"
        assert caught.value.is_tls_verification_failure is True
        assert caught.value.retryable_same_contract is False
        assert not accepted

    process = native_helper._process
    async with _origin(transport, tls_fixture.context()) as (url, accepted):
        await _exchange(native_helper, transport, url)
        assert accepted == [transport]
    assert native_helper._process is process
    assert process is not None and process.returncode is None


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["https", "wss"])
async def test_native_non_certificate_tls_failure_keeps_connect_retry_eligibility(
    native_helper: SubprocessNativeEgressClient, transport: str
) -> None:
    async def malformed_tls(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(1) == b"\x16"
        writer.write(b"invalid peer certificate: UnknownIssuer\r\n")
        await writer.drain()

    async with _tcp_server(malformed_tls) as port:
        with pytest.raises(NativeEgressTransportError) as caught:
            await _exchange(native_helper, transport, f"{transport}://127.0.0.1:{port}/fixture")
        assert caught.value.failure_phase == "connect"
        assert caught.value.is_tls_verification_failure is False
        assert caught.value.retryable_same_contract is True
