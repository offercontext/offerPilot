import { inflateRawSync } from 'node:zlib';

// Deliberately a validator for the pinned product's synthetic-profile exports,
// not an extractor or a general-purpose ZIP reader. Never return source values.
const MAX_JSON_BYTES = 1024 * 1024;
const MAX_ARCHIVE_BYTES = 128 * 1024 * 1024;
const MAX_ENTRY_BYTES = 64 * 1024 * 1024;
const MAX_TOTAL_BYTES = 256 * 1024 * 1024;
const MAX_ENTRIES = 4096;
const utf8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
const SETTINGS_KEYS = ['version', 'exported_at', 'runtime_mode', 'auth_enabled', 'has_auth_token',
  'log_level', 'chat_auto_approve_writes', 'active_provider_id', 'fallback_provider_ids', 'providers'];
const PROVIDER_KEYS = ['id', 'label', 'provider', 'base_url', 'model', 'enabled',
  'supports_json_schema', 'context_window', 'max_output_tokens', 'has_api_key'];

function requireValid(value) { if (!value) throw new Error('Invalid backup structure'); }
function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}
function exactKeys(value, expected) {
  requireValid(isObject(value));
  const actual = Object.keys(value);
  requireValid(actual.length === expected.length && expected.every(key => Object.hasOwn(value, key)));
}

// JSON.parse alone silently accepts duplicate members. A hidden earlier member
// could retain credentials even when the final parsed value looks redacted.
function rejectDuplicateMembers(source) {
  let offset = 0;
  const whitespace = () => { while (/\s/.test(source[offset] ?? '') && offset < source.length) offset++; };
  function string() {
    const start = offset++;
    while (source[offset] !== '"') { if (source[offset] === '\\') offset++; offset++; }
    offset++;
    return source.slice(start, offset);
  }
  function value(depth) {
    requireValid(depth <= 64);
    whitespace();
    if (source[offset] === '{') {
      offset++; whitespace();
      const names = new Set();
      if (source[offset] !== '}') while (true) {
        whitespace();
        const key = JSON.parse(string());
        requireValid(!names.has(key)); names.add(key);
        whitespace(); offset++; value(depth + 1); whitespace();
        if (source[offset] !== ',') break;
        offset++;
      }
      offset++;
    } else if (source[offset] === '[') {
      offset++; whitespace();
      if (source[offset] !== ']') while (true) {
        value(depth + 1); whitespace();
        if (source[offset] !== ',') break;
        offset++;
      }
      offset++;
    } else if (source[offset] === '"') string();
    else while (offset < source.length && !/[\s,}\]]/.test(source[offset])) offset++;
  }
  value(0);
}
function readJson(bytes) {
  requireValid(Buffer.isBuffer(bytes) && bytes.length > 0 && bytes.length <= MAX_JSON_BYTES);
  const source = utf8.decode(bytes);
  const parsed = JSON.parse(source);
  rejectDuplicateMembers(source);
  return parsed;
}

export function validateSettingsBackup(bytes) {
  try {
    const payload = readJson(bytes);
    exactKeys(payload, SETTINGS_KEYS);
    requireValid(payload.version === 1);
    requireValid(typeof payload.exported_at === 'string'
      && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(payload.exported_at)
      && Number.isFinite(Date.parse(payload.exported_at)));
    requireValid(['local', 'server'].includes(payload.runtime_mode));
    requireValid(['DEBUG', 'INFO', 'WARNING', 'ERROR'].includes(payload.log_level));
    requireValid(typeof payload.auth_enabled === 'boolean' && payload.has_auth_token === false
      && typeof payload.chat_auto_approve_writes === 'boolean');
    requireValid(Array.isArray(payload.providers) && payload.providers.length > 0
      && payload.providers.length <= MAX_ENTRIES);
    const ids = new Set();
    for (const provider of payload.providers) {
      exactKeys(provider, PROVIDER_KEYS);
      for (const field of ['id', 'label', 'provider', 'base_url', 'model']) requireValid(typeof provider[field] === 'string');
      requireValid(provider.id.length > 0 && !ids.has(provider.id)); ids.add(provider.id);
      requireValid(typeof provider.enabled === 'boolean' && typeof provider.supports_json_schema === 'boolean'
        && provider.has_api_key === false);
      for (const field of ['context_window', 'max_output_tokens']) {
        requireValid(Number.isSafeInteger(provider[field]) && provider[field] >= 0);
      }
    }
    requireValid(typeof payload.active_provider_id === 'string' && ids.has(payload.active_provider_id));
    requireValid(Array.isArray(payload.fallback_provider_ids));
    const fallbacks = new Set();
    for (const id of payload.fallback_provider_ids) {
      requireValid(typeof id === 'string' && ids.has(id) && id !== payload.active_provider_id && !fallbacks.has(id));
      fallbacks.add(id);
    }
    return { valid: true, schemaVersion: 1, providerCount: ids.size,
      fallbackProviderCount: fallbacks.size, credentialValuesAbsent: true };
  } catch {
    // Do not retain parser errors, causes, names, content, or credential values.
    throw new Error('Settings backup validation failed');
  }
}

