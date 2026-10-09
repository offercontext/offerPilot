"""Read-only fixed-host IMAP transport; credentials never enter business storage."""
from __future__ import annotations

import base64
import binascii
import importlib
import threading
import imaplib
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .extraction import MAX_RAW_BYTES, ParsedMail, parse_mail


class TransportUnavailable(RuntimeError):
    pass


class SecretReader(Protocol):
    def get(self, reference: str) -> str: ...


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
             *, limit: int = 50, since: datetime | None = None,
             until: datetime | None = None) -> FolderBatch: ...


class DisabledTransport:
    def folders(self) -> list[str]:
        raise TransportUnavailable("secure_connection_unavailable")

    def baseline(self, folder: str) -> tuple[str, int]:
        raise TransportUnavailable("secure_connection_unavailable")

    def read(self, folder: str, uidvalidity: str, after_uid: int,
             *, limit: int = 50, since: datetime | None = None,
             until: datetime | None = None) -> FolderBatch:
        raise TransportUnavailable("secure_connection_unavailable")


class OSVault:
    """Pin one explicitly approved native backend; never follow global fallbacks."""
    _ALLOWED = {"keyring.backends.Windows", "keyring.backends.macOS",
                "keyring.backends.SecretService"}

    def __init__(self) -> None:
        try:
            keyring = importlib.import_module("keyring")
            backend = keyring.get_keyring()
            if backend.__class__.__module__ not in self._ALLOWED or backend.priority <= 0:
                raise TransportUnavailable("secure_store_unavailable")
            self._backend: Any = backend
            self.backend_name = backend.__class__.__module__
        except Exception:
            raise TransportUnavailable("secure_store_unavailable") from None

    def get(self, reference: str) -> str:
        try:
            value = self._backend.get_password("OfferPilot.job-mail", reference)
        except Exception:
            raise TransportUnavailable("secure_store_unavailable") from None
        if not isinstance(value, str) or not value:
            raise TransportUnavailable("credential_missing")
        return value

    def set(self, reference: str, secret: str) -> None:
        try:
            self._backend.set_password("OfferPilot.job-mail", reference, secret)
        except Exception:
            raise TransportUnavailable("credential_write_failed") from None

    def delete(self, reference: str) -> None:
        try:
            # Missing after an interrupted delete is already a successful cleanup.
            if self._backend.get_password("OfferPilot.job-mail", reference) is None:
                return
            self._backend.delete_password("OfferPilot.job-mail", reference)
            if self._backend.get_password("OfferPilot.job-mail", reference) is not None:
                raise TransportUnavailable("credential_removal_failed")
        except Exception:
            raise TransportUnavailable("credential_removal_failed") from None


def encode_mailbox(name: str) -> bytes:
    """IMAP modified UTF-7; retain the exact decoded mailbox identity."""
    output: list[str] = []
    pending: list[str] = []
    def flush() -> None:
        if pending:
            encoded = base64.b64encode("".join(pending).encode("utf-16-be")).decode("ascii")
            output.append("&" + encoded.rstrip("=").replace("/", ",") + "-")
            pending.clear()
    for char in name:
        if " " <= char <= "~":
            flush()
            output.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    return "".join(output).encode("ascii")


def decode_mailbox(raw: bytes) -> str:
    try:
        text = raw.decode("ascii")
        output: list[str] = []
        at = 0
        while at < len(text):
            if text[at] != "&":
                if not " " <= text[at] <= "~":
                    raise ValueError("invalid mailbox")
                output.append(text[at])
                at += 1
                continue
            end = text.index("-", at)
            part = text[at + 1:end]
            if not part:
                output.append("&")
            else:
                encoded = part.replace(",", "/")
                encoded += "=" * (-len(encoded) % 4)
                decoded = base64.b64decode(encoded, validate=True).decode("utf-16-be")
                if any(" " <= char <= "~" for char in decoded):
                    raise ValueError("noncanonical mailbox")
                output.append(decoded)
            at = end + 1
        result = "".join(output)
        if not result or len(result) > 500 or any(c in result for c in "\r\n\x00"):
            raise ValueError("invalid mailbox")
        if encode_mailbox(result) != raw:
            raise ValueError("noncanonical mailbox")
        return result
    except (ValueError, UnicodeError, binascii.Error):
        raise TransportUnavailable("folder_discovery_invalid") from None


