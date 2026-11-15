import test from 'node:test';
import assert from 'node:assert/strict';
import { validateSettingsBackup, validateWorkspaceBackup } from '../backup-format.mjs';
import { settingsPayload, configPayload, makeWorkspaceZip, makeZip } from './backup-fixtures.mjs';

const json = value => Buffer.from(JSON.stringify(value));
function failsSettings(bytes) {
  assert.throws(() => validateSettingsBackup(bytes), { message: 'Settings backup validation failed' });
}
function failsWorkspace(bytes, options) {
  assert.throws(() => validateWorkspaceBackup(bytes, options), { message: 'Workspace backup validation failed' });
}
function changeProvider(change) {
  const payload = settingsPayload(); change(payload.providers[0]); return json(payload);
}
function centralOffsets(bytes) {
  const end = bytes.length - 22, count = bytes.readUInt16LE(end + 10);
  const offsets = []; let offset = bytes.readUInt32LE(end + 16);
  for (let index = 0; index < count; index++) {
    offsets.push(offset);
    offset += 46 + bytes.readUInt16LE(offset + 28) + bytes.readUInt16LE(offset + 30) + bytes.readUInt16LE(offset + 32);
  }
  return offsets;
}
function mutateHeader(bytes, index, localPosition, centralPosition, value, width = 4) {
  const central = centralOffsets(bytes)[index], local = bytes.readUInt32LE(central + 42);
  const write = width === 2 ? 'writeUInt16LE' : 'writeUInt32LE';
  if (localPosition !== null) bytes[write](value, local + localPosition);
  if (centralPosition !== null) bytes[write](value, central + centralPosition);
  return bytes;
}

test('settings v1 accepts the pinned provider schema and returns only safe counts/booleans', () => {
  const result = validateSettingsBackup(json(settingsPayload()));
  assert.deepEqual(result, { valid: true, schemaVersion: 1, providerCount: 1,
    fallbackProviderCount: 0, credentialValuesAbsent: true });
  const payload = settingsPayload();
  payload.providers.push({ ...payload.providers[0], id: 'fallback' });
  payload.fallback_provider_ids.push('fallback');
  assert.equal(validateSettingsBackup(json(payload)).fallbackProviderCount, 1);
  assert.ok(Object.values(result).every(value => typeof value === 'boolean' || typeof value === 'number'));
});

test('settings rejects missing, extra, credential, mistyped, and wrong-version fields', () => {
  const missing = settingsPayload(); delete missing.log_level;
  for (const payload of [missing, [], null, {}, settingsPayload({ version: 2 }),
    settingsPayload({ version: '1' }), settingsPayload({ exported_at: 'tomorrow' }),
    settingsPayload({ runtime_mode: 'other' }), settingsPayload({ log_level: 'TRACE' }),
    settingsPayload({ auth_enabled: 0 }), settingsPayload({ has_auth_token: true }),
    settingsPayload({ chat_auto_approve_writes: 'false' }), settingsPayload({ auth_token: '' }),
    settingsPayload({ confirmation_secret: '' }), settingsPayload({ providers: [] })]) failsSettings(json(payload));
  for (const change of [provider => { delete provider.enabled; },
    provider => { provider.api_key = ''; }, provider => { provider.has_api_key = true; },
    provider => { provider.has_api_key = 'false'; }, provider => { provider.model = {}; },
    provider => { provider.supports_json_schema = 1; }, provider => { provider.context_window = -1; },
    provider => { provider.context_window = 1.5; }, provider => { provider.max_output_tokens = Number.MAX_SAFE_INTEGER + 1; }]) {
    failsSettings(changeProvider(change));
  }
});

test('settings provider IDs and fallback references must be unambiguous', () => {
  for (const change of [payload => { payload.active_provider_id = 'absent'; },
    payload => { payload.providers.push({ ...payload.providers[0] }); },
    payload => { payload.fallback_provider_ids = ['absent']; },
    payload => { payload.fallback_provider_ids = ['default']; },
    payload => { payload.providers.push({ ...payload.providers[0], id: 'other' }); payload.fallback_provider_ids = ['other', 'other']; }]) {
    const payload = settingsPayload(); change(payload); failsSettings(json(payload));
  }
});

