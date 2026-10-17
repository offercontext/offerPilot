import { deflateRawSync } from 'node:zlib';

export function settingsPayload(overrides = {}) {
  return {
    version: 1, exported_at: '2026-10-08T09:30:00.123456+00:00', runtime_mode: 'local',
    auth_enabled: false, has_auth_token: false, log_level: 'INFO', chat_auto_approve_writes: false,
    active_provider_id: 'default', fallback_provider_ids: [], providers: [{
      id: 'default', label: 'Default', provider: 'openai', base_url: 'https://api.openai.com/v1',
      model: 'gpt-4o', enabled: true, supports_json_schema: false,
      context_window: 0, max_output_tokens: 0, has_api_key: false,
    }], ...overrides,
  };
}

export function configPayload(overrides = {}) {
  return {
    api_key: '', base_url: 'https://api.openai.com/v1', model: 'gpt-4o', local_port: 8080,
    chat_auto_approve_writes: false, onboarding_force_open: false, active_provider_id: 'default',
    fallback_provider_id: '', multimodal_provider_id: '', providers: [], fallback_provider_ids: [],
    runtime_mode: 'local', auth_enabled: false, auth_token: '', log_level: 'INFO', skills: [],
    confirmation_secret: '', ...overrides,
  };
}

// Independent bitwise fixture CRC; do not reuse the validator's implementation.
function fixtureCrc(bytes) {
  let value = 0xffffffff;
  for (const byte of bytes) {
    value ^= byte;
    for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
  }
  return (value ^ 0xffffffff) >>> 0;
}

export function makeZip(entries, { comment = '' } = {}) {
  const locals = [], central = [];
  let localOffset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name, 'utf8');
    const contents = Buffer.isBuffer(entry.data) ? entry.data : Buffer.from(entry.data ?? '');
    const method = entry.method ?? 8, descriptor = entry.descriptor ?? false;
    const compressed = Buffer.concat([method === 0 ? contents : deflateRawSync(contents), entry.compressedSuffix ?? Buffer.alloc(0)]);
    const flags = entry.flags ?? (0x800 | (descriptor ? 8 : 0));
    const crc = fixtureCrc(contents), extra = entry.extra ?? Buffer.alloc(0);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0); header.writeUInt16LE(20, 4);
    header.writeUInt16LE(flags, 6); header.writeUInt16LE(method, 8);
    if (!descriptor) {
      header.writeUInt32LE(crc, 14); header.writeUInt32LE(compressed.length, 18);
      header.writeUInt32LE(contents.length, 22);
    }
    header.writeUInt16LE(name.length, 26); header.writeUInt16LE(extra.length, 28);
    const suffix = Buffer.alloc(descriptor ? (entry.unsignedDescriptor ? 12 : 16) : 0);
    if (descriptor) {
      const start = entry.unsignedDescriptor ? 0 : 4;
      if (start) suffix.writeUInt32LE(0x08074b50, 0);
      suffix.writeUInt32LE(crc, start); suffix.writeUInt32LE(compressed.length, start + 4);
      suffix.writeUInt32LE(contents.length, start + 8);
    }
    const local = Buffer.concat([header, name, extra, compressed, suffix]);
    const directory = Buffer.alloc(46);
    directory.writeUInt32LE(0x02014b50, 0); directory.writeUInt16LE(0x314, 4);
    directory.writeUInt16LE(20, 6); directory.writeUInt16LE(flags, 8);
    directory.writeUInt16LE(method, 10); directory.writeUInt32LE(crc, 16);
    directory.writeUInt32LE(compressed.length, 20); directory.writeUInt32LE(contents.length, 24);
    directory.writeUInt16LE(name.length, 28); directory.writeUInt16LE(extra.length, 30);
    directory.writeUInt32LE(entry.attributes ?? 0x81a40000, 38);
    directory.writeUInt32LE(localOffset, 42);
    central.push(directory, name, extra); locals.push(local); localOffset += local.length;
  }
  const directory = Buffer.concat(central), commentBytes = Buffer.from(comment);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10); end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(localOffset, 16); end.writeUInt16LE(commentBytes.length, 20);
  return Buffer.concat([...locals, directory, end, commentBytes]);
}

export function makeWorkspaceZip(extraEntries = [], { config = configPayload(), method = 8 } = {}) {
  const database = Buffer.alloc(100); database.write('SQLite format 3\0');
  return makeZip([
    { name: 'config.json', data: JSON.stringify(config), method },
    { name: 'data.db', data: database, method },
    ...extraEntries,
  ]);
}
