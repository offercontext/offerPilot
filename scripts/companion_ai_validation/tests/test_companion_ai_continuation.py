"""Offline immutable predecessor, strict ledger replay and lineage mutation tests."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

from scripts.companion_ai_validation import continuation as c


def evidence_field(original):
    return 'ORIGINAL_EVIDENCE_PATH' if original is True else 'EVIDENCE_PATH' if original == 'third' else 'SECOND_EVIDENCE_PATH'


def selected_evidence_path(original):
    return getattr(c, evidence_field(original))


def expected_members(original):
    return c.ORIGINAL_MEMBERS if original is True else c.MEMBERS if original == 'third' else c.SECOND_MEMBERS


def members(*, original=False):
    with zipfile.ZipFile(selected_evidence_path(original)) as archive:
        return {name: archive.read(name) for name in c.SECOND_MEMBERS}


def ledger(*, original=False):
    return [json.loads(line) for line in members(original=original)['budget.jsonl'].splitlines()]


def replay(raw, *, original=False):
    return c._replay_ledger(raw) if original is True else c._replay_correction_ledger(raw, third=original == "third")


def verify_archive(raw, *, original=False):
    return (c._verify_original_archive(raw) if original is True else
            c._verify_archive(raw) if original == "third" else c._verify_second_archive(raw))


def encoded(rows):
    return b''.join(json.dumps(row, separators=(',', ':')).encode() + b'\n' for row in rows)


def repack(values, monkeypatch, *, original=False, extra=None, duplicate=None, mode=None):
    """Repin outer hashes ONLY in tests to exercise inner validation independently."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in values.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o600 if mode is None else mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
        if extra:
            archive.writestr(extra, b'x')
        if duplicate:
            with pytest.warns(UserWarning, match='Duplicate name'):
                archive.writestr(duplicate, values[duplicate])
    raw = output.getvalue()
    prefix = 'ORIGINAL_' if original is True else '' if original == 'third' else 'SECOND_'
    monkeypatch.setattr(c, prefix + 'ARCHIVE_SHA256', hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(c, prefix + 'ARCHIVE_SIZE', len(raw))
    monkeypatch.setattr(c, prefix + 'MEMBERS', {name: (len(value), hashlib.sha256(value).hexdigest())
                                     for name, value in values.items()})
    return raw


def test_real_pinned_bundle_verifies_exact_known_liability_and_no_arguments():
    original = c.SECOND_EVIDENCE_PATH.read_bytes()
    assert len(original) == 18554
    assert hashlib.sha256(original).hexdigest() == c.SECOND_ARCHIVE_SHA256
    prior = c.verified_predecessor()
    assert (prior.settled_micro_cny, prior.reserved_micro_cny, prior.request_count) == (315652, 0, 36)
    assert prior.run_id == 40060633208 and prior.artifact_id == 12547790764
    assert c.BUDGET - prior.settled_micro_cny == 19684348
    with pytest.raises(FrozenInstanceError):
        prior.settled_micro_cny = 0
    with pytest.raises(TypeError):
        c.verified_predecessor(0)
    with pytest.raises(TypeError):
        c.lineage_fields('/tmp/arbitrary-ledger')


def test_lineage_is_fresh_exact_and_fixed_not_a_new_budget():
    fields = c.lineage_fields()
    assert set(fields) == c.LINEAGE_KEYS
    assert fields['campaign_id'] == 'one-shot-20261021'
    assert fields['correction_ordinal'] == 3
    assert fields['prior_request_count'] == 36
    assert fields['price_valid_until'] == int(datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp())
    c.validate_lineage({**fields, 'schema': 2, 'source_sha': 'b' * 40})
    fields['prior_settled_micro_cny'] = 0
    assert c.lineage_fields()['prior_settled_micro_cny'] == 315652
    with pytest.raises(c.ContinuationError, match='CONTINUATION_LINEAGE_MISMATCH'):
        c.validate_lineage(fields)


@pytest.mark.parametrize('key', sorted(c.LINEAGE_KEYS))
@pytest.mark.parametrize('mutation', ['missing', 'wrong_value', 'wrong_type'])
def test_every_lineage_field_is_mandatory_typed_and_exact(key, mutation):
    fields = c.lineage_fields()
    if mutation == 'missing':
        del fields[key]
    elif mutation == 'wrong_value':
        fields[key] = fields[key] + 1 if type(fields[key]) is int else fields[key] + '-changed'
    else:
        fields[key] = float(fields[key]) if type(fields[key]) is int else None
    with pytest.raises(c.ContinuationError, match='CONTINUATION_LINEAGE_MISMATCH'):
        c.validate_lineage(fields)


def test_boolean_cannot_equal_numeric_ordinal():
    fields = c.lineage_fields()
    fields['correction_ordinal'] = True
    with pytest.raises(c.ContinuationError):
        c.validate_lineage(fields)


@pytest.mark.parametrize('original', [False, True, 'third'])
@pytest.mark.parametrize('damage', ['missing', 'directory', 'symlink', 'changed', 'short', 'long'])
def test_unavailable_or_changed_bundle_never_supplies_a_balance(tmp_path, monkeypatch, damage, original):
    evidence_path = selected_evidence_path(original)
    field_name = evidence_field(original)
    path = tmp_path / 'bundle.zip'
    original = evidence_path.read_bytes()
    if damage == 'directory':
        path.mkdir()
    elif damage == 'symlink':
        path.symlink_to(evidence_path)
    elif damage != 'missing':
        raw = original[:-1] if damage == 'short' else original + b'x' if damage == 'long' else b'x' + original[1:]
        path.write_bytes(raw)
    fields = c.lineage_fields()
    monkeypatch.setattr(c, field_name, path)
    for method in (c.verified_predecessor, c.lineage_fields, lambda: c.validate_lineage(fields)):
        with pytest.raises(c.ContinuationError):
            method()


@pytest.mark.parametrize('original', [False, True, 'third'])
@pytest.mark.parametrize('method', ['verified_predecessor', 'lineage_fields', 'validate_lineage'])
def test_verification_reopens_bytes_instead_of_caching_success(tmp_path, monkeypatch, original, method):
    path = tmp_path / 'bundle.zip'
    evidence_path = selected_evidence_path(original)
    field_name = evidence_field(original)
    path.write_bytes(evidence_path.read_bytes())
    monkeypatch.setattr(c, field_name, path)
    fields = c.lineage_fields()
    def invoke():
        return c.validate_lineage(fields) if method == 'validate_lineage' else getattr(c, method)()
    invoke()
    path.write_bytes(b'changed after first check')
    with pytest.raises(c.ContinuationError):
        invoke()


def test_normal_closed_true_is_not_an_unknown_halt():
    rows = ledger(original=True)
    assert rows[-1]['event'] == 'closed' and rows[-1]['halted'] is True
    result = c._replay_ledger(encoded(rows))
    assert result == {'schema': 1, 'source_sha': c.ORIGINAL_SOURCE_SHA,
                      'request_count': 12, 'settled_micro_cny': 103154,
                      'reserved_micro_cny': 0, 'budget_micro_cny': 20000000, 'halted': False}


@pytest.mark.parametrize('index,key,value', [
    (0, 'schema', True), (0, 'schema', 1.0), (0, 'source_sha', 'a' * 40),
    (0, 'settled_micro_cny', 103154), (0, 'reserved_micro_cny', 3000000),
    (0, 'budget_micro_cny', 40000000), (0, 'request_count', 0.0),
    (1, 'request_count', True), (1, 'reserved_micro_cny', 0), (1, 'reserved_micro_cny', 2999999),
    (1, 'settled_micro_cny', -1), (1, 'body_sha256', 'x' * 64),
    (1, 'operation', 'different'), (1, 'halted', True),
    (2, 'cost_micro_cny', 0), (2, 'cost_micro_cny', 8192.0),
    (2, 'prompt_tokens', True), (2, 'prompt_tokens', -1), (2, 'prompt_tokens', 1048577),
    (2, 'completion_tokens', 513), (2, 'completion_tokens', 28.0),
    (2, 'reserved_micro_cny', 3000000), (2, 'event', 'halted'),
    (3, 'calls', True), (3, 'calls', 2), (3, 'halted', True),
    (-1, 'halted', False), (-1, 'event', 'halted'), (-1, 'reserved_micro_cny', 3000000),
    (-1, 'settled_micro_cny', 0), (-1, 'request_count', 11),
])
def test_replay_rejects_unknown_refund_wrong_types_and_arithmetic(index, key, value):
    rows = ledger(original=True)
    rows[index][key] = value
    with pytest.raises(c.ContinuationError):
        c._replay_ledger(encoded(rows))


@pytest.mark.parametrize('original', [False, True, 'third'])
@pytest.mark.parametrize('mutation', ['drop', 'extra', 'reorder', 'extra_key', 'missing_key',
                                      'duplicate_key', 'nonfinite', 'no_newline', 'blank_line'])
def test_replay_requires_complete_unambiguous_ordered_journal(mutation, original):
    rows = ledger(original=original)
    if mutation == 'drop':
        rows.pop()
    elif mutation == 'extra':
        rows.append(deepcopy(rows[-1]))
    elif mutation == 'reorder':
        rows[1], rows[2] = rows[2], rows[1]
    elif mutation == 'extra_key':
        rows[0]['starting_balance'] = 0
    elif mutation == 'missing_key':
        rows[0].pop('halted')
    raw = encoded(rows)
    schema = b'1' if original is True else b'2'
    if mutation == 'duplicate_key':
        raw = raw.replace(b'"schema":' + schema, b'"schema":0,"schema":' + schema, 1)
    elif mutation == 'nonfinite':
        raw = raw.replace(b'"schema":' + schema, b'"schema":NaN', 1)
    elif mutation == 'no_newline':
        raw = raw[:-1]
    elif mutation == 'blank_line':
        raw += b'\n'
    with pytest.raises(c.ContinuationError):
        replay(raw, original=original)


@pytest.mark.parametrize('original', [False, True, 'third'])
@pytest.mark.parametrize('damage', ['extra', 'traversal', 'duplicate', 'symlink', 'directory'])
def test_archive_structure_cannot_smuggle_other_files(monkeypatch, damage, original):
    kwargs = {}
    if damage in ('extra', 'traversal'):
        kwargs['extra'] = '../elsewhere' if damage == 'traversal' else 'unexpected.txt'
    elif damage == 'duplicate':
        kwargs['duplicate'] = 'budget.jsonl'
    else:
        kwargs['mode'] = (stat.S_IFLNK if damage == 'symlink' else stat.S_IFDIR) | 0o600
    raw = repack(members(original=original), monkeypatch, original=original, **kwargs)
    with pytest.raises(c.ContinuationError):
        verify_archive(raw, original=original)


@pytest.mark.parametrize('original', [False, True, 'third'])
@pytest.mark.parametrize('damage', ['summary_budget', 'summary_type', 'summary_scope', 'summary_count',
                                    'summary_responses', 'response_order', 'response_read_only',
                                    'response_count', 'response_text'])
def test_summary_and_successful_dialogue_must_match_replayed_budget(monkeypatch, damage, original):
    values = members(original=original)
    summary = json.loads(values['summary.json'])
    responses = [json.loads(line) for line in values['responses.jsonl'].splitlines()]
    if damage == 'summary_budget':
        summary['budget']['settled_micro_cny'] = 0
    elif damage == 'summary_type':
        summary['budget']['stages_complete'] = 12.0
    elif damage == 'summary_scope':
        summary['matrix']['pass_scope'] = 'persona_accepted'
    elif damage == 'summary_count':
        summary['matrix']['ordinary_count'] = 8
    elif damage == 'summary_responses':
        summary['matrix']['responses'] = []
    else:
        if damage == 'response_order':
            responses[0], responses[1] = responses[1], responses[0]
        elif damage == 'response_read_only':
            responses[0]['ordinary_turn_read_only'] = 1
        elif damage == 'response_count':
            responses.pop()
        else:
            responses[0]['assistant_text'] = ''
        summary['matrix']['responses'] = responses
    values['summary.json'] = json.dumps(summary).encode()
    values['responses.jsonl'] = encoded(responses)
    raw = repack(values, monkeypatch, original=original)
    with pytest.raises(c.ContinuationError):
        verify_archive(raw, original=original)


def test_evidence_module_has_no_broker_or_network_dependency():
    source = Path(c.__file__).read_text()
    assert 'import broker' not in source and 'from scripts.' not in source
    assert 'import http' not in source and 'import urllib' not in source


def test_fixed_archives_have_distinct_pins_and_each_replays_exactly_38_rows():
    assert c.ORIGINAL_EVIDENCE_PATH != c.SECOND_EVIDENCE_PATH
    assert c.ORIGINAL_ARCHIVE_SHA256 != c.ARCHIVE_SHA256
    for original in (False, True, 'third'):
        path = selected_evidence_path(original)
        expected = expected_members(original)
        assert len(ledger(original=original)) == 38
        state = verify_archive(path.read_bytes(), original=original)
        assert state['settled_micro_cny'] == (103154 if original is True else 315652 if original == 'third' else 208516)
        assert state['reserved_micro_cny'] == 0
        with zipfile.ZipFile(path) as archive:
            assert len(archive.infolist()) == 3
            assert set(archive.namelist()) == set(expected)
            for name, (size, digest) in expected.items():
                raw = archive.read(name)
                assert (len(raw), hashlib.sha256(raw).hexdigest()) == (size, digest)


def test_schema2_is_historical_ordinal1_and_inherits_original_close_exactly_once():
    rows = ledger()
    original = c._replay_ledger(encoded(ledger(original=True)))
    first, last = rows[0], rows[-1]
    for field in ('settled_micro_cny', 'reserved_micro_cny', 'request_count'):
        assert first['prior_' + field] == original[field]
    assert first['settled_micro_cny'] == 103154
    assert first['request_count'] == first['batch_request_count'] == 0
    assert first['lifetime_request_count'] == 12
    assert last['batch_settled_micro_cny'] == 105362
    assert last['lifetime_settled_micro_cny'] == last['settled_micro_cny'] == 208516
    assert last['lifetime_request_count'] == 24
    assert last['request_count'] == last['batch_request_count'] == 12
    historical_deadline = int(datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp())
    for row in rows:
        assert row['correction_ordinal'] == 1
        assert row['price_valid_until'] == historical_deadline
    result = c._replay_correction_ledger(encoded(rows))
    assert result == {key: value for key, value in last.items() if key != 'event'} | {'halted': False}
    assert c.verified_predecessor().settled_micro_cny not in (105362, 103154 + 208516)


@pytest.mark.parametrize('index', [0, 1, 2, 3, -1])
@pytest.mark.parametrize('key', sorted(c.LINEAGE_KEYS))
@pytest.mark.parametrize('mutation', ['missing', 'wrong_value', 'wrong_type'])
@pytest.mark.parametrize('third', [False, True])
def test_every_historical_lineage_field_is_fixed_on_every_transition(index, key, mutation, third):
    rows = ledger(original="third" if third else False)
    value = rows[index][key]
    if mutation == 'missing':
        del rows[index][key]
    elif mutation == 'wrong_type':
        rows[index][key] = float(value) if type(value) is int else None
    else:
        rows[index][key] = value + 1 if type(value) is int else value + '-changed'
    with pytest.raises(c.ContinuationError):
        c._replay_correction_ledger(encoded(rows), third=third)


@pytest.mark.parametrize('index', range(38))
@pytest.mark.parametrize('key', [
    'batch_request_count', 'lifetime_request_count', 'batch_settled_micro_cny',
    'batch_reserved_micro_cny', 'lifetime_settled_micro_cny', 'lifetime_reserved_micro_cny',
    'lifetime_liability_micro_cny', 'lifetime_remaining_micro_cny',
])
@pytest.mark.parametrize('third', [False, True])
def test_schema2_accounting_cannot_diverge_at_any_row(index, key, third):
    rows = ledger(original="third" if third else False)
    rows[index][key] += 1
    with pytest.raises(c.ContinuationError):
        c._replay_correction_ledger(encoded(rows), third=third)


@pytest.mark.parametrize('key', [
    'schema', 'request_count', 'settled_micro_cny', 'reserved_micro_cny',
    'batch_request_count', 'lifetime_request_count', 'batch_settled_micro_cny',
    'batch_reserved_micro_cny', 'lifetime_settled_micro_cny', 'lifetime_reserved_micro_cny',
    'lifetime_liability_micro_cny', 'lifetime_remaining_micro_cny',
])
@pytest.mark.parametrize('value', [True, False, 0.0, None, '0'])
@pytest.mark.parametrize('third', [False, True])
def test_schema2_accounting_types_are_not_coerced(key, value, third):
    rows = ledger(original="third" if third else False)
    rows[0][key] = value
    with pytest.raises(c.ContinuationError):
        c._replay_correction_ledger(encoded(rows), third=third)


@pytest.mark.parametrize('index,key,value', [
    (0, 'source_sha', c.ORIGINAL_SOURCE_SHA), (0, 'schema', 1),
    (0, 'settled_micro_cny', 0), (0, 'request_count', 12),
    (0, 'price_valid_until', c.PRICE_VALID_UNTIL), (0, 'correction_ordinal', 2),
    (1, 'request_count', True), (1, 'reserved_micro_cny', 0),
    (1, 'reserved_micro_cny', 2999999), (1, 'settled_micro_cny', -1),
    (1, 'body_sha256', 'x' * 64), (1, 'body_sha256', None), (1, 'operation', 'different'),
    (2, 'cost_micro_cny', 0), (2, 'cost_micro_cny', 8518.0),
    (2, 'prompt_tokens', True), (2, 'prompt_tokens', -1), (2, 'prompt_tokens', 1048577),
    (2, 'completion_tokens', 513), (2, 'completion_tokens', 29.0),
    (2, 'reserved_micro_cny', 3000000), (2, 'event', 'halted'),
    (3, 'calls', True), (3, 'calls', 2),
    (-1, 'halted', False), (-1, 'event', 'halted'), (-1, 'reserved_micro_cny', 3000000),
    (-1, 'settled_micro_cny', 105362), (-1, 'settled_micro_cny', 103154 + 208516),
    (-1, 'request_count', 24), (-1, 'lifetime_request_count', 12),
])
def test_schema2_rejects_refunds_double_counts_unknowns_and_repriced_history(index, key, value):
    rows = ledger()
    rows[index][key] = value
    with pytest.raises(c.ContinuationError):
        c._replay_correction_ledger(encoded(rows))


@pytest.mark.parametrize('index', range(37))
@pytest.mark.parametrize('third', [False, True])
def test_no_halted_state_before_normal_close_can_be_inherited(index, third):
    rows = ledger(original="third" if third else False)
    rows[index]['halted'] = True
    with pytest.raises(c.ContinuationError):
        c._replay_correction_ledger(encoded(rows), third=third)


@pytest.mark.parametrize('field', ['settled_micro_cny', 'reserved_micro_cny', 'request_count'])
def test_public_chain_cross_checks_original_close_against_correction_open(monkeypatch, field):
    state = c._verify_original_archive(c.ORIGINAL_EVIDENCE_PATH.read_bytes())
    state[field] += 1
    monkeypatch.setattr(c, '_verify_original_archive', lambda raw: state)
    with pytest.raises(c.ContinuationError):
        c.verified_predecessor()


@pytest.mark.parametrize('original', [False, True, 'third'])
@pytest.mark.parametrize('member', ['budget.jsonl', 'summary.json', 'responses.jsonl'])
def test_each_member_digest_is_checked_even_after_outer_hash_is_repinned(monkeypatch, original, member):
    values = members(original=original)
    expected = expected_members(original)
    values[member] = b'x' + values[member][1:]
    raw = repack(values, monkeypatch, original=original)
    monkeypatch.setattr(c, 'ORIGINAL_MEMBERS' if original is True else 'MEMBERS' if original == 'third' else 'SECOND_MEMBERS', expected)
    with pytest.raises(c.ContinuationError, match='PREDECESSOR_MEMBER_MISMATCH'):
        verify_archive(raw, original=original)


@pytest.mark.parametrize('ordinal', [0, 1, 2, 4, 100, True, 3.0])
def test_no_other_ordinal_can_be_admitted(ordinal):
    fields = c.lineage_fields()
    fields['correction_ordinal'] = ordinal
    with pytest.raises(c.ContinuationError, match='CONTINUATION_LINEAGE_MISMATCH'):
        c.validate_lineage(fields)
    with pytest.raises(TypeError):
        c.verified_predecessor(ordinal=ordinal)


def test_current_plan_and_deadline_cannot_rewrite_historical_replay(monkeypatch):
    original = c.ORIGINAL_EVIDENCE_PATH.read_bytes()
    correction = c.SECOND_EVIDENCE_PATH.read_bytes()
    original_state = c._verify_original_archive(original)
    correction_state = c._verify_second_archive(correction)
    historical_operations = c.HISTORICAL_OPERATIONS
    historical_digest = c.HISTORICAL_OPERATIONS_SHA256
    changed_operations = ('future-unapproved-operation',)
    changed_digest = hashlib.sha256(json.dumps(
        changed_operations, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
    ).encode()).hexdigest()
    monkeypatch.setattr(c, 'OPERATIONS', changed_operations)
    monkeypatch.setattr(c, 'OPERATIONS_SHA256', changed_digest)
    monkeypatch.setattr(c, 'PRICE_VALID_UNTIL', c.PRICE_VALID_UNTIL + 86400)
    assert c._verify_original_archive(original) == original_state
    assert c._verify_second_archive(correction) == correction_state
    assert c.HISTORICAL_OPERATIONS == historical_operations
    assert c.HISTORICAL_OPERATIONS_SHA256 == historical_digest
    assert c.lineage_fields()['operations_sha256'] == changed_digest != historical_digest
    assert c.verified_predecessor().settled_micro_cny == 315652


@pytest.mark.parametrize('field', ['settled_micro_cny', 'reserved_micro_cny', 'request_count'])
def test_third_open_is_bound_to_second_cumulative_close(monkeypatch, field):
    state = c._verify_second_archive(c.SECOND_EVIDENCE_PATH.read_bytes())
    state['lifetime_' + field] += 1
    monkeypatch.setattr(c, '_verify_second_archive', lambda raw: state)
    with pytest.raises(c.ContinuationError):
        c.verified_predecessor()


def test_third_artifact_exact_ordinal_two_spending_and_current48_cap():
    raw = c.EVIDENCE_PATH.read_bytes()
    assert len(raw) == 17747 and hashlib.sha256(raw).hexdigest() == c.ARCHIVE_SHA256
    result = c._verify_archive(raw)
    assert result['correction_ordinal'] == 2
    assert result['prior_settled_micro_cny'] == 208516
    assert result['prior_request_count'] == 24
    assert result['batch_settled_micro_cny'] == 107136
    assert result['lifetime_settled_micro_cny'] == 315652
    assert result['lifetime_request_count'] == 36
    assert result['lifetime_reserved_micro_cny'] == 0
    assert c.verified_predecessor().request_count + len(c.OPERATIONS) == 48
    assert len(set(c.OPERATIONS)) == 12
    assert c.OPERATIONS != c.HISTORICAL_OPERATIONS


def test_current_candidate_repricing_cannot_change_third_archive(monkeypatch):
    raw = c.EVIDENCE_PATH.read_bytes()
    before = c._verify_archive(raw)
    monkeypatch.setattr(c, 'PRICE_VALID_UNTIL', c.PRICE_VALID_UNTIL + 86400)
    assert c._verify_archive(raw) == before
