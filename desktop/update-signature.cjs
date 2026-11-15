'use strict';
const { execFile } = require('node:child_process');
const path = require('node:path');
// Strengthen the pinned updater's legacy verifier: its PowerShell fallback can
// otherwise treat an unavailable verification tool as success. No fallback is
// permitted here. The approved publisher must be the complete certificate DN.
function strictSignatureVerifier(expectedSubject, execute = execFile, systemRoot = process.env.SystemRoot || 'C:\\Windows') {
  return (publishers, filename) => new Promise(resolve => {
    if (!Array.isArray(publishers) || publishers.length !== 1 || publishers[0] !== expectedSubject
      || typeof expectedSubject !== 'string' || !expectedSubject.startsWith('CN=')
      || typeof filename !== 'string' || /[\x00-\x1f]/.test(filename) || !path.win32.isAbsolute(filename)) {
      resolve('Invalid signature verification input'); return;
    }
    const literal = Buffer.from(filename, 'utf8').toString('base64');
    const command = `$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $p=[System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${literal}')); $s=Microsoft.PowerShell.Security\\Get-AuthenticodeSignature -LiteralPath $p; [pscustomobject]@{Status=$s.Status.ToString();Subject=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress`;
    const powershell = path.win32.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
    execute(powershell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(command, 'utf16le').toString('base64')], {windowsHide: true, timeout: 30000, maxBuffer: 16384, env: { ...process.env, PSModulePath: path.win32.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'Modules') }}, (error, stdout) => {
      if (error) { resolve('Windows signature verification could not complete'); return; }
      try {
        const signature = JSON.parse(String(stdout).replace(/^\uFEFF/, '').trim());
        resolve(signature.Status === 'Valid' && signature.Subject === expectedSubject ? null : 'Installer signature or publisher does not match');
      } catch { resolve('Invalid Windows signature verification response'); }
    });
  });
}
module.exports = { strictSignatureVerifier };