test('JSON decoding rejects empty, malformed UTF-8, duplicate escaped keys, and deeply nested input', () => {
  for (const bytes of [Buffer.alloc(0), Buffer.from('{'), Buffer.from([0xff]), Buffer.alloc(1024 * 1024 + 1),
    Buffer.from('{"version":1,"vers\\u0069on":1}'), Buffer.from('['.repeat(66) + '0' + ']'.repeat(66))]) failsSettings(bytes);
  const settings = JSON.stringify(settingsPayload());
  failsSettings(Buffer.from(settings.replace('"providers":[', '"providers":[{"api_key":"hidden"}],"providers":[')));
  assert.equal(validateSettingsBackup(Buffer.from(settings.replace('"Default"', '"\\\"escaped\\\\label"'))).valid, true);
});

test('workspace accepts stored and DEFLATE files, Unicode paths, empty logs, and binary data', () => {
  for (const method of [0, 8]) {
    const bytes = makeWorkspaceZip([
      { name: '日志/验证.log', data: 'synthetic log\n', method },
      { name: 'empty.log', data: '', method },
      { name: 'attachments/synthetic.bin', data: Buffer.from([0, 1, 127, 128, 255]), method },
    ], { method });
    const result = validateWorkspaceBackup(bytes);
    assert.deepEqual(result, { valid: true, entryCount: 5,
      uncompressedBytes: json(configPayload()).length + 100 + Buffer.byteLength('synthetic log\n') + 5,
      configPresent: true, sqlitePresent: true, credentialsRedacted: true, keyringAbsent: true,
      forbiddenValueCount: 0, forbiddenValuesAbsent: true });
    assert.ok(Object.values(result).every(value => typeof value === 'boolean' || typeof value === 'number'));
  }
});

test('workspace accepts signed and unsigned standard data descriptors', () => {
  for (const unsignedDescriptor of [false, true]) {
    assert.equal(validateWorkspaceBackup(makeWorkspaceZip([{ name: 'stream.log', data: 'streamed log',
      descriptor: true, unsignedDescriptor }])).valid, true);
  }
});

test('workspace reads an independently generated Python zipfile DEFLATE export', () => {
  // Python 3 zipfile.ZipFile(BytesIO(), 'w', compression=ZIP_DEFLATED), with
  // ZipInfo timestamps fixed at 2026-10-08 09:30:00. ASCII and UTF-8 name flags
  // differ exactly as in _build_backup_archive; no Python dependency at runtime.
  const bytes = Buffer.from('UEsDBBQAAAAIAMBLSF3vnVfvQQAAAE0AAAALAAAAY29uZmlnLmpzb26rVkosyIzPTq1UslJQUtJRUEosLcmIL8nPTs2DiSTn56VlFuUmlmTm58UXpyYXpZbApAqK8ssyU1KLioEC0bG1AFBLAwQUAAAACADAS0hdt6FXhRQAAABkAAAABwAAAGRhdGEuZGILDvTJLElVSMsvyk0sUTBmoAUAAFBLAwQUAAAICADAS0hdSZMPoBAAAAAOAAAAEQAAAOaXpeW/ly/pqozor4EubG9nK67MK8lILclMVsjJT+cCAFBLAQIUAxQAAAAIAMBLSF3vnVfvQQAAAE0AAAALAAAAAAAAAAAAAACAAQAAAABjb25maWcuanNvblBLAQIUAxQAAAAIAMBLSF23oVeFFAAAAGQAAAAHAAAAAAAAAAAAAACAAWoAAABkYXRhLmRiUEsBAhQDFAAACAgAwEtIXUmTD6AQAAAADgAAABEAAAAAAAAAAAAAAIABowAAAOaXpeW/ly/pqozor4EubG9nUEsFBgAAAAADAAMArQAAAOIAAAAAAA==', 'base64');
  const result = validateWorkspaceBackup(bytes);
  assert.equal(result.valid, true); assert.equal(result.entryCount, 3);
  assert.equal(result.uncompressedBytes, 191);
});

test('workspace accepts Windows Python ZipInfo file modes and writestr permission-only attributes', () => {
  const bytes = makeWorkspaceZip(), offsets = centralOffsets(bytes);
  for (const offset of offsets) bytes.writeUInt16LE(20, offset + 4); // create_system = 0 (Windows)
  bytes.writeUInt32LE(0x01800000, offsets[0] + 38); // writestr: 0600, without S_IFREG
  bytes.writeUInt32LE(0x81b60020, offsets[1] + 38); // archive.write: regular 0666 + DOS archive bit
  assert.equal(validateWorkspaceBackup(bytes).valid, true);
});