const crcTable = Uint32Array.from({ length: 256 }, (_, index) => {
  let crc = index;
  for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  return crc >>> 0;
});
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 0xff];
  return (crc ^ 0xffffffff) >>> 0;
}
function extraFields(bytes) {
  let offset = 0;
  while (offset < bytes.length) {
    requireValid(offset + 4 <= bytes.length);
    const type = bytes.readUInt16LE(offset), length = bytes.readUInt16LE(offset + 2);
    // No ZIP64, encryption, or alternate Unicode path/comment interpretations.
    requireValid(![0x0001, 0x0017, 0x7075, 0x6375, 0x9901].includes(type));
    offset += 4 + length;
    requireValid(offset <= bytes.length);
  }
}
function safeName(bytes, flags) {
  requireValid(bytes.length > 0 && bytes.length <= 4096);
  if (!(flags & 0x800)) requireValid(bytes.every(byte => byte < 128));
  const name = utf8.decode(bytes);
  requireValid(name === name.normalize('NFC') && !/[\\<>:"|?*\x00-\x1f\x7f]/.test(name));
  const components = name.split('/');
  for (const component of components) {
    requireValid(component !== '' && component !== '.' && component !== '..' && !/[. ]$/.test(component));
    requireValid(!/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(component));
    requireValid(!/agent-journal-key\.json|keyring/i.test(component));
  }
  return name;
}
function forbiddenBuffers(values) {
  requireValid(Array.isArray(values) && values.length <= 128);
  const result = [];
  for (const value of values) {
    requireValid(typeof value === 'string' || Buffer.isBuffer(value));
    requireValid(value.length <= 4096);
    const bytes = Buffer.isBuffer(value) ? value : Buffer.from(value, 'utf8');
    requireValid(bytes.length <= 4096);
    if (bytes.length > 0 && !result.some(existing => existing.equals(bytes))) result.push(bytes);
  }
  return result;
}
function absentSecrets(bytes, forbidden) { requireValid(forbidden.every(value => !bytes.includes(value))); }

function archiveEntries(bytes) {
  requireValid(Buffer.isBuffer(bytes) && bytes.length >= 22 && bytes.length <= MAX_ARCHIVE_BYTES);
  let end = -1;
  for (let offset = bytes.length - 22; offset >= Math.max(0, bytes.length - 22 - 65535); offset--) {
    if (bytes.readUInt32LE(offset) === 0x06054b50 && offset + 22 + bytes.readUInt16LE(offset + 20) === bytes.length) {
      end = offset; break;
    }
  }
  requireValid(end >= 0 && bytes.readUInt16LE(end + 4) === 0 && bytes.readUInt16LE(end + 6) === 0);
  const count = bytes.readUInt16LE(end + 10);
  const centralSize = bytes.readUInt32LE(end + 12), centralStart = bytes.readUInt32LE(end + 16);
  requireValid(count > 0 && count <= MAX_ENTRIES && count === bytes.readUInt16LE(end + 8)
    && centralSize !== 0xffffffff && centralStart !== 0xffffffff && centralStart + centralSize === end);
  let offset = centralStart, totalBytes = 0;
  const entries = [], names = new Set();
  for (let index = 0; index < count; index++) {
    requireValid(offset + 46 <= end && bytes.readUInt32LE(offset) === 0x02014b50);
    const needed = bytes.readUInt16LE(offset + 6), flags = bytes.readUInt16LE(offset + 8);
    const method = bytes.readUInt16LE(offset + 10), crc = bytes.readUInt32LE(offset + 16);
    const compressedSize = bytes.readUInt32LE(offset + 20), size = bytes.readUInt32LE(offset + 24);
    const nameLength = bytes.readUInt16LE(offset + 28), extraLength = bytes.readUInt16LE(offset + 30);
    const commentLength = bytes.readUInt16LE(offset + 32), attributes = bytes.readUInt32LE(offset + 38);
    const local = bytes.readUInt32LE(offset + 42), mode = (attributes >>> 16) & 0xf000;
    const next = offset + 46 + nameLength + extraLength + commentLength;
    requireValid(next <= end && needed <= 20 && (flags & ~0x080e) === 0 && [0, 8].includes(method)
      && (method !== 0 || (flags & 6) === 0) && bytes.readUInt16LE(offset + 34) === 0
      && (mode === 0 || mode === 0x8000) && !(attributes & 0x10)
      && compressedSize <= MAX_ARCHIVE_BYTES && size <= MAX_ENTRY_BYTES && local < centralStart);
    const rawName = bytes.subarray(offset + 46, offset + 46 + nameLength);
    const name = safeName(rawName, flags), canonical = name.toLowerCase();
    requireValid(!names.has(canonical)); names.add(canonical);
    extraFields(bytes.subarray(offset + 46 + nameLength, offset + 46 + nameLength + extraLength));
    totalBytes += size; requireValid(totalBytes <= MAX_TOTAL_BYTES);
    entries.push({ name, rawName, needed, flags, method, crc, compressedSize, size, local,
      time: bytes.readUInt32LE(offset + 12) });
    offset = next;
  }
  requireValid(offset === end);
  for (const name of names) {
    const components = name.split('/');
    for (let index = 1; index < components.length; index++) requireValid(!names.has(components.slice(0, index).join('/')));
  }
  entries.sort((left, right) => left.local - right.local);
  let cursor = 0;
  for (const entry of entries) {
    const { local, needed, flags, method, crc, compressedSize, size, rawName, time } = entry;
    requireValid(local === cursor && local + 30 <= centralStart && bytes.readUInt32LE(local) === 0x04034b50
      && bytes.readUInt16LE(local + 4) === needed && bytes.readUInt16LE(local + 6) === flags
      && bytes.readUInt16LE(local + 8) === method && bytes.readUInt32LE(local + 10) === time);
    const nameLength = bytes.readUInt16LE(local + 26), extraLength = bytes.readUInt16LE(local + 28);
    const start = local + 30 + nameLength + extraLength;
    requireValid(start + compressedSize <= centralStart
      && bytes.subarray(local + 30, local + 30 + nameLength).equals(rawName));
    extraFields(bytes.subarray(local + 30 + nameLength, start));
    for (const [position, expected] of [[14, crc], [18, compressedSize], [22, size]]) {
      const actual = bytes.readUInt32LE(local + position);
      requireValid(actual === expected || ((flags & 8) && actual === 0));
    }
    cursor = start + compressedSize;
    if (flags & 8) {
      const matches = at => at + 12 <= centralStart && bytes.readUInt32LE(at) === crc
        && bytes.readUInt32LE(at + 4) === compressedSize && bytes.readUInt32LE(at + 8) === size;
      if (cursor + 16 <= centralStart && bytes.readUInt32LE(cursor) === 0x08074b50 && matches(cursor + 4)) cursor += 16;
      else { requireValid(matches(cursor)); cursor += 12; }
    }
    entry.start = start;
  }
  requireValid(cursor === centralStart);
  return { entries, totalBytes };
}

export function validateWorkspaceBackup(bytes, options = {}) {
  try {
    requireValid(isObject(options));
    const { forbiddenValues = [] } = options;
    const forbidden = forbiddenBuffers(forbiddenValues);
    const { entries, totalBytes } = archiveEntries(bytes);
    // Cover plaintext ZIP metadata as well as each uncompressed binary/text entry.
    absentSecrets(bytes, forbidden);
    let configPresent = false, sqlitePresent = false;
    for (const entry of entries) {
      const compressed = bytes.subarray(entry.start, entry.start + entry.compressedSize);
      let contents;
      if (entry.method === 0) { requireValid(entry.size === entry.compressedSize); contents = compressed; }
      else {
        const inflated = inflateRawSync(compressed, { maxOutputLength: Math.max(1, entry.size), info: true });
        requireValid(inflated.engine.bytesWritten === compressed.length);
        contents = inflated.buffer;
      }
      requireValid(contents.length === entry.size && crc32(contents) === entry.crc);
      absentSecrets(contents, forbidden);
      if (entry.name === 'config.json') {
        const config = readJson(contents);
        requireValid(isObject(config));
        for (const key of ['api_key', 'auth_token', 'confirmation_secret']) requireValid(Object.hasOwn(config, key) && config[key] === '');
        requireValid(Array.isArray(config.providers));
        for (const provider of config.providers) requireValid(isObject(provider) && Object.hasOwn(provider, 'api_key') && provider.api_key === '');
        configPresent = true;
      }
      if (entry.name === 'data.db') {
        requireValid(contents.length >= 100 && contents.subarray(0, 16).equals(Buffer.from('SQLite format 3\0')));
        sqlitePresent = true;
      }
    }
    requireValid(configPresent && sqlitePresent);
    return { valid: true, entryCount: entries.length, uncompressedBytes: totalBytes,
      configPresent, sqlitePresent, credentialsRedacted: true, keyringAbsent: true,
      forbiddenValueCount: forbidden.length, forbiddenValuesAbsent: true };
  } catch {
    throw new Error('Workspace backup validation failed');
  }
}
