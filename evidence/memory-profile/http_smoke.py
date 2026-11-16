"""Isolated real HTTP memory smoke; run with the repository virtualenv."""
import socket
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn

from offerpilot.api import create_app
from offerpilot.context_projector.loader import ContextSourceLoader
from offerpilot.context_sources.loader import load_optional_sources
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import Conversation


def command(version, content='', action='confirm'):
    return dict(mutation_id=str(uuid4()), expected_version=version,
                content=content, action=action, confirmed=True)


with tempfile.TemporaryDirectory(prefix='offerpilot-memory-http-') as temp:
    data = Path(temp)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(data_dir=data), log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
            first = command(0, 'HTTP old memory')
            response = client.post('/api/confirmed-memory', json=first)
            assert response.status_code == 200, response.text
            item = response.json()
            url = '/api/confirmed-memory/' + item['id']
            assert client.post('/api/confirmed-memory', json=first).json() == item
            sessions = session_factory_for_data_dir(data)
            with sessions() as session:
                conversation = Conversation(title='memory smoke')
                session.add(conversation)
                session.commit()
                cid = conversation.id
            loader = ContextSourceLoader(data / 'data.db')
            try:
                assert client.post(url, json=command(1, 'HTTP corrected memory')).status_code == 200
                assert client.post(url, json=command(1, 'stale')).status_code == 409
                recalled = str(load_optional_sources(loader, cid, 'question').contributors)
                assert 'HTTP corrected memory' in recalled and 'HTTP old memory' not in recalled
                invalid = client.post(url, json={**command(2, 'unconfirmed'), 'confirmed': False})
                assert invalid.status_code == 422
                assert 'unconfirmed' not in invalid.text
                deletion = command(2, action='delete')
                assert client.post(url, json=deletion).status_code == 200
                assert client.post(url, json=deletion).status_code == 200
                assert client.get(url).status_code == 404
                assert client.get('/api/confirmed-memory').json() == []
                assert client.post('/api/confirmed-memory', json=first).status_code == 410
                recalled = str(load_optional_sources(loader, cid, 'question').contributors)
                assert 'HTTP corrected memory' not in recalled and 'HTTP old memory' not in recalled
            finally:
                loader._pool.close()
        print('PASS real HTTP create/replay/edit/stale CAS/unconfirmed rejection/delete/replay/list/detail and actual recall consistency')
    finally:
        server.should_exit = True
        thread.join(15)
        assert not thread.is_alive()
