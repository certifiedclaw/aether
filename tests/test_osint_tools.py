import ipaddress
import socket

import pytest

from actions import osint_tools as ot


class _FakeResponse:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self._headers = headers or []
        self._body = body
        self.read_limit = None

    def getheaders(self):
        return self._headers

    def read(self, limit):
        self.read_limit = limit
        return self._body


class _FakeConnection:
    instances = []
    response = _FakeResponse()

    def __init__(self, host, port, address, timeout):
        self.host = host
        self.port = port
        self.address = address
        self.timeout = timeout
        self.requested = None
        type(self).instances.append(self)

    def request(self, method, path, headers):
        self.requested = (method, path, headers)

    def getresponse(self):
        return type(self).response

    def close(self):
        pass


def _address_info(address):
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0))


def test_resolve_public_addresses_rejects_mixed_public_and_private_dns(monkeypatch):
    monkeypatch.setattr(
        ot.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            _address_info("8.8.8.8"),
            _address_info("10.0.0.4"),
        ],
    )

    addresses, error = ot._resolve_public_addresses("mixed.example")

    assert addresses == []
    assert "non-public" in error


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1",
        "https://[::1]",
        "ftp://example.com",
        "file:///etc/passwd",
        "https://user:password@example.com",
        "https://example.com:99999",
        "https://example.com:0",
        "https://example.com/\r\nHost: internal",
    ],
)
def test_tech_stack_rejects_unsafe_targets_before_connecting(monkeypatch, target):
    _FakeConnection.instances = []
    monkeypatch.setattr(ot, "_PinnedHTTPConnection", _FakeConnection)
    monkeypatch.setattr(ot, "_PinnedHTTPSConnection", _FakeConnection)

    result = ot.tech_stack(target)

    assert result.startswith("Tech stack lookup blocked:")
    assert _FakeConnection.instances == []


def test_tech_stack_pins_public_ip_and_caps_response_read(monkeypatch):
    _FakeConnection.instances = []
    _FakeConnection.response = _FakeResponse(
        headers=[("Server", "nginx")],
        body=b"<html>wp-content</html>",
    )
    monkeypatch.setattr(
        ot,
        "_resolve_public_addresses",
        lambda hostname: ([ipaddress.ip_address("8.8.8.8")], None),
    )
    monkeypatch.setattr(ot, "_PinnedHTTPSConnection", _FakeConnection)

    result = ot.tech_stack("https://example.test/about?q=public")

    connection = _FakeConnection.instances[0]
    assert connection.address == "8.8.8.8"
    assert connection.requested[0:2] == ("GET", "/about?q=public")
    assert connection.requested[2]["Host"] == "example.test"
    assert _FakeConnection.response.read_limit == 30000
    assert "nginx" in result
    assert "WordPress" in result


def test_tech_stack_does_not_follow_redirects(monkeypatch):
    _FakeConnection.instances = []
    _FakeConnection.response = _FakeResponse(
        status=302,
        headers=[("Location", "http://127.0.0.1/admin")],
    )
    monkeypatch.setattr(
        ot,
        "_resolve_public_addresses",
        lambda hostname: ([ipaddress.ip_address("8.8.8.8")], None),
    )
    monkeypatch.setattr(ot, "_PinnedHTTPSConnection", _FakeConnection)

    result = ot.tech_stack("example.test")

    assert "redirects are not followed" in result
    assert len(_FakeConnection.instances) == 1


@pytest.mark.parametrize(
    "subnet",
    ["8.8.8.0/24", "172.15.0.0/24", "192.168.1.0/23", "2001:db8::/64", "not-a-subnet"],
)
def test_lan_audit_rejects_non_private_or_oversized_subnets(monkeypatch, subnet):
    monkeypatch.setattr(
        ot.socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("invalid subnet must not be scanned"),
    )

    result = ot.lan_audit(subnet)

    assert result.startswith(("LAN audit is limited", "Subnet too large", "Invalid subnet"))


def test_lan_audit_scans_only_hosts_and_requested_ports(monkeypatch):
    attempts = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def connect(address, timeout):
        attempts.append((address, timeout))
        if address == ("192.168.1.2", 80):
            return FakeSocket()
        raise OSError("closed")

    monkeypatch.setattr(ot.socket, "create_connection", connect)

    result = ot.lan_audit("192.168.1.0/30", "80,443")

    assert set(attempts) == {
        (("192.168.1.1", 80), ot._LAN_AUDIT_TIMEOUT),
        (("192.168.1.1", 443), ot._LAN_AUDIT_TIMEOUT),
        (("192.168.1.2", 80), ot._LAN_AUDIT_TIMEOUT),
        (("192.168.1.2", 443), ot._LAN_AUDIT_TIMEOUT),
    }
    assert "192.168.1.2: 80 (HTTP)" in result
    assert "not a vulnerability assessment" in result
    assert "not, by themselves, confirmed vulnerabilities" in result
    assert "does not show that they are reachable from the internet" in result
    assert "OPSEC triage before changing settings" in result
    assert "What device or data are you protecting" in result
    assert "choose a proportionate step" in result


def test_lan_audit_rejects_invalid_or_excessive_port_lists(monkeypatch):
    monkeypatch.setattr(
        ot.socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("invalid port list must not be scanned"),
    )

    assert ot.lan_audit("192.168.1.0/30", "bad").startswith("Invalid ports")
    assert ot.lan_audit("192.168.1.0/30", ",".join(map(str, range(1, 34)))).startswith(
        "Too many ports"
    )


def test_pinned_https_connection_uses_pinned_ip_and_hostname_sni(monkeypatch):
    connected_to = []
    server_names = []

    class FakeSocket:
        def close(self):
            pass

    class FakeContext:
        def wrap_socket(self, sock, server_hostname):
            server_names.append(server_hostname)
            return sock

    monkeypatch.setattr(
        ot.socket,
        "create_connection",
        lambda address, *args: connected_to.append(address) or FakeSocket(),
    )
    monkeypatch.setattr(ot.ssl, "create_default_context", FakeContext)

    connection = ot._PinnedHTTPSConnection("example.test", 443, "8.8.8.8", 10)
    connection.connect()
    connection.close()

    assert connected_to == [("8.8.8.8", 443)]
    assert server_names == ["example.test"]


def test_ssl_cert_lookup_blocks_private_hosts_before_connecting(monkeypatch):
    monkeypatch.setattr(
        ot.socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("private target must not be contacted"),
    )

    result = ot.ssl_cert_info("127.0.0.1")

    assert result.startswith("SSL cert lookup blocked:")


def test_ssl_cert_lookup_pins_public_ip_and_keeps_hostname_for_tls(monkeypatch):
    connected_to = []
    server_names = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def getpeercert(self):
            return {}

    class FakeContext:
        def wrap_socket(self, sock, server_hostname):
            server_names.append(server_hostname)
            return sock

    monkeypatch.setattr(
        ot.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_address_info("8.8.8.8")],
    )
    monkeypatch.setattr(
        ot.socket,
        "create_connection",
        lambda address, **kwargs: connected_to.append(address) or FakeSocket(),
    )
    monkeypatch.setattr(ot.ssl, "create_default_context", FakeContext)

    result = ot.ssl_cert_info("example.test")

    assert connected_to == [("8.8.8.8", 443)]
    assert server_names == ["example.test"]
    assert "SSL Certificate: example.test:443" in result
