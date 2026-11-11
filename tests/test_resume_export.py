from io import BytesIO
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy.orm import sessionmaker
from offerpilot.repositories.resumes import ResumesRepository, ResumeCreate

from offerpilot import resume_export
from offerpilot.api import create_app, _extract_pdf_text
from offerpilot.config import Config, save_config

CONTENT = {
    'contact': {'name': '张小明', 'email': 'demo@example.test', 'phone': '010-12345678', 'location': '上海'},
    'career_intent': {'target_roles': ['软件工程师'], 'target_locations': ['北京']},
    'education': [{'school': '示例大学', 'degree': '本科', 'major': '计算机',
                   'start_date': '2020', 'end_date': '2024'}],
    'experience': [{'company': '示例公司', 'title': '开发工程师',
                    'highlights': ['实现离线导出', '性能提升 20%']}],
    'projects': [{'name': '简历助手', 'role': '开发', 'highlights': ['可编辑文档']}],
    'skills': ['Python', '中文排版'], 'additional_text': '补充事实\n第二行 <script> & literal',
    'raw_text': 'RAW_SECRET_NEVER_EXPORT',
    'import_review': {'version': 1, 'raw_text_sha256': 'REVIEW_SECRET'},
}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def create(client, content=None):
    repo = ResumesRepository(sessionmaker(bind=client.app.state.db_engine))
    return repo.create(ResumeCreate(
        title='中文简历', source='upload',
        content_json=CONTENT if content is None else content,
        parsed_data='PARSED_SECRET_NEVER_EXPORT',
        source_file_path='/never-read/PRIVATE_SECRET.pdf',
    )).id


def preview(client, rid, locale='zh-CN'):
    return client.post(f'/api/resumes/{rid}/export-preview', json={'locale': locale})


def download(client, rid, fingerprint, fmt='pdf', locale='zh-CN'):
    return client.post(f'/api/resumes/{rid}/export', json={
        'locale': locale, 'format': fmt, 'source_fingerprint': fingerprint,
    })


def pdf_text(data):
    return '\n'.join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)


def test_chinese_pdf_docx_saved_projection_and_read_only(client, tmp_path, monkeypatch):
    rid = create(client)
    before = sqlite3.connect(tmp_path / 'data.db').iterdump()
    before = list(before)
    original_read = Path.read_bytes

    def guarded_read(path):
        assert 'PRIVATE_SECRET' not in str(path)
        return original_read(path)

    monkeypatch.setattr(Path, 'read_bytes', guarded_read)
    result = preview(client, rid)
    assert result.status_code == 200, result.text
    fingerprint = result.headers['X-Resume-Export-Fingerprint']
    assert len(fingerprint) == 64
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['content-type'] == 'application/pdf'
    assert result.headers['content-disposition'].startswith('inline;')
    assert "filename*=UTF-8''" in result.headers['content-disposition']
    text = pdf_text(result.content)
    assert '张小明' in text and '补充事实' in text and '<script> & literal' in text
    assert '\x00' not in text
    assert '张小明' in _extract_pdf_text(result.content)
    assert 'demo@example.test' in _extract_pdf_text(result.content)
    assert result.headers['X-Resume-Export-Title'] == '%E4%B8%AD%E6%96%87%E7%AE%80%E5%8E%86'
    assert '求职意向' in text and '可编辑文档' in text
    for secret in ('RAW_SECRET', 'PARSED_SECRET', 'PRIVATE_SECRET', 'REVIEW_SECRET'):
        assert secret not in text
    pdf = download(client, rid, fingerprint)
    assert pdf.status_code == 200
    assert pdf.content == result.content  # preview uses exact deterministic PDF renderer
    assert pdf.headers['content-disposition'].startswith('attachment;')
    docx = download(client, rid, fingerprint, 'docx')
    assert docx.status_code == 200, docx.text
    with ZipFile(BytesIO(docx.content)) as z:
        xml = z.read('word/document.xml').decode()
        assert '张小明' in xml and '补充事实' in xml and '可编辑文档' in xml
        assert '<w:t' in xml and '<w:drawing' not in xml
        assert '<w:pBdr' not in z.read('word/styles.xml').decode()
        assert '&lt;script&gt; &amp; literal' in xml
        assert all(secret not in xml for secret in ('RAW_SECRET', 'PARSED_SECRET', 'PRIVATE_SECRET'))
        assert all('TargetMode="External"' not in z.read(n).decode()
                   for n in z.namelist() if n.endswith('.rels'))
    assert list(sqlite3.connect(tmp_path / 'data.db').iterdump()) == before