def parse_folder_list(row: object) -> dict[str, Any]:
    literal: bytes | None = None
    if isinstance(row, tuple) and len(row) == 2 and isinstance(row[0], bytes) and isinstance(row[1], bytes):
        prefix, literal = row
    elif isinstance(row, bytes):
        prefix = row
    else:
        raise TransportUnavailable("folder_discovery_invalid")
    match = re.fullmatch(rb'\(([^)]*)\)\s+(NIL|"(?:[^"\\]|\\.)*")\s+(.+)', prefix)
    if match is None:
        raise TransportUnavailable("folder_discovery_invalid")
    flags = match.group(1).decode("ascii").split()
    name = match.group(3)
    if literal is not None:
        length = re.fullmatch(rb'\{(\d+)\}', name)
        if length is None or int(length.group(1)) != len(literal):
            raise TransportUnavailable("folder_discovery_invalid")
        raw_name = literal
    elif re.fullmatch(rb'"(?:[^"\\]|\\["\\])*"', name):
        raw_name = re.sub(rb'\\(["\\])', rb'\1', name[1:-1])
    elif re.fullmatch(rb'[^\x00-\x20(){}"\\\x7f]+', name):
        raw_name = name
    else:
        raise TransportUnavailable("folder_discovery_invalid")
    display = decode_mailbox(raw_name)
    special = [flag for flag in flags if flag.lower() in {
        "\\sent", "\\drafts", "\\junk", "\\trash", "\\all", "\\archive", "\\flagged"}]
    excluded = any(flag.lower() in {"\\sent", "\\drafts", "\\junk", "\\trash", "\\all"} for flag in special)
    excluded = excluded or display.casefold() in {
        "sent", "sent messages", "drafts", "junk", "spam", "trash", "deleted messages",
        "已发送", "草稿箱", "垃圾箱", "已删除", "垃圾邮件", "草稿"}
    return {"id": "imap:" + base64.urlsafe_b64encode(raw_name).decode("ascii"),
            "name": display, "selectable": "\\noselect" not in {flag.lower() for flag in flags},
            "excluded_by_default": excluded, "special_use": special}


