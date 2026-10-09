"""Read-only fixed-host IMAP transport; credentials never enter business storage."""
from __future__ import annotations

import importlib
import imaplib
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .extraction import MAX_RAW_BYTES, ParsedMail, parse_mail


class TransportUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FolderBatch:
    uidvalidity: str
    messages: list[ParsedMail]
    last_uid: int
    has_more: bool


class MailTransport(Protocol):
    def folders(self) -> list[str]: ...
    def baseline(self, folder: str) -> tuple[str, int]: ...
    def read(self, folder: str, uidvalidity: str, after_uid: int,
             *, limit: int = 50, since: datetime | None = None) -> FolderBatch: ...


class DisabledTransport:
    def folders(self) -> list[str]:
        raise TransportUnavailable("secure_connection_unavailable")

    def baseline(self, folder: str) -> tuple[str, int]:
        raise TransportUnavailable("secure_connection_unavailable")

    def read(self, folder: str, uidvalidity: str, after_uid: int,
             *, limit: int = 50, since: datetime | None = None) -> FolderBatch:
        raise TransportUnavailable("secure_connection_unavailable")


class OSVault:
    """Only audited OS stores; no plaintext/file/chainer fallback.

    The host must install/configure keyring separately. This interface never
    accepts a password through the mail/chat API. Setup is a user-controlled
    secure handoff; unavailable stores block persistent mailbox connections.
    """
    _ALLOWED = {"keyring.backends.Windows", "keyring.backends.macOS",
                "keyring.backends.SecretService"}

    def __init__(self) -> None:
        try:
            self._keyring: Any = importlib.import_module("keyring")
            backend = self._keyring.get_keyring()
            if backend.__class__.__module__ not in self._ALLOWED:
                raise TransportUnavailable("secure_store_unavailable")
        except TransportUnavailable:
            raise
        except Exception:
            raise TransportUnavailable("secure_store_unavailable") from None

    def get(self, reference: str) -> str:
        try:
            value = self._keyring.get_password("OfferPilot.job-mail", reference)
        except Exception:
            raise TransportUnavailable("secure_store_unavailable") from None
        if not isinstance(value, str) or not value:
            raise TransportUnavailable("credential_missing")
        return value

    def delete(self, reference: str) -> None:
        try:
            self._keyring.delete_password("OfferPilot.job-mail", reference)
        except Exception:
            raise TransportUnavailable("credential_removal_failed") from None