test('workspace rejects empty archives and missing or wrongly named required members', () => {
  for (const bytes of [Buffer.alloc(0), makeZip([]), makeZip([{ name: 'config.json', data: json(configPayload()) }]),
    makeZip([{ name: 'folder/config.json', data: json(configPayload()) }]),
    makeZip([{ name: 'data.db', data: Buffer.from('SQLite format 3\0') }])]) failsWorkspace(bytes);
});

test('workspace validates required config redaction, including every provider', () => {
  const missing = configPayload(); delete missing.confirmation_secret;
  for (const config of [null, [], missing, configPayload({ api_key: 'fake-sensitive-key' }),
    configPayload({ auth_token: null }), configPayload({ confirmation_secret: 'generated-secret' }),
    configPayload({ providers: null }), configPayload({ providers: [{}] }),
    configPayload({ providers: [{ api_key: '' }, { api_key: 'fake-sensitive-key' }] })]) {
    failsWorkspace(makeWorkspaceZip([], { config }));
  }
  assert.equal(validateWorkspaceBackup(makeWorkspaceZip([], { config: configPayload({ providers: [{ api_key: '' }] }) })).valid, true);
  const data = Buffer.alloc(100); data.write('SQLite format 3\0');
  failsWorkspace(makeZip([{ name: 'config.json', data: '{"api_key":"hidden","api_key":"","auth_token":"","confirmation_secret":"","providers":[]}' },
    { name: 'data.db', data }]));
  failsWorkspace(makeZip([{ name: 'config.json', data: json(configPayload()) }, { name: 'data.db', data: Buffer.alloc(100) }]));
});

test('workspace rejects unsafe and noncanonical paths, aliases, and file-directory collisions', () => {
  for (const name of ['../private', 'a/../private', '/absolute', 'C:/private', 'a\\private', 'a//b', './private',
    'trailing/', 'file:stream', 'name\0hidden', 'space /file', 'dot./file', 'NUL.txt', 'dir/COM1', 'e\u0301.log']) {
    failsWorkspace(makeWorkspaceZip([{ name, data: 'synthetic' }]));
  }
  for (const names of [['config.json'], ['CONFIG.JSON'], ['a', 'a/file.log']]) {
    failsWorkspace(makeWorkspaceZip(names.map(name => ({ name, data: 'synthetic' }))));
  }
});

test('workspace excludes journal keyrings and interrupted keyring temporary files at any depth', () => {
  for (const name of ['agent-journal-key.json', '.agent-journal-key.json.lock',
    '.agent-journal-key.json.aaaaaaaa.tmp', 'nested/agent-journal-key.json', 'nested/.keyring.tmp', 'keyring']) {
    failsWorkspace(makeWorkspaceZip([{ name, data: 'synthetic' }]));
  }
});

test('workspace rejects symlinks, directories, devices, encryption, unsupported codecs, and ZIP64', () => {
  for (const entry of [{ attributes: 0xa1ff0000 }, { attributes: 0x41ed0010 }, { attributes: 0x21a40000 },
    { flags: 0x801 }, { flags: 0x840 }, { method: 12 }, { extra: Buffer.from([1, 0, 0, 0]) },
    { extra: Buffer.from([0x75, 0x70, 0, 0]) }, { extra: Buffer.from([2, 0, 8, 0]) }]) {
    failsWorkspace(makeWorkspaceZip([{ name: 'extra.bin', data: 'synthetic', ...entry }]));
  }
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, 4, 6, 45, 2));
  const zip64 = makeWorkspaceZip(); zip64.writeUInt16LE(0xffff, zip64.length - 12); failsWorkspace(zip64);
});

