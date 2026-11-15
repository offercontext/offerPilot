'use strict';
const fs = require('node:fs');
const { createHash } = require('node:crypto');
const path = require('node:path');
async function verifyDownloadedUpdate({ paths, info, verifier, publisher }) {
  if (!Array.isArray(paths) || paths.length !== 1 || !path.isAbsolute(paths[0]) || !/\.exe$/i.test(paths[0])) throw new Error('Unexpected update payload');
  const files = info?.files?.filter(file => /\.exe(?:\?|$)/i.test(String(file.url)));
  if (files?.length !== 1 || typeof files[0].sha512 !== 'string' || !/^[A-Za-z0-9+/]{86}==$/.test(files[0].sha512)) throw new Error('Missing update checksum');
  const filename = paths[0];
  const stat = await fs.promises.lstat(filename);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('Unsafe update file');
  if (files[0].size != null && stat.size !== files[0].size) throw new Error('Update size mismatch');
  const hash = createHash('sha512');
  for await (const chunk of fs.createReadStream(filename)) hash.update(chunk);
  if (hash.digest('base64') !== files[0].sha512) throw new Error('Update checksum mismatch');
  if (await verifier([publisher], filename) !== null) throw new Error('Update signature verification failed');
}
module.exports = { verifyDownloadedUpdate };