class QQIMAPTransport:
    """No SMTP, arbitrary host, insecure TLS, mailbox writes or body logging.

    Instantiation needs an explicit host-side enable flag and OS credential
    reference. Merely loading the module or opening Settings makes no network
    request. Runtime activation remains disabled by the first-batch API.
    """
    def __init__(self, address: str, credential_ref: str, *, enabled: bool = False,
                 vault: OSVault | None = None) -> None:
        if not enabled or not re.fullmatch(r"[^\s@]+@qq\.com", address, re.I):
            raise TransportUnavailable("real_mail_not_enabled")
        self._address = address
        self._reference = credential_ref
        self._vault = vault or OSVault()

    def _open(self) -> imaplib.IMAP4_SSL:
        client: imaplib.IMAP4_SSL | None = None
        try:
            client = imaplib.IMAP4_SSL("imap.qq.com", 993,
                                      ssl_context=ssl.create_default_context(), timeout=20)
            client.login(self._address, self._vault.get(self._reference))
            return client
        except Exception:
            if client is not None:
                self._close(client)
            # Provider errors may include addresses, identifiers or credentials.
            raise TransportUnavailable("mail_connection_failed") from None

    @staticmethod
    def _close(client: imaplib.IMAP4_SSL) -> None:
        try:
            client.logout()
        except Exception:
            pass

    @staticmethod
    def _select(client: imaplib.IMAP4_SSL, folder: str) -> tuple[str, int]:
        if not folder or any(c in folder for c in '\r\n\x00'):
            raise TransportUnavailable("invalid_folder")
        quoted = '"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"'
        status, _ = client.select(quoted, readonly=True)
        if status != "OK":
            raise TransportUnavailable("folder_unavailable")
        _, raw = client.response("UIDVALIDITY")
        validity = raw[0].decode("ascii") if raw and raw[0] else ""
        if not validity.isdigit():
            raise TransportUnavailable("folder_identity_unavailable")
        _, next_response = client.response("UIDNEXT")
        next_uid = next_response[0].decode("ascii") if next_response and next_response[0] else ""
        if not next_uid.isdigit():
            raise TransportUnavailable("folder_identity_unavailable")
        return validity, max(0, int(next_uid) - 1)

    def folders(self) -> list[str]:
        # Modified UTF-7 folder discovery requires a later authenticated QQ
        # integration check. Never silently select every returned directory.
        raise TransportUnavailable("folder_discovery_requires_validation")

    def baseline(self, folder: str) -> tuple[str, int]:
        client = self._open()
        try:
            return self._select(client, folder)
        finally:
            self._close(client)

    def read(self, folder: str, uidvalidity: str, after_uid: int,
             *, limit: int = 50, since: datetime | None = None) -> FolderBatch:
        client = self._open()
        try:
            validity, highest = self._select(client, folder)
            after = after_uid if validity == uidvalidity else 0
            criteria = ["UID", f"{after + 1}:*"]
            if since is not None:
                cutoff = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
                # IMAP SINCE has day precision. Verify the exact INTERNALDATE
                # before fetching BODY, so the earlier same-day mail is not read.
                criteria.extend(["SINCE", cutoff.strftime("%d-%b-%Y")])
            status, result = client.uid("search", *criteria)
            if status != "OK":
                raise TransportUnavailable("folder_search_failed")
            ids = sorted(int(item) for item in (result[0] or b"").split()
                         if int(item) > after)
            messages: list[ParsedMail] = []
            last = after
            for uid in ids[:max(1, min(limit, 50))]:
                status, size_result = client.uid("fetch", str(uid), "(RFC822.SIZE INTERNALDATE)")
                size_text = b" ".join(item for item in size_result if isinstance(item, bytes))
                size_match = re.search(rb"RFC822.SIZE (\d+)", size_text)
                if status != "OK" or not size_match:
                    raise TransportUnavailable("message_metadata_failed")
                date = re.search(rb'INTERNALDATE "([^"]+)"', size_text)
                if not date:
                    raise TransportUnavailable("message_date_missing")
                received = datetime.strptime(date.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
                if since is not None and received < cutoff:
                    last = uid
                    continue
                if int(size_match.group(1)) > MAX_RAW_BYTES:
                    raise TransportUnavailable("message_too_large")
                status, data = client.uid("fetch", str(uid), "(INTERNALDATE BODY.PEEK[])")
                if status != "OK":
                    raise TransportUnavailable("message_fetch_failed")
                literal = next((item for item in data if isinstance(item, tuple)), None)
                if literal is None:
                    raise TransportUnavailable("message_fetch_failed")
                messages.append(parse_mail(literal[1], uid=uid, uidvalidity=validity,
                                           folder=folder, received_at=received))
                last = uid
            return FolderBatch(validity, messages, last, last < highest)
        except TransportUnavailable:
            raise
        except Exception:
            raise TransportUnavailable("mail_read_failed") from None
        finally:
            self._close(client)


class FakeIMAPTransport:
    """Injected local fixtures only. No endpoint accepts arbitrary IMAP hosts."""
    def __init__(self, messages: list[ParsedMail] | None = None) -> None:
        self.messages = messages or []
        self.generations: dict[str, str] = {m.folder: m.uidvalidity for m in self.messages}
        self.generations.setdefault("INBOX", "1")
        self.read_calls: list[str] = []

    def folders(self) -> list[str]:
        return sorted(self.generations)

    def baseline(self, folder: str) -> tuple[str, int]:
        if folder not in self.generations:
            raise TransportUnavailable("folder_unavailable")
        generation = self.generations[folder]
        return generation, max((m.uid for m in self.messages
                                if m.folder == folder and m.uidvalidity == generation), default=0)

    def read(self, folder: str, uidvalidity: str, after_uid: int,
             *, limit: int = 50, since: datetime | None = None) -> FolderBatch:
        self.read_calls.append(folder)
        generation, _ = self.baseline(folder)
        after = after_uid if generation == uidvalidity else 0
        rows = sorted((m for m in self.messages if m.folder == folder
                       and m.uidvalidity == generation and m.uid > after), key=lambda m: m.uid)
        selected = rows[:limit]
        cutoff = since.replace(tzinfo=timezone.utc) if since and since.tzinfo is None else since
        visible = [m for m in selected if cutoff is None or datetime.fromisoformat(m.received_at) >= cutoff]
        return FolderBatch(generation, visible, selected[-1].uid if selected else after,
                           len(rows) > len(selected))