test('workspace verifies every CRC and both copies of metadata', () => {
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, 14, 16, 0));
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, 14, null, 0));
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, null, 16, 0));
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, 8, null, 0, 2));
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, 10, null, 1));
  const nameMismatch = makeWorkspaceZip(); nameMismatch[30] = 'x'.charCodeAt(0); failsWorkspace(nameMismatch);
  const corruptBody = makeWorkspaceZip([], { method: 0 }); corruptBody[42] ^= 1; failsWorkspace(corruptBody);
  const duplicateOffset = makeWorkspaceZip(), offsets = centralOffsets(duplicateOffset);
  duplicateOffset.writeUInt32LE(0, offsets[1] + 42); failsWorkspace(duplicateOffset);
  // A decoder can otherwise return valid content while ignoring trailing bytes
  // that the ZIP directory incorrectly advertises as part of the stream.
  failsWorkspace(makeWorkspaceZip([{ name: 'log', data: 'synthetic', compressedSuffix: Buffer.from('ignored bytes') }]));
});

test('workspace rejects truncation, trailing bytes, multipart archives, and corrupt descriptors', () => {
  const good = makeWorkspaceZip();
  for (const length of [1, 12, 22, Math.floor(good.length / 2), good.length - 1]) failsWorkspace(good.subarray(0, length));
  failsWorkspace(Buffer.concat([good, Buffer.from('trailing')]));
  failsWorkspace(Buffer.concat([Buffer.from('preamble'), good]));
  const disk = Buffer.from(good); disk.writeUInt16LE(1, disk.length - 18); failsWorkspace(disk);
  const badDescriptor = makeWorkspaceZip([{ name: 'stream.log', data: 'log', descriptor: true }]);
  const central = centralOffsets(badDescriptor)[2], local = badDescriptor.readUInt32LE(central + 42);
  const descriptor = local + 30 + badDescriptor.readUInt16LE(local + 26) + badDescriptor.readUInt32LE(central + 20);
  badDescriptor[descriptor + 4] ^= 1; failsWorkspace(badDescriptor);
});

test('workspace bounds advertised sizes and actual inflation without allocating advertised amounts', () => {
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, 22, 24, 64 * 1024 * 1024 + 1));
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, 18, 20, 0xffffffff));
  failsWorkspace(mutateHeader(makeWorkspaceZip(), 0, 22, 24, 1));
  const entries = Array.from({ length: 5 }, (_, index) => ({ name: `log${index}`, data: '' }));
  const total = makeWorkspaceZip(entries);
  for (let index = 2; index < 7; index++) mutateHeader(total, index, 22, 24, 64 * 1024 * 1024);
  failsWorkspace(total);
  const many = makeZip(Array.from({ length: 4097 }, (_, index) => ({ name: `file${index}`, data: '', method: 0 })));
  failsWorkspace(many);
});

test('workspace scans decompressed logs and binary files for ephemeral forbidden values', () => {
  const secret = 'synthetic-canary-with-no-real-credential';
  for (const method of [0, 8]) for (const data of [secret, Buffer.concat([Buffer.from([0, 255]), Buffer.from(secret), Buffer.from([128])])]) {
    failsWorkspace(makeWorkspaceZip([{ name: 'synthetic.log', data, method }]), { forbiddenValues: [secret] });
  }
  const result = validateWorkspaceBackup(makeWorkspaceZip(), { forbiddenValues: ['', secret, Buffer.from(secret)] });
  assert.equal(result.forbiddenValueCount, 1);
  failsWorkspace(makeWorkspaceZip([{ name: `${secret}.log`, data: '' }]), { forbiddenValues: [secret] });
  failsWorkspace(makeWorkspaceZip(), { forbiddenValues: [42] });
  failsWorkspace(makeWorkspaceZip(), { forbiddenValues: ['x'.repeat(4097)] });
});

test('failures never expose source bytes, filenames, parser causes, or supplied secret values', () => {
  const secret = 'do-not-retain-synthetic-secret';
  for (const operation of [() => validateSettingsBackup(Buffer.from(`{"${secret}"`)),
    () => validateWorkspaceBackup(makeWorkspaceZip([{ name: `../${secret}`, data: '' }])),
    () => validateWorkspaceBackup(makeWorkspaceZip([{ name: 'log', data: secret }]), { forbiddenValues: [secret] }),
    () => validateWorkspaceBackup(makeWorkspaceZip(), { get forbiddenValues() { throw new Error(secret); } })]) {
    try { operation(); assert.fail('expected validation failure'); }
    catch (error) {
      assert.match(error.message, /backup validation failed$/);
      assert.equal(error.cause, undefined);
      assert.ok(!error.message.includes(secret)); assert.ok(!JSON.stringify(error).includes(secret));
    }
  }
});