def test_conflicts_locale_changes_and_deletion(client):
    rid = create(client)
    fp = preview(client, rid).headers['X-Resume-Export-Fingerprint']
    assert download(client, rid, fp, locale='en-US').status_code == 409
    client.patch(f'/api/resumes/{rid}', json={'title': 'New title'})
    assert download(client, rid, fp).json()['error_code'] == 'resume_export_source_conflict'
    fp = preview(client, rid).headers['X-Resume-Export-Fingerprint']
    client.patch(f'/api/resumes/{rid}', json={'content_json': {'skills': ['Rust']}})
    assert download(client, rid, fp).status_code == 409
    ResumesRepository(sessionmaker(bind=client.app.state.db_engine)).delete(rid)
    assert preview(client, rid).status_code == 404
    assert download(client, rid, fp).status_code == 404
    assert preview(client, 99999).status_code == 404


def test_render_race_rechecks_source(client, monkeypatch):
    rid = create(client)
    fp = preview(client, rid).headers['X-Resume-Export-Fingerprint']
    real = resume_export.render_pdf

    def mutate(source):
        result = real(source)
        client.patch(f'/api/resumes/{rid}', json={'title': 'Edited during export'})
        return result

    monkeypatch.setattr(resume_export, 'render_pdf', mutate)
    assert download(client, rid, fp).status_code == 409


def test_raw_only_copies_cannot_export_and_review_metadata_is_ignored(client):
    rid = create(client, {'raw_text': 'private original'})
    copy = client.post(f'/api/resumes/{rid}/copy', json={}).json()
    assert copy['source'] == 'manual'
    for id_ in (rid, copy['id']):
        result = preview(client, id_)
        assert result.status_code == 422
        assert result.json()['error_code'] == 'resume_export_empty_content'
    rid = create(client)
    fp = preview(client, rid).headers['X-Resume-Export-Fingerprint']
    client.patch(f'/api/resumes/{rid}', json={'content_json': {
        **CONTENT, 'raw_text': 'different excluded raw', 'import_review': {'version': 2},
    }})
    assert download(client, rid, fp).status_code == 200


@pytest.mark.parametrize('payload', [None, {}, [], {'locale': 4}, {'locale': 'zh'},
    {'locale': 'zh-CN', 'content': CONTENT}, {'locale': 'zh-CN', 'format': 'pdf'}])
def test_strict_preview_payload(client, payload):
    result = client.post('/api/resumes/1/export-preview', json=payload)
    assert result.status_code == 422
    assert result.json()['error_code'] == 'resume_export_invalid_request'


@pytest.mark.parametrize('change', [{'format': 'txt'}, {'source_fingerprint': 'xx'},
    {'locale': False}, {'extra': 'no'}, {'source_fingerprint': None}])
def test_strict_download_payload(client, change):
    payload = {'locale': 'zh-CN', 'format': 'pdf', 'source_fingerprint': '0' * 64, **change}
    result = client.post('/api/resumes/1/export', json=payload)
    assert result.status_code == 422
    assert result.json()['error_code'] == 'resume_export_invalid_request'


@pytest.mark.parametrize(('content', 'code'), [
    ({'skills': 'Python'}, 'invalid_source'),
    ({'contact': None}, 'invalid_source'),
    ({'education': [{'school': 4}]}, 'invalid_source'),
    ({'contact': {'name': 'Ada', 'private_extra': 'secret'}}, 'unsupported_fields'),
    ({'skills': ['Python'], 'custom': 'secret'}, 'unsupported_fields'),
    ({'experience': [{'highlights': ['a'] * 101}]}, 'too_large'),
    ({'additional_text': 'a' * (32 * 1024 + 1)}, 'too_large'),
    ({'additional_text': '\x00'}, 'unsupported_text'),
    ({'contact': {'name': '张😀'}}, 'unsupported_text'),
    ({}, 'empty_content'),
])
def test_unexportable_fields_fail_explicitly(client, content, code):
    rid = create(client, content)
    response = preview(client, rid)
    assert response.status_code == 422, response.text
    assert response.json()['error_code'] == f'resume_export_{code}'


