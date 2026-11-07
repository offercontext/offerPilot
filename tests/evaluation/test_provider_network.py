import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_provider_dns_refresh_is_scoped_to_validated_send_and_endpoint():
    code = r'''
import json, socket, sys, tempfile, threading
from scripts.evaluation_subject.core_bootstrap import fence
from scripts.evaluation_subject.core_transport import SEND_PERMISSION
answer = {'ip': '203.0.113.10'}
def resolve(host, port, *args, **kwargs):
    sys.audit('socket.getaddrinfo', host, port, 0, 1, 0)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (answer['ip'], int(port)))]
socket.getaddrinfo = resolve
class Channel:
    config = {'data_dir': tempfile.gettempdir()}
    def attempt(self, purpose, **metadata): pass
channel = Channel()
fence(channel, set(), 'https://provider.test/v1')
answer['ip'] = '203.0.113.20'
SEND_PERMISSION.permitted = True
socket.getaddrinfo('provider.test', 443)
try:
    sys.audit('socket.connect', None, ('203.0.113.20', 443))
    fresh_allowed = True
except PermissionError:
    fresh_allowed = False
denials = []
for address in [('203.0.113.10', 443), ('203.0.113.21', 443), ('203.0.113.20', 444)]:
    try:
        sys.audit('socket.connect', None, address)
        denials.append(False)
    except PermissionError:
        denials.append(True)
try:
    socket.getaddrinfo('other.test', 443)
    foreign_dns_blocked = False
except PermissionError:
    foreign_dns_blocked = True
def worker():
    SEND_PERMISSION.permitted = True
    try:
        sys.audit('socket.connect', None, ('203.0.113.20', 443))
        denials.append(False)
    except PermissionError:
        denials.append(True)
t = threading.Thread(target=worker); t.start(); t.join()
SEND_PERMISSION.permitted = False
try:
    sys.audit('socket.connect', None, ('203.0.113.20', 443))
    denials.append(False)
except PermissionError:
    denials.append(True)
sys.__stdout__.write(json.dumps(dict(fresh_allowed=fresh_allowed, denials=denials, foreign_dns_blocked=foreign_dns_blocked)))
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(fresh_allowed=True, denials=[True]*5, foreign_dns_blocked=True)


@pytest.mark.parametrize('responded', [False, True])
def test_provider_failure_records_stage_chain_and_response_without_secret(tmp_path, monkeypatch, responded):
    import httpx

    from scripts.evaluation_subject.core_transport import observe, SEND_PERMISSION
    from offerpilot.context_projector.gateway import SingleCandidateAgentTransport
    from offerpilot.context_projector.projector import ModelSurfaceProjector

    for cls, name in ((httpx.Client, 'send'), (ModelSurfaceProjector, 'project'),
                      (SingleCandidateAgentTransport, 'complete_one'),
                      (SingleCandidateAgentTransport, 'stream_one')):
        monkeypatch.setattr(cls, name, getattr(cls, name))

    class Channel:
        config = dict(data_dir=str(tmp_path), trial_key=dict(execution_item_id='E09.memory_override'))
        events = []

        def emit(self, kind, payload, **kwargs):
            self.events.append(payload)

        def provider_attempt(self, **metadata):
            return 1

    channel = Channel()
    observe(channel, 'https://provider.test/v1', dict(model='test', max_input_bytes=10000, max_output_tokens=64))

    def handler(request):
        if responded:
            return httpx.Response(200, content=b'invalid-json')
        try:
            raise OSError(10060, 'must-not-export-secret')
        except OSError as exc:
            error = httpx.ConnectTimeout('must-not-export-secret')
            request.extensions['trace']('connection.connect_tcp.failed', dict(exception=error))
            raise error from exc

    body = dict(model='test', max_tokens=64, temperature=0, thinking=dict(type='disabled'), messages=[])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(Exception):
        client.post('https://provider.test/v1/chat/completions', json=body)
    failure = next(v for v in channel.events if v.get('category') == 'provider_error')
    assert failure['http_response_received'] is responded
    assert failure['failure_stage'] == ('response_capture' if responded else 'connection.connect_tcp.failed')
    assert failure['exception_chain'][0]['type'] == ('JSONDecodeError' if responded else 'ConnectTimeout')
    assert 'must-not-export-secret' not in json.dumps(channel.events)
    assert SEND_PERMISSION.permitted is False and SEND_PERMISSION.addresses == set()
