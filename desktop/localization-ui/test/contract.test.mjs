import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { buttonName, classifyRequest, safeRequest, samePath, ownedProcesses, earlyOwnedProcesses, validateSockets, treeDigest, assertNativeMenu, TEXT } from '../contract.mjs';

const origin = 'http://127.0.0.1:43123';
const here = path.dirname(fileURLToPath(import.meta.url));
test('fresh disabled companion uses exact Pilot renderer copy without renaming the native tray', () => {
  assert.deepEqual(['haru', 'expand', 'pin', 'close'].map(key => TEXT['zh-CN'][key]),
    ['Pilot 桌面小窗', '展开 Pilot 对话', 'Pilot 始终置顶', '关闭 Pilot 对话']);
  assert.deepEqual(['haru', 'expand', 'pin', 'close'].map(key => TEXT['en-US'][key]),
    ['Pilot Desktop Companion', 'Expand Pilot Chat', 'Keep Pilot on Top', 'Close Pilot chat']);
  assert.equal(TEXT['zh-CN'].tray[1], '隐藏 Haru');
  assert.equal(TEXT['en-US'].tray[1], 'Hide Haru');
});
test('visible AntD two-Han-character button typography is matched without broadening the action', () => {
  assert.match('取消', buttonName('取消'));
  assert.match('取 消', buttonName('取消'));
  assert.doesNotMatch('取消并清空输入', buttonName('取消'));
  assert.match('Cancel', buttonName('Cancel'));
  assert.doesNotMatch('Cancel and submit', buttonName('Cancel'));
  assert.match('Cancel (safe)', buttonName('Cancel (safe)'));
});
test('only same-origin GET/HEAD reach the installed backend', () => {
  for (const method of ['GET', 'HEAD']) assert.equal(classifyRequest(`${origin}/api/job-mail/status`, method, origin).allowed, true);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'get']) {
    assert.deepEqual(classifyRequest(`${origin}/api/job-mail/imports`, method, origin), { allowed: false, reason: 'backend-mutation' });
  }
  for (const url of ['https://api.example.invalid/v1', 'http://localhost:43123/api', 'http://127.0.0.1:43124/api',
    'http://127.0.0.1.evil.invalid:43123/api', 'ws://127.0.0.1:43123/api', 'file:///C:/credentials']) {
    assert.equal(classifyRequest(url, 'GET', origin).allowed, false, url);
  }
  assert.equal(classifyRequest('http://user:pass@127.0.0.1:43123/', 'GET', origin).allowed, false);
  assert.equal(classifyRequest('not a URL', 'GET', origin).allowed, false);
  assert.equal(classifyRequest('data:image/png;base64,abc', 'GET', origin).allowed, true);
  assert.equal(classifyRequest('blob:http://127.0.0.1:43123/id', 'GET', origin).allowed, true);
});
test('failure request diagnostics drop query, fragments, credentials and inline content', () => {
  const reason = { reason: 'outbound-or-unexpected-origin' };
  const safe = safeRequest('https://user:secret@example.invalid/api?token=private#hidden', 'GET', reason);
  assert.deepEqual(safe, { method: 'GET', target: 'https://example.invalid/api', reason: reason.reason });
  assert.equal(safeRequest('data:text/plain,private', 'GET', reason).target, 'data:');
  assert.equal(safeRequest('invalid secret', 'GET', reason).target, '<invalid-url>');
});
test('cleanup ownership requires independently observed exact installed paths and parent PID', () => {
  const exe = 'C:\\runner\\安装 Application\\OfferPilot Desktop.exe';
  const backend = 'C:\\runner\\安装 Application\\resources\\backend\\offerpilot-backend.exe';
  const main = { pid: 10, parentPid: 4, path: exe, created: '2026-10-15T00:00:00Z' };
  const child = { pid: 11, parentPid: 10, path: backend, created: '2026-10-15T00:00:01Z' };
  assert.equal(samePath(exe, 'c:/RUNNER/安装 Application/OfferPilot Desktop.exe'), true);
  assert.deepEqual(ownedProcesses([main, child, { ...child, pid: 15, parentPid: 7 }], 10, exe, backend), [main, child]);
  assert.throws(() => ownedProcesses([{ ...main, created: undefined }], 10, exe, backend));
  assert.throws(() => ownedProcesses([{ ...main, path: 'C:\\other\\OfferPilot Desktop.exe' }], 10, exe, backend));
  assert.throws(() => ownedProcesses([main, child, { ...child, pid: 12 }], 10, exe, backend));
});
test('runtime socket check rejects nonloopback exposure and product outbound TCP', () => {
  const snapshot = { listeners: [{ pid: 11, address: '127.0.0.1', port: 43123 }, { pid: 10, address: '127.0.0.1', port: 49152 }], outbound: [] };
  validateSockets(snapshot, 11, 43123);
  assert.throws(() => validateSockets({ ...snapshot, outbound: [{ pid: 11, address: '192.0.2.1', port: 443 }] }, 11, 43123));
  assert.throws(() => validateSockets({ ...snapshot, listeners: [{ pid: 11, address: '0.0.0.0', port: 43123 }] }, 11, 43123));
  assert.throws(() => validateSockets(snapshot, 11, 43124));
});
test('early Windows cleanup distinguishes the live cmd wrapper from its installed EXE child', () => {
  const exe = 'C:\\temp\\OfferPilot Desktop.exe';
  const backend = 'C:\\temp\\resources\\backend\\offerpilot-backend.exe';
  const main = { pid: 101, parentPid: 100, path: exe, created: '2026-10-15T08:00:01Z' };
  const child = { pid: 102, parentPid: 101, path: backend, created: '2026-10-15T08:00:02Z' };
  const launch = { handlePid: 100, handleRunning: true, startedAt: Date.parse('2026-10-15T08:00:00Z'), observedAt: Date.parse('2026-10-15T08:00:03Z') };
  assert.deepEqual(earlyOwnedProcesses([main, child], launch, exe, backend), [main, child]);
  assert.deepEqual(earlyOwnedProcesses([main, child], { ...launch, handlePid: 101 }, exe, backend), [main, child]);
  assert.throws(() => earlyOwnedProcesses([main, child], { ...launch, handleRunning: false }, exe, backend));
  assert.throws(() => earlyOwnedProcesses([{ ...main, parentPid: 99 }, child], launch, exe, backend));
  assert.throws(() => earlyOwnedProcesses([{ ...main, created: '2026-10-15T07:59:59Z' }, child], launch, exe, backend));
  assert.throws(() => earlyOwnedProcesses([{ ...main, created: '2026-10-15T08:00:04Z' }, child], launch, exe, backend));
  assert.throws(() => earlyOwnedProcesses([main, { ...main, pid: 103 }, child], launch, exe, backend));
  assert.throws(() => earlyOwnedProcesses([{ ...main, path: 'C:\\Windows\\System32\\cmd.exe' }], launch, exe, backend));
});
test('payload digest binds file names and bytes and changes on added or edited files', async t => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-localization-hash-'));
  t.after(() => fs.rm(folder, { recursive: true, force: true }));
  await fs.mkdir(path.join(folder, 'nested'));
  await fs.writeFile(path.join(folder, 'nested/a'), 'original');
  const before = await treeDigest(folder);
  assert.deepEqual(await treeDigest(folder), before);
  await fs.writeFile(path.join(folder, 'nested/a'), 'changed');
  assert.notEqual((await treeDigest(folder)).sha256, before.sha256);
  await fs.writeFile(path.join(folder, 'nested/a'), 'original');
  await fs.writeFile(path.join(folder, 'extra'), 'original');
  assert.notEqual((await treeDigest(folder)).sha256, before.sha256);
});
test('native locale assertion requires exact localized menu and enabled explicit exit', () => {
  const menu = locale => TEXT[locale].menu.map((label, i) => ({ label, children: i === 0 ? [{ label: TEXT[locale].quit, enabled: true }] : [] }));
  for (const locale of ['zh-CN', 'en-US']) assertNativeMenu(menu(locale), locale);
  assert.throws(() => assertNativeMenu(menu('zh-CN'), 'en-US'));
  const disabled = menu('en-US'); disabled[0].children[0].enabled = false;
  assert.throws(() => assertNativeMenu(disabled, 'en-US'));
});
test('new harness uses installed executable, explicit quit, and no product or old helper rewrites', async () => {
  const code = await fs.readFile(path.join(here, '../smoke.mjs'), 'utf8');
  const windows = await fs.readFile(path.join(here, '../windows.ps1'), 'utf8');
  assert.match(code, /executablePath: paths\.exe/);
  assert.match(code, /chromiumSandbox: true, bypassCSP: false/);
  assert.match(code, /app\.getPath\('userData'\)/);
  assert.match(code, /getByRole\('combobox'/);
  assert.match(code, /route\.abort\('blockedbyclient'\)/);
  assert.ok(code.indexOf("await route.abort('blockedbyclient')") < code.indexOf('launch.presence.recordAborted('));
  assert.ok(code.indexOf('captured = capturePresenceRequest(request)') < code.indexOf('return launch.requestQueue.schedule('));
  assert.ok(code.indexOf('return launch.requestQueue.schedule(') < code.indexOf('origin = `http://127.0.0.1:${await port()}`'));
  assert.match(code, /ownerMainFrame: active\.page\.mainFrame\(\)/);
  assert.match(code, /snapshot: \{ identity: snapshot\.identity, enabled: snapshot\.settings\?\.enabled \}/);
  assert.ok(code.indexOf("await launchPhase('verify-disabled-companion-presence')") < code.indexOf("await launchPhase('runtime-security-read')"));
  assert.match(code, /await current\.requestQueue\.drain\(\);\s+ensureSafe\(\)/);
  assert.match(code, /original\.call\(this, menu\)/);
  assert.match(code, /await launch\(1, 'zh-CN'\)/);
  assert.match(code, /await launch\(2, 'en-US'\)/);
  assert.match(code, /await launch\(3, 'zh-CN'\)/);
  assert.doesNotMatch(code, /(?:route\.fulfill|addInitScript|\.setLocale\(|--user-data-dir|installed-ui\/|request\.json|PIN\.|active\.child\.kill)/);
  assert.doesNotMatch(code, /(?:writeFile|appendFile)\(path\.join\(paths\.(?:install|userData)/);
  assert.doesNotMatch(windows.replace(/^\s*#.*$/gm, ''), /Remove-Item|taskkill|Stop-Process\s+-Name|Set-NetFirewall|Set-ExecutionPolicy/);
  assert.match(windows, /\$start\.Arguments = '\/S \/currentuser \/D='/);
  for (const field of ['pid', 'parentPid', 'path', 'created']) assert.ok(windows.includes(`$identity.${field}`));
});
test('non-CI invocation fails before installer or browser launch and writes explicit incomplete results', async t => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-localization-rejection-'));
  t.after(() => fs.rm(folder, { recursive: true, force: true }));
  const child = spawnSync(process.execPath, [path.join(here, '../smoke.mjs')], {
    env: { GITHUB_ACTIONS: 'false', LOCALIZATION_EVIDENCE_DIR: folder },
    encoding: 'utf8', timeout: 10000,
  });
  assert.equal(child.status, 1);
  const result = JSON.parse(await fs.readFile(path.join(folder, 'result.json'), 'utf8'));
  assert.equal(result.status, 'failed');
  assert.equal(result.failure.stage, 'environment');
  assert.deepEqual(result.launches, []);
  assert.deepEqual(result.screenshots, []);
  assert.ok(result.requiredStageResults.every(item => item.status === 'not-run-or-incomplete'));
});
