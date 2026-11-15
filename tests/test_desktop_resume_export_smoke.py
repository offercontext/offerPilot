"""Run the frozen-smoke export contract against real routes, plus failure injection.

This validates the smoke itself; only the Windows jobs execute frozen binaries.
"""
import importlib.util
from io import BytesIO
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from offerpilot.api import create_app


@pytest.fixture
def smoke():
    path = Path(__file__).parents[1] / 'desktop' / 'smoke-backend.py'
    spec = importlib.util.spec_from_file_location('desktop_resume_smoke', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def real_transport(smoke, tmp_path, monkeypatch):
    client = TestClient(create_app(data_dir=tmp_path))
    calls = []

    def transport(origin, path, token, *, method='GET', payload=None, headers=None, timeout=5):
        assert origin == 'http://frozen.test' and token == 'fixture-token'
        response = client.request(method, path, json=payload, headers=headers)
        calls.append((path, timeout, response.content))
        return response.status_code, response.content, dict(response.headers)

    monkeypatch.setattr(smoke, 'request_details', transport)
    return transport, calls


def test_frozen_export_smoke_uses_real_chinese_routes_and_preserves_source(smoke, real_transport):
    _, calls = real_transport
    smoke.smoke_resume_exports('http://frozen.test', 'fixture-token')
    previews = [body for path, _, body in calls if path.endswith('/export-preview')]
    assert len(previews) == 2 and previews[0] == previews[1]
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(previews[0])).pages)
    assert '张小明' in text and '明确保存的补充事实' in text
    assert 'PRIVATE_SOURCE' not in text and 'PRIVATE_REVIEW' not in text
    assert all(timeout == (90 if '/export' in path else 5) for path, timeout, _ in calls)


@pytest.mark.parametrize('fault, message', [
    ('preview', 'Chinese PDF preview failed'),
    ('pdf', 'PDF download differs'),
    ('docx', 'DOCX lacks saved Chinese fields'),
    ('fingerprint', 'fingerprint differs'),
    ('privacy', 'Private source fields changed'),
    ('stale', 'accepted a stale preview'),
])
def test_frozen_export_smoke_fails_closed(smoke, real_transport, monkeypatch, fault, message):
    transport, _ = real_transport

    def faulty(origin, path, token, **kwargs):
        status, body, headers = transport(origin, path, token, **kwargs)
        fmt = (kwargs.get('payload') or {}).get('format')
        if fault == 'stale' and status == 409:
            return 200, b'%PDF-invalid stale download', headers
        if status != 200:
            return status, body, headers
        if fault == 'preview' and path.endswith('/export-preview'):
            return 500, b'render failed', headers
        if fault == 'pdf' and fmt == 'pdf':
            body += b'changed'
        if fault == 'fingerprint' and fmt == 'pdf':
            headers['x-resume-export-fingerprint'] = '0' * 64
        if fault == 'docx' and fmt == 'docx':
            from zipfile import ZipFile
            output = BytesIO()
            with ZipFile(BytesIO(body)) as original, ZipFile(output, 'w') as modified:
                for name in original.namelist():
                    data = original.read(name)
                    if name == 'word/document.xml':
                        data = data.replace('张小明'.encode(), b'wrong')
                    modified.writestr(name, data)
            body = output.getvalue()
        if fault == 'privacy' and path.startswith('/api/resumes/2/') and (
                path.endswith('/export-preview') or fmt == 'pdf'):
            body += b'PRIVATE_SOURCE_MUST_NOT_EXPORT'
        return status, body, headers

    monkeypatch.setattr(smoke, 'request_details', faulty)
    with pytest.raises(RuntimeError, match=message):
        smoke.smoke_resume_exports('http://frozen.test', 'fixture-token')


def test_request_wrapper_keeps_existing_shape_and_default_timeout(smoke, monkeypatch):
    class Response:
        status = 200
        headers = {'Content-Type': 'application/json'}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return b'{"ok":true}'

    calls = []

    def open_request(req, timeout):
        calls.append((req, timeout))
        return Response()

    monkeypatch.setattr(smoke.HTTP, 'open', open_request)
    assert smoke.request('http://localhost', '/api/test', 'test-token',
                         method='POST', payload={'value': 1}) == (200, b'{"ok":true}')
    req, timeout = calls[0]
    assert timeout == 5
    assert req.get_header('X-offerpilot-desktop-token') == 'test-token'
    assert json.loads(req.data) == {'value': 1}
