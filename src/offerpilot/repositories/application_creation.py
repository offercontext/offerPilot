"""Single Application intake. No fetching, model calls, or business deduplication."""
from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Application, ApplicationCreationReceipt, ApplicationCreationWorkspace
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.application_jd_versions import (
    ApplicationJDService, JDVersionError, JDVersionValidationError,
    _normalize_source_url, _validate_idempotency_key, _validate_jd_text,
)
from offerpilot.schemas import ApplicationOut
from offerpilot.application_status import normalize_application_status


class ApplicationCreationService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def scope_id(self) -> str:
        with self.sessions() as session:
            return str(session.scalars(select(ApplicationCreationWorkspace.scope_id)).one())

    def create(
        self, data: ApplicationCreate, initial_jd: Any, key: Any,
    ) -> tuple[dict[str, Any], bool]:
        if key is not None:
            _validate_idempotency_key(key)
        jd = None
        if initial_jd is not None:
            if key is None:
                raise JDVersionValidationError('initial_jd requires a creation idempotency_key')
            if not isinstance(initial_jd, dict) or set(initial_jd) - {'jd_text', 'source_url'}:
                raise JDVersionValidationError('initial_jd must contain only jd_text and source_url')
            raw_text = initial_jd.get('jd_text')
            if not isinstance(raw_text, str):
                raise JDVersionValidationError('jd_text must be a string')
            _validate_jd_text(raw_text)
            jd = {'jd_text': initial_jd['jd_text'],
                  'source_url': _normalize_source_url(initial_jd.get('source_url'))}
        # Defaults and status aliases have been resolved by the HTTP boundary.
        data.closed_reason = data.closed_reason.strip() if data.status == 'closed' else ''
        if data.status == 'closed' and not data.closed_reason:
            raise ValueError('closed_reason is required when closing an application')
        fingerprint = hashlib.sha256(json.dumps(
            {'application': asdict(data), 'initial_jd': jd},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        ).encode('utf-8')).hexdigest()
        scope = self.scope_id()
        try:
            with self.sessions() as session:
                # SQLite serializes competing writers before either creates a resource.
                session.execute(text('BEGIN IMMEDIATE'))
                if key is not None:
                    receipt = session.scalar(select(ApplicationCreationReceipt).where(
                        ApplicationCreationReceipt.scope_id == scope,
                        ApplicationCreationReceipt.operation == 'create_application',
                        ApplicationCreationReceipt.idempotency_key == key,
                    ))
                    if receipt is not None:
                        if receipt.request_fingerprint != fingerprint:
                            raise JDVersionError('同一创建请求的内容不同',
                                                 'application_creation_conflict', 409)
                        return json.loads(receipt.result_json), True
                application = ApplicationsRepository(self.sessions).bind(session).create(data)
                version = None
                if jd is not None:
                    version = ApplicationJDService(self.sessions).bind(session).create_version(
                        application.id, jd_text=jd['jd_text'], source_url=jd['source_url'],
                        source_kind='ui', expected_current_version_id=None,
                        idempotency_key=key or uuid.uuid4().hex,
                    ).version
                result = ApplicationOut.model_validate(application).model_dump(mode='json')
                if key is not None or jd is not None:
                    result['jd_version_id'] = version.id if version else None
                if key is not None:
                    session.add(ApplicationCreationReceipt(
                        scope_id=scope, operation='create_application', idempotency_key=key,
                        request_fingerprint=fingerprint, application_id=application.id,
                        jd_version_id=version.id if version else None,
                        result_json=json.dumps(result, ensure_ascii=False),
                    ))
                session.commit()
                return result, False
        except OperationalError as exc:
            if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                raise JDVersionError('创建尚未确认，请使用原请求恢复',
                                     'application_creation_retryable', 503) from exc
            raise

    def duplicates(self, company: str, position: str, url: str) -> dict[str, Any]:
        def normalized(value: str) -> str:
            return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())

        def close(left: str, right: str) -> bool:
            if not left or not right:
                return False
            shorter, longer = sorted((left, right), key=len)
            return left == right or (len(shorter) >= 2 and longer.startswith(shorter))

        company, position, url = normalized(company), normalized(position), url.strip()
        matches: list[tuple[int, Application]] = []
        with self.sessions() as session:
            rows = session.scalars(select(Application).where(Application.deleted_at.is_(None))
                                   .order_by(Application.updated_at.desc(), Application.id.desc()))
            for row in rows:
                c, p = normalized(row.company_name), normalized(row.position_name)
                if url and row.job_url.strip() == url:
                    matches.append((0, row))
                elif close(company, c) and close(position, p):
                    matches.append((1 if (company, position) == (c, p) else 2, row))
            matches.sort(key=lambda item: item[0])
            return {
                'items': [dict(ApplicationOut.model_validate(row).model_dump(mode='json'),
                               status=normalize_application_status(row.status),
                               match_reason=('url', 'exact_name', 'prefix')[rank])
                          for rank, row in matches[:20]],
                'has_more': len(matches) > 20,
            }
