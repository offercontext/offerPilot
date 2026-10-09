from datetime import datetime, timezone
from email.message import EmailMessage

import pytest

from offerpilot.job_mail.transport import OSVault, QQIMAPTransport, TransportUnavailable


class Vault:
    def get(self, reference):
        assert reference == "opaque-reference"
        return "synthetic-test-code"


class FakeProtocol:
    def __init__(self, host, port, ssl_context, timeout):
        self.commands = []
        assert (host, port) == ("imap.qq.com", 993)
        assert ssl_context.check_hostname
        assert ssl_context.verify_mode == 2
        assert timeout == 20

    def login(self, address, secret):
        assert address == "synthetic@qq.com"
        assert secret == "synthetic-test-code"

    def select(self, folder, readonly):
        self.commands.append(("select", folder, readonly))
        assert readonly is True
        return "OK", [b"2"]

    def response(self, name):
        return name, [b"1" if name == "UIDVALIDITY" else b"3"]

    def uid(self, name, *args):
        self.commands.append((name, *args))
        if name == "search":
            assert "SINCE" in args
            return "OK", [b"1 2"]
        uid = args[0]
        date = b'09-Oct-2026 01:00:00 +0000' if uid == "1" else b'09-Oct-2026 15:00:00 +0000'
        if args[1] == "(RFC822.SIZE INTERNALDATE)":
            return "OK", [b'1 (RFC822.SIZE 500 INTERNALDATE "' + date + b'")']
        assert uid == "2", "The older same-day body must not be fetched"
        assert args[1] == "(INTERNALDATE BODY.PEEK[])"
        raw = EmailMessage()
        raw["Subject"] = "synthetic interview"
        raw.set_content("面试邀请")
        return "OK", [(b'2 (INTERNALDATE "' + date + b'")', raw.as_bytes())]

    def logout(self):
        self.commands.append(("logout",))


def test_fixed_host_tls_readonly_peek_and_date_filter_before_body(monkeypatch):
    protocols = []
    def factory(*args, **kwargs):
        protocol = FakeProtocol(*args, **kwargs)
        protocols.append(protocol)
        return protocol
    monkeypatch.setattr("imaplib.IMAP4_SSL", factory)
    transport = QQIMAPTransport("synthetic@qq.com", "opaque-reference", enabled=True, vault=Vault())
    result = transport.read("INBOX", "1", 0, since=datetime(2026, 10, 9, 12, tzinfo=timezone.utc))
    assert len(result.messages) == 1
    assert result.messages[0].uid == 2
    assert result.last_uid == 2
    assert all(c[0] not in {"store", "copy", "move", "expunge", "append"} for c in protocols[0].commands)


def test_provider_error_sanitized_and_logged_out(monkeypatch):
    class Failing(FakeProtocol):
        def login(self, address, secret):
            raise RuntimeError("synthetic-test-code private address")
    monkeypatch.setattr("imaplib.IMAP4_SSL", Failing)
    transport = QQIMAPTransport("synthetic@qq.com", "opaque-reference", enabled=True, vault=Vault())
    with pytest.raises(TransportUnavailable) as error:
        transport.read("INBOX", "1", 0)
    assert str(error.value) == "mail_connection_failed"


def test_vault_plaintext_backend_rejected(monkeypatch):
    class Plaintext:
        pass
    class Keyring:
        def get_keyring(self):
            return Plaintext()
    monkeypatch.setattr("importlib.import_module", lambda name: Keyring())
    with pytest.raises(TransportUnavailable, match="secure_store_unavailable"):
        OSVault()
