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


def test_fixed_run_cutoff_does_not_fetch_newer_mail_body(monkeypatch):
    protocols = []
    def factory(*args, **kwargs):
        protocol = FakeProtocol(*args, **kwargs)
        protocols.append(protocol)
        return protocol
    monkeypatch.setattr('imaplib.IMAP4_SSL', factory)
    transport = QQIMAPTransport('synthetic@qq.com', 'opaque-reference', enabled=True, vault=Vault())
    batch = transport.read('INBOX', '1', 0,
        since=datetime(2026, 10, 9, 12, tzinfo=timezone.utc),
        until=datetime(2026, 10, 9, 14, tzinfo=timezone.utc))
    assert batch.messages == []
    assert batch.last_uid == 1
    assert batch.has_more
    assert not any('BODY.PEEK[]' in str(command) for command in protocols[0].commands)


@pytest.mark.parametrize("folder", ["收件箱", 'Candidates & Hiring/"Team"', "项目😀"])
def test_modified_utf7_roundtrip_and_readonly_quoted_wire_folder(folder):
    from offerpilot.job_mail.transport import decode_mailbox, encode_mailbox
    assert decode_mailbox(encode_mailbox(folder)) == folder
    protocol = FakeProtocol("imap.qq.com", 993, __import__("ssl").create_default_context(), 20)
    QQIMAPTransport._select(protocol, folder)
    expected = '"' + encode_mailbox(folder).decode().replace('\\', '\\\\').replace('"', '\\"') + '"'
    assert protocol.commands == [("select", expected, True)]


def test_list_parses_literals_noselect_special_use_nil_and_escaped_quotes():
    from offerpilot.job_mail.transport import encode_mailbox, parse_folder_list
    wire = encode_mailbox("招聘 & 面试")
    literal = parse_folder_list((b'(\\HasNoChildren) NIL {' + str(len(wire)).encode() + b'}', wire))
    assert literal["name"] == "招聘 & 面试" and literal["selectable"]
    assert literal["id"] != literal["name"]
    assert parse_folder_list(b'(\\NoSelect) "/" "Parent"')["selectable"] is False
    sent = parse_folder_list(b'(\\Sent) "/" "Sent Items"')
    assert sent["excluded_by_default"] and sent["special_use"] == ["\\Sent"]
    assert parse_folder_list(b'() "/" "Team \\"A\\""')["name"] == 'Team "A"'


@pytest.mark.parametrize("row", [b'garbage', b'() NIL "&bad-"', b'() NIL "bad\rname"',
                                (b'() NIL {3}', b'four'), b'() NIL "unfinished'])
def test_invalid_list_is_rejected(row):
    from offerpilot.job_mail.transport import parse_folder_list
    with pytest.raises(TransportUnavailable):
        parse_folder_list(row)


def test_vault_control_characters_never_reach_login(monkeypatch):
    logins = []
    class UnsafeVault:
        def get(self, reference):
            return "fake\r\nA STORE 1 +FLAGS \\Deleted"
    class NoLogin(FakeProtocol):
        def login(self, *args):
            logins.append(args)
    monkeypatch.setattr("imaplib.IMAP4_SSL", NoLogin)
    transport = QQIMAPTransport("synthetic@qq.com", "opaque-reference", enabled=True, vault=UnsafeVault())
    with pytest.raises(TransportUnavailable):
        transport.read("INBOX", "1", 0)
    assert logins == []


def test_stdlib_login_never_retains_plaintext_in_diagnostic_ring(monkeypatch):
    import imaplib
    class MemoryProtocol(imaplib.IMAP4):
        def __init__(self, *args, **kwargs):
            self.state = "NONAUTH"
            self.literal = None
            self.tagged_commands = {}
            self.untagged_responses = {}
            self.continuation_response = None
            self.is_readonly = False
            self.tagpre = b"MOCK"
            self.tagnum = 0
            self._encoding = "ascii"
            self._cmd_log = {}
            self._cmd_log_idx = 0
            self._cmd_log_len = 10
            self.debug = 0
        def send(self, data):
            assert b"synthetic-test-code" in data
        def _command_complete(self, name, tag):
            return "OK", [b"logged in"]
        def logout(self):
            pass
    monkeypatch.setattr("imaplib.IMAP4_SSL", MemoryProtocol)
    transport = QQIMAPTransport("synthetic@qq.com", "opaque-reference", enabled=True, vault=Vault())
    client = transport._open()
    assert client.state == "AUTH"
    assert client._cmd_log == {}
    transport._close(client)