def test_malformed_saved_json_and_total_limits():
    for raw, code in [('[]', 'invalid_source'), ('{', 'invalid_source'),
                      ('x' * (resume_export.MAX_SOURCE_BYTES + 1), 'too_large'),
                      (json.dumps({'experience': [{'highlights': ['a' * 30000] * 5}]}), 'too_large')]:
        with pytest.raises(resume_export.ResumeExportError) as exc:
            resume_export.snapshot(SimpleNamespace(id=1, title='x', content_json=raw), 'zh-CN')
        assert exc.value.code == f'resume_export_{code}'


def test_long_text_flows_across_pages_and_preserves_last_line(client):
    text = ('中文长段落与 English words ' * 400) + '\nLAST_CONTENT_END'
    rid = create(client, {'contact': {'name': '长简历'}, 'additional_text': text})
    result = preview(client, rid, 'en-US')
    assert result.status_code == 200, result.text
    assert len(PdfReader(BytesIO(result.content)).pages) > 1
    assert 'LAST_CONTENT_END' in pdf_text(result.content)
    assert 'Additional information' in pdf_text(result.content)


def test_auth_enforced_for_both_export_routes(tmp_path):
    save_config(tmp_path, Config(auth_enabled=True, auth_token='secret-token'))
    client = TestClient(create_app(data_dir=tmp_path))
    for path in ('export-preview', 'export'):
        assert client.post(f'/api/resumes/1/{path}', json={'locale': 'zh-CN'}).status_code == 401
        result = client.post(f'/api/resumes/1/{path}', headers={'Authorization': 'Bearer secret-token'},
                             json={'locale': 'zh-CN'} if path == 'export-preview' else {
                                 'locale': 'zh-CN', 'format': 'pdf', 'source_fingerprint': '0' * 64,
                             })
        assert result.status_code == 404


def test_font_and_frozen_desktop_resources_are_packaged():
    assert resume_export.FONT_PATH.is_file()
    assert 'SIL OPEN FONT LICENSE' in resume_export.FONT_PATH.with_name('OFL.txt').read_text()
    spec = Path('desktop/backend.spec').read_text()
    assert '"offerpilot", "docx"' in spec
    assert 'collect_submodules("fontTools")' in spec


@pytest.mark.parametrize('body', [b'{', b'', b'[]', b'"' + b'x' * 3000 + b'"', b'[' * 1000 + b'0' + b']' * 1000])
def test_malformed_or_oversize_request_is_coded(client, body):
    for route in ('export-preview', 'export'):
        result = client.post(f'/api/resumes/1/{route}', content=body,
                             headers={'Content-Type': 'application/json'})
        assert result.status_code == 422
        assert result.json()['error_code'] == 'resume_export_invalid_request'


def test_render_error_is_private_and_coded(client, monkeypatch):
    rid = create(client)

    def fail(_source):
        raise RuntimeError('PRIVATE_SECRET_INTERNAL_PATH')

    monkeypatch.setattr(resume_export, 'render_pdf', fail)
    result = preview(client, rid)
    assert result.status_code == 500
    assert result.json()['error_code'] == 'resume_export_render_failed'
    assert 'PRIVATE_SECRET' not in result.text


def test_renamed_invalid_source_still_reports_preview_conflict(client):
    rid = create(client)
    fp = preview(client, rid).headers['X-Resume-Export-Fingerprint']
    repo = ResumesRepository(sessionmaker(bind=client.app.state.db_engine))
    repo.update(rid, {'content_json': {'skills': 5}})
    assert download(client, rid, fp).status_code == 409


@pytest.mark.parametrize('title', ['', '   ', 'Title\nInjected', 'Title\rInjected', 'Title\tTab'])
def test_title_must_be_nonblank_single_line(title):
    with pytest.raises(resume_export.ResumeExportError) as exc:
        resume_export.snapshot(SimpleNamespace(id=1, title=title,
                               content_json=json.dumps(CONTENT)), 'zh-CN')
    assert exc.value.code == 'resume_export_invalid_source'
    assert '标题' in exc.value.message
