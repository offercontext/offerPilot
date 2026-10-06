import test from 'node:test';
import assert from 'node:assert/strict';
import { safeFailure, commandFailure } from '../diagnostics.mjs';

test('diagnostics preserve only allowlisted classes/codes/tool exit status', () => {
  const error = new Error('token=secret ws://127.0.0.1:1234/private-debug-endpoint');
  error.code = 'ENOENT';
  error.stack = 'private stack';
  error.cause = { headers: { authorization: 'secret' } };
  assert.deepEqual(safeFailure(error), { errorClass: 'Error', errorCode: 'ENOENT' });
  assert.deepEqual(safeFailure(commandFailure('COMMAND_EXIT', '7zip', 2)),
    { errorClass: 'Error', errorCode: 'COMMAND_EXIT', tool: '7zip', exitCode: 2 });
  assert.deepEqual(safeFailure({ name: 'token=secret', code: 'ws://private', tool: 'private/path', exitCode: 'secret' }), { errorClass: 'Error' });
});