class QQIMAPTransport:
    """No SMTP, arbitrary host, insecure TLS, mailbox writes or body logging.

    Instantiation needs an explicit host-side enable flag and OS credential
    reference. Merely loading the module or opening Settings makes no network
    request. Runtime activation remains disabled by the first-batch API.
    """
    def __init__(self, address: str, credential_ref: str, *, enabled: bool = False,
                 vault: SecretReader | None = None) -> None:
        if not enabled or not re.fullmatch(r"[^\s@]+@qq\.com", address, re.I):
            raise TransportUnavailable("real_mail_not_enabled")
        self._address = address
        self._reference = credential_ref
        self._vault = vault or OSVault()
        self._cancelled = threading.Event()
        self._active_lock = threading.Lock()
        self._active: set[imaplib.IMAP4_SSL] = set()

    def _open(self) -> imaplib.IMAP4_SSL:
        client: imaplib.IMAP4_SSL | None = None
        try:
            self._check_cancelled()
            client = imaplib.IMAP4_SSL("imap.qq.com", 993,
                                      ssl_context=ssl.create_default_context(), timeout=20)
            with self._active_lock:
                self._active.add(client)
            self._check_cancelled()
            client.debug = 0  # IMAP debug must never print the LOGIN credential.
            # imaplib still retains complete commands in its diagnostic ring
            # even with debug=0. Disable that instance's ring before LOGIN.
            setattr(client, "_log", lambda *args: None)
            command_log = getattr(client, "_cmd_log", None)
            if isinstance(command_log, dict):
                command_log.clear()
            secret = self._vault.get(self._reference)
            if not secret or any(ord(char) < 32 or ord(char) == 127 for char in secret):
                raise TransportUnavailable("invalid_credential")
            self._check_cancelled()
            client.login(self._address, secret)
            self._check_cancelled()
            return client
        except Exception:
            if client is not None:
                self._close(client)
            # Provider errors may include addresses, identifiers or credentials.
            raise TransportUnavailable("mail_connection_failed") from None

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise TransportUnavailable("mail_cancelled")

    def cancel(self) -> None:
        self._cancelled.set()
        with self._active_lock:
            clients = list(self._active)
        for client in clients:
            try:
                client.shutdown()
            except Exception:
                pass

    def _close(self, client: imaplib.IMAP4_SSL) -> None:
        with self._active_lock:
            self._active.discard(client)
        try:
            client.logout()
        except Exception:
            pass

    @staticmethod
    def _select(client: imaplib.IMAP4_SSL, folder: str) -> tuple[str, int]:
        if not folder or any(c in folder for c in '\r\n\x00'):
            raise TransportUnavailable("invalid_folder")
        wire = encode_mailbox(folder).decode("ascii")
        quoted = '"' + wire.replace('\\', '\\\\').replace('"', '\\"') + '"'
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

    def discover(self) -> list[dict[str, Any]]:
        client = self._open()
        try:
            self._check_cancelled()
            status, rows = client.list('""', '"*"')
            self._check_cancelled()
            if status != "OK" or not isinstance(rows, list) or len(rows) > 500:
                raise TransportUnavailable("folder_discovery_failed")
            folders = [parse_folder_list(row) for row in rows if row not in (None, b"", b")")]
            if len({folder["id"] for folder in folders}) != len(folders):
                raise TransportUnavailable("folder_discovery_invalid")
            return folders
        except TransportUnavailable:
            raise
        except Exception:
            raise TransportUnavailable("folder_discovery_failed") from None
        finally:
            self._close(client)

    def folders(self) -> list[str]:
        return [folder["name"] for folder in self.discover() if folder["selectable"]]

    def baseline(self, folder: str) -> tuple[str, int]:
        client = self._open()
        try:
            self._check_cancelled()
            result = self._select(client, folder)
            self._check_cancelled()
            return result
        finally:
            self._close(client)

    def read(self, folder: str, uidvalidity: str, after_uid: int,
             *, limit: int = 50, since: datetime | None = None,
             until: datetime | None = None) -> FolderBatch:
        client = self._open()
        try:
            self._check_cancelled()
            validity, highest = self._select(client, folder)
            after = after_uid if validity == uidvalidity else 0
            criteria = ["UID", f"{after + 1}:*"]
            if since is not None:
                cutoff = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
                # IMAP SINCE has day precision. Verify the exact INTERNALDATE
                # before fetching BODY, so the earlier same-day mail is not read.
                criteria.extend(["SINCE", cutoff.strftime("%d-%b-%Y")])
            self._check_cancelled()
            status, result = client.uid("search", *criteria)
            self._check_cancelled()
            if status != "OK":
                raise TransportUnavailable("folder_search_failed")
            ids = sorted(int(item) for item in (result[0] or b"").split()
                         if int(item) > after)
            messages: list[ParsedMail] = []
            last = after
            for uid in ids[:max(1, min(limit, 50))]:
                self._check_cancelled()
                status, size_result = client.uid("fetch", str(uid), "(RFC822.SIZE INTERNALDATE)")
                size_text = b" ".join(item for item in size_result if isinstance(item, bytes))
                size_match = re.search(rb"RFC822.SIZE (\d+)", size_text)
                if status != "OK" or not size_match:
                    raise TransportUnavailable("message_metadata_failed")
                date = re.search(rb'INTERNALDATE "([^"]+)"', size_text)
                if not date:
                    raise TransportUnavailable("message_date_missing")
                received = datetime.strptime(date.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
                upper = until.replace(tzinfo=timezone.utc) if until and until.tzinfo is None else until
                if upper is not None and received > upper:
                    # Preserve this UID for the next run. A manual sync must
                    # not expand its scope as newer mail arrives during work.
                    break
                if since is not None and received < cutoff:
                    last = uid
                    continue
                if int(size_match.group(1)) > MAX_RAW_BYTES:
                    raise TransportUnavailable("message_too_large")
                self._check_cancelled()
                status, data = client.uid("fetch", str(uid), "(INTERNALDATE BODY.PEEK[])")
                self._check_cancelled()
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
             *, limit: int = 50, since: datetime | None = None,
             until: datetime | None = None) -> FolderBatch:
        self.read_calls.append(folder)
        generation, _ = self.baseline(folder)
        after = after_uid if generation == uidvalidity else 0
        rows = sorted((m for m in self.messages if m.folder == folder
                       and m.uidvalidity == generation and m.uid > after), key=lambda m: m.uid)
        selected = rows[:limit]
        cutoff = since.replace(tzinfo=timezone.utc) if since and since.tzinfo is None else since
        upper = until.replace(tzinfo=timezone.utc) if until and until.tzinfo is None else until
        admitted: list[ParsedMail] = []
        for message in selected:
            if upper is not None and datetime.fromisoformat(message.received_at) > upper:
                break
            admitted.append(message)
        visible = [m for m in admitted if cutoff is None or datetime.fromisoformat(m.received_at) >= cutoff]
        return FolderBatch(generation, visible, admitted[-1].uid if admitted else after,
                           len(rows) > len(admitted))
