import pytest

from scripts.image_vision_admission import admit, reserved_calls


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled", "timed_out"])
def test_uncertain_or_failed_attempts_still_reserve_four(outcome):
    assert reserved_calls([{"name": "real-vision", "status": "completed",
                            "conclusion": outcome}]) == 4


def test_skipped_job_does_not_reserve_calls():
    assert reserved_calls([{"name": "real-vision", "status": "completed",
                            "conclusion": "skipped"}]) == 0


@pytest.mark.parametrize("jobs", [[], [{"name": "real-vision", "status": "queued"}],
                                  [{"name": "real-vision", "status": "completed",
                                    "conclusion": "unknown"}]])
def test_missing_pending_or_unknown_history_fails_closed(jobs):
    with pytest.raises(ValueError):
        reserved_calls(jobs)


def test_sixth_four_call_run_is_rejected():
    admit(16)
    with pytest.raises(ValueError):
        admit(20)


def test_rerun_denied_without_network(monkeypatch, capsys):
    from scripts.image_vision_admission import main
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert main() == 2
    assert '"admitted":false' in capsys.readouterr().out

@pytest.mark.parametrize('previous, expected', [(4, 0), (5, 2)])
def test_complete_history_enforces_cross_run_limit(monkeypatch, previous, expected):
    import io
    import json
    from scripts import image_vision_admission as module
    for key, value in {'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_REPOSITORY': module.REPO,
                       'GITHUB_REF': 'refs/heads/feat/20261013-pilot-image-messages',
                       'GITHUB_RUN_ID': '99', 'GH_TOKEN': 'synthetic-not-a-secret'}.items():
        monkeypatch.setenv(key, value)
    def response(request, timeout):
        if '/workflows/' in request.full_url:
            data = {'total_count': previous + 1,
                    'workflow_runs': [{'id': 99}] + [{'id': i} for i in range(previous)]}
        else:
            data = {'total_count': 1, 'jobs': [{'name': 'real-vision', 'status': 'completed',
                                              'conclusion': 'failure'}]}
        return io.BytesIO(json.dumps(data).encode())
    monkeypatch.setattr(module.urllib.request, 'urlopen', response)
    assert module.main() == expected


@pytest.mark.parametrize('data', [
    {'total_count': 2, 'workflow_runs': [{'id': 99}]},
    {'total_count': 1, 'workflow_runs': [{'id': 98}]},
])
def test_incomplete_history_or_missing_current_run_rejected(monkeypatch, data):
    import io
    import json
    from scripts import image_vision_admission as module
    for key, value in {'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_REPOSITORY': module.REPO,
                       'GITHUB_REF': 'refs/heads/feat/20261013-pilot-image-messages',
                       'GITHUB_RUN_ID': '99', 'GH_TOKEN': 'synthetic-not-a-secret'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(module.urllib.request, 'urlopen',
                        lambda *args, **kwargs: io.BytesIO(json.dumps(data).encode()))
    assert module.main() == 2
