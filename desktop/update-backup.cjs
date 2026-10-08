'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const execute = promisify(execFile);
async function restrictWindowsBackup(destination) {
  if (process.platform !== 'win32') return;
  const systemRoot = process.env.SystemRoot || 'C:\\Windows';
  const encoded = Buffer.from(destination, 'utf8').toString('base64');
  const command = `$ErrorActionPreference='Stop'; $p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encoded}')); $sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; $acl=[System.Security.AccessControl.DirectorySecurity]::new(); $acl.SetAccessRuleProtection($true,$false); $acl.SetOwner($sid); $rule=[System.Security.AccessControl.FileSystemAccessRule]::new($sid,[System.Security.AccessControl.FileSystemRights]::FullControl,[System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',[System.Security.AccessControl.PropagationFlags]::None,[System.Security.AccessControl.AccessControlType]::Allow); $acl.AddAccessRule($rule); Microsoft.PowerShell.Security\\Set-Acl -LiteralPath $p -AclObject $acl; $actual=Microsoft.PowerShell.Security\\Get-Acl -LiteralPath $p; if(-not $actual.AreAccessRulesProtected){throw 'Backup ACL not protected'}; foreach($r in $actual.Access){if($r.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){throw 'Unexpected backup ACL'}}`;
  await execute(path.win32.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
    ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(command, 'utf16le').toString('base64')],
    {windowsHide: true, timeout: 15000, maxBuffer: 16384, env: { ...process.env, PSModulePath: path.win32.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'Modules') }});
}
async function copyTree(source, destination) {
  const stat = await fs.lstat(source);
  if (stat.isSymbolicLink()) throw new Error('Backup refuses links');
  if (stat.isDirectory()) {
    await fs.mkdir(destination, { mode: 0o700 });
    for (const name of await fs.readdir(source)) await copyTree(path.join(source, name), path.join(destination, name));
  } else if (stat.isFile()) {
    await fs.copyFile(source, destination, fs.constants.COPYFILE_EXCL);
    // Never make a sensitive backup more permissive than the source on POSIX.
    if (process.platform !== 'win32') await fs.chmod(destination, stat.mode & 0o600);
    const copied = await fs.stat(destination);
    if (copied.size !== stat.size) throw new Error('Incomplete backup');
  } else throw new Error('Unsupported backup file');
}
async function backupForUpdate({ userData, version, backendExited, restrictDestination = restrictWindowsBackup }) {
  if (backendExited !== true) throw new Error('Backend is not confirmed stopped');
  const root = path.resolve(userData);
  if (!(await fs.lstat(root)).isDirectory() || (await fs.lstat(root)).isSymbolicLink()) throw new Error('Unsafe data directory');
  const backupRoot = `${root}-update-backups`;
  await fs.mkdir(backupRoot, { recursive: true, mode: 0o700 });
  const rootStat = await fs.lstat(backupRoot);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error('Unsafe backup directory');
  const destination = path.join(backupRoot, `${new Date().toISOString().replace(/[:.]/g, '-')}-${randomUUID()}`);
  await fs.mkdir(destination, { mode: 0o700 });
  // The directory is still empty. On Windows remove inherited grants and allow
  // only this Windows user before copying any sensitive configuration.
  await restrictDestination(destination);
  // Full backend workspace includes SQLite sidecars and sensitive config.
  // Electron's live LevelDB is intentionally not copied as an alleged snapshot.
  // Updates retain the original userData/partition and never migrate its path.
  await copyTree(path.join(root, 'data'), path.join(destination, 'data'));
  for (const filename of ['desktop-port.json', 'haru-window.json']) {
    try { await copyTree(path.join(root, filename), path.join(destination, filename)); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  await fs.writeFile(path.join(destination, 'complete.json'), JSON.stringify({ version, createdAt: new Date().toISOString(), scope: 'backend-workspace-and-desktop-preferences', browserStorage: 'retained-in-place-not-snapshotted' }), { flag: 'wx', mode: 0o600 });
  return destination;
}
module.exports = { backupForUpdate };
