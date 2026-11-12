// Test harness only: launch a pinned, unmodified installed Windows package.
// UIAutomation operates the real native dialog; no dialog/download overrides.
import assert from 'node:assert/strict';
import { button } from '../installed-ui/ui-locators.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, '../installed-ui/package.json'));
const { _electron } = require('playwright-core');
const { extractFile } = require('@electron/asar');
const artifact = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
const pin = {
  source: 'ea50127de95da42fdb6f89e5f0f744e76ff12203',
  build: '0f4f580103620f7ae4668e7bd589c3237ec90b2a',
  run: 43070936796, artifact: 13882405863,
  installerSha256: '4a83aa184f03ef35b65cd44a09645c7a46f13c51efa400127be55e8d0dea7a46',
};
const report = { status: 'running', pin, harness: process.env.GITHUB_SHA, checks: [], downloads: [],
  scope: 'Pinned NSIS-installed Electron, real editor and PDF canvas, Windows UIAutomation identity + verified native HWND save/cancel. Synthetic fresh profile.',
  limitations: ['Unsigned validation package, not a release.', 'Playwright debugging launch; ordinary undebugged launch, UAC and SmartScreen untested.', 'UIAutomation identity and Win32 HWND control messages, not pointer-based native interaction.', 'Chinese single-page resume only; bilingual/multipage coverage belongs to the separate browser run.'],
  dialogMocked: false, downloadBehaviorOverridden: false, productionModified: false };
let stage = 'environment', app, page, installed = false, exe, installDir;
const hash = value => createHash('sha256').update(value).digest('hex');
const exists = async filename => fs.access(filename).then(() => true, () => false);
const check = (name, condition) => { assert.ok(condition, name); report.checks.push(name); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(name, condition, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await condition()) return; await sleep(100); }
  throw new Error(`Timed out: ${name}`);
}
function command(executable, args, timeout = 60000) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Owned helper timed out')); }, timeout);
    child.stdout.on('data', bytes => { out += bytes; });
    child.stderr.on('data', bytes => { err += bytes; });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => { clearTimeout(timer); code === 0 ? resolve(out.trim()) : reject(new Error(`Helper exit ${code}: ${err.slice(-3000)}`)); });
  });
}
const powershell = (script, timeout = 60000) => command('pwsh.exe', ['-NoProfile', '-NonInteractive', '-Command', script], timeout);
await fs.mkdir(output, { recursive: true });
const writeReport = () => fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ ...report, stage }, null, 2) + '\n');
await writeReport();
try {
  check('disposable hosted Windows only', process.platform === 'win32' && process.env.GITHUB_ACTIONS === 'true' && process.env.RUNNER_OS === 'Windows' && process.env.RUNNER_ENVIRONMENT === 'github-hosted');
  const files = await fs.readdir(artifact);
  const installers = files.filter(name => name.endsWith('-setup.exe'));
  check('one pinned installer', installers.length === 1);
  const installer = path.join(artifact, installers[0]);
  check('installer bytes match successful build log', hash(await fs.readFile(installer)) === pin.installerSha256);
  const notes = await fs.readFile(path.join(artifact, 'VALIDATION-NOTES.txt'), 'utf8');
  check('artifact names exact build and run', notes.includes(pin.build) && notes.includes(String(pin.run)));
  const userData = path.join(process.env.APPDATA, 'OfferPilot Desktop');
  check('real APPDATA profile is fresh', !await exists(userData));
  const registered = await powershell("@(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -eq 'OfferPilot Desktop' }).Count");
  check('no existing OfferPilot installation', registered === '0');
  const scratch = await fs.mkdtemp(path.join(process.env.RUNNER_TEMP, 'resume-export-native-'));
  installDir = path.join(scratch, 'fresh installed app');
  const downloads = path.join(scratch, 'downloads'); await fs.mkdir(downloads);
  stage = 'install';
  // NSIS /D must be last and unquoted, even when it contains spaces.
  const quote = value => `'${value.replaceAll("'", "''")}'`;
  await powershell(`$p = [Diagnostics.ProcessStartInfo]::new(); $p.FileName=${quote(installer)}; $p.Arguments=${quote('/S /currentuser /D=' + installDir)}; $p.UseShellExecute=$false; $c=[Diagnostics.Process]::Start($p); if (-not $c.WaitForExit(180000)) { $c.Kill(); throw 'Owned installer timed out' }; if ($c.ExitCode -ne 0) { throw 'Installer failed' }`, 200000);
  installed = true; exe = path.join(installDir, 'OfferPilot Desktop.exe');
  check('installed executable exists', await exists(exe));
  for (const filename of ['main.cjs', 'capabilities.cjs', 'preload.cjs']) {
    const source = await fs.readFile(path.join(here, '..', filename));
    const packaged = extractFile(path.join(installDir, 'resources/app.asar'), filename);
    check(`unmodified installed ${filename}`, source.toString().replaceAll('\r\n', '\n') === packaged.toString().replaceAll('\r\n', '\n'));
  }
  stage = 'launch';
  app = await _electron.launch({ executablePath: exe, cwd: installDir, chromiumSandbox: true, bypassCSP: false, timeout: 90000 });
  const identity = await app.evaluate(({ app }) => ({ pid: process.pid, path: process.execPath, userData: app.getPath('userData'), packaged: app.isPackaged }));
  check('real installed app and default fresh profile', identity.packaged && identity.path.toLowerCase() === exe.toLowerCase() && identity.userData.toLowerCase() === userData.toLowerCase());
  const unexpected = [], errors = [], exports = [], cspViolations = [];
  app.context().on('request', request => {
    const url = new URL(request.url());
    if (['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) && url.hostname !== '127.0.0.1') unexpected.push(url.origin);
    if (/\/api\/(chat|ai)\//.test(url.pathname)) unexpected.push(url.pathname);
  });
  await until('owner page', () => app.windows().some(page => /^http:\/\/127\.0\.0\.1:/.test(page.url()) && !page.url().includes('desktopSurface=haru')), 90000);
  page = app.windows().find(page => /^http:\/\/127\.0\.0\.1:/.test(page.url()) && !page.url().includes('desktopSurface=haru'));
  page.setDefaultTimeout(30000);
  page.setDefaultNavigationTimeout(30000);
  page.on('pageerror', error => errors.push(String(error)));
  page.on('response', response => { if (/\/export(?:-preview)?$/.test(new URL(response.url()).pathname)) exports.push(response); });
  await page.exposeBinding('recordNativeExportCsp', (_source, value) => cspViolations.push(value));
  // Do not reload while the installed application's initial navigation is
  // settling. Observe the real current renderer and independently read its
  // same-origin document route headers; this is not an initial-response trace.
  await page.getByRole('navigation', { name: '主导航', exact: true }).waitFor();
  await page.evaluate(() => document.addEventListener('securitypolicyviolation', event => window.recordNativeExportCsp({ directive: event.violatedDirective, blocked: event.blockedURI })));
  const documentCsp = await page.evaluate(async () => {
    const response = await fetch(location.href);
    if (!response.ok) throw new Error('Installed document route unavailable');
    return response.headers.get('content-security-policy');
  });
  check('installed document route CSP is unchanged (separate request)', documentCsp === require(path.join(here, '../capabilities.cjs')).contentSecurityPolicy);
  const security = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().filter(win => !win.webContents.getURL().includes('desktopSurface=haru')).map(win => win.webContents.getLastWebPreferences()));
  check('production renderer isolation remains enabled', security.length === 1 && security[0].sandbox && security[0].contextIsolation && security[0].webSecurity && !security[0].nodeIntegration);
  stage = 'real-editor';
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button', { name: '素材库', exact: true }).click();
  await page.locator('.op-module-tabs').getByRole('tab', { name: '简历', exact: true }).click();
  await page.waitForURL(url => url.searchParams.get('view') === 'resumes');
  await page.screenshot({ path: path.join(output, 'installed-resume-library.png'), timeout: 5000 });
  await button(page.locator('[aria-label="创建基础简历入口"]'), '新建空白简历').click();
  const editor = page.getByRole('region', { name: '编辑简历', exact: true }); await editor.waitFor();
  const title = '原生导出验收 合成简历';
  await editor.getByPlaceholder('简历标题', { exact: true }).fill(title);
  await editor.getByRole('navigation', { name: '简历章节', exact: true }).getByRole('button', { name: '基本信息', exact: true }).click();
  await editor.locator('label').filter({ hasText: /^姓名$/ }).locator('..').locator('input').fill('合成候选人 Native QA');
  await editor.getByRole('navigation', { name: '简历章节', exact: true }).getByRole('button', { name: '技能', exact: true }).click();
  await editor.getByPlaceholder('每行一个技能', { exact: true }).fill('Python\n真实原生窗口验收');
  await button(editor, '保存').click(); await editor.waitFor({ state: 'hidden' });
  await button(page.locator('.ant-card').filter({ has: page.getByText(title, { exact: true }) }), '编辑').click();
  await until('saved title hydration', () => editor.getByPlaceholder('简历标题', { exact: true }).inputValue().then(value => value === title));
  await button(editor, '导出简历').click();
  const modal = page.getByRole('dialog', { name: '导出已保存简历', exact: true }); await modal.waitFor();
  const pdf = button(modal, '下载 PDF');
  const docx = button(modal, '下载 DOCX');
  check('export initially requires preview', await pdf.isDisabled() && await docx.isDisabled());
  stage = 'native-pdf-preview';
  await button(modal, '生成 PDF 预览').click();
  const reviewed = modal.getByRole('checkbox');
  await until('real PDF worker rendered', () => reviewed.isEnabled());
  const pixels = await modal.locator('canvas').evaluate(canvas => {
    const bytes = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let dark = 0; for (let i = 0; i < bytes.length; i += 4) if (bytes[i+3] && bytes[i] < 180 && bytes[i+1] < 180 && bytes[i+2] < 180) dark++;
    return { dark, width: canvas.width, height: canvas.height };
  });
  check('actual PDF canvas has ink', pixels.dark > 200 && pixels.width > 500 && pixels.height > 500);
  check('preview alone cannot download', await pdf.isDisabled() && await docx.isDisabled());
  await page.screenshot({ path: path.join(output, 'installed-preview.png') });
  await reviewed.check();
  const resumeSnapshot = async () => {
    const snapshot = await page.evaluate(async () => {
      const response = await fetch('/api/resumes');
      return { status: response.status, value: await response.json() };
    });
    assert.equal(snapshot.status, 200, 'resume snapshot must be an authenticated successful read');
    assert.ok(Array.isArray(snapshot.value) && snapshot.value.length === 1 && snapshot.value[0].title === title, 'exact synthetic saved resume snapshot required');
    return snapshot.value;
  };
  const before = await resumeSnapshot();
  const defaultDownloads = await app.evaluate(({ app }) => app.getPath('downloads'));
  const downloadNames = async () => fs.readdir(defaultDownloads).then(names => names.sort(), error => { if (error.code === 'ENOENT') return []; throw error; });
  const defaultNamesBefore = await downloadNames();
  for (const [format, choice] of [['pdf','cancel'], ['pdf','save'], ['docx','save']]) {
    stage = `native-${format}-${choice}`;
    const destination = path.join(downloads, `selected-${format}-${choice}.${format}`);
    const dialogResult = command('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', path.join(here, 'dialog.ps1'), '-OwnerPid', String(identity.pid), '-Executable', exe, '-Choice', choice, '-Destination', destination, '-Screenshot', path.join(output, `${format}-${choice}-native-dialog.png`)], 45000);
    // Attach rejection handling before UI click: the sync native dialog blocks
    // the Electron main process until the independent UIA helper answers it.
    const observed = dialogResult.then(value => ({ value }), error => ({ error }));
    await (format === 'pdf' ? pdf : docx).click({ noWaitAfter: true });
    const result = await observed; if (result.error) throw result.error;
    const native = JSON.parse(result.value);
    await until('export UI settled', () => pdf.isEnabled());
    if (choice === 'save') {
      await until('native-selected file exists', () => exists(destination));
      const response = exports.at(-1); check(`${format} HTTP success`, response.status() === 200);
      const expected = await response.body();
      await until('native-selected file complete', async () => (await fs.stat(destination)).size === expected.length);
      const bytes = await fs.readFile(destination);
      check(`${format} saved bytes equal actual authenticated response`, bytes.equals(expected));
      check(`${format} format signature`, bytes.subarray(0, format === 'pdf' ? 5 : 2).toString() === (format === 'pdf' ? '%PDF-' : 'PK'));
      await fs.copyFile(destination, path.join(output, `saved.${format}`));
      report.downloads.push({ format, choice, native, bytes: bytes.length, sha256: hash(bytes) });
    } else {
      await sleep(500);
      check('no file observed in selected/default directory after native Cancel', (await fs.readdir(downloads)).length === 0 && JSON.stringify(await downloadNames()) === JSON.stringify(defaultNamesBefore));
      report.downloads.push({ format, choice, native, selectedDirectoryEmpty: true, defaultDirectoryUnchanged: true, observationAfterUiSettledMs: 500 });
    }
  }
  const after = await resumeSnapshot();
  check('exports did not change saved resumes', JSON.stringify(before) === JSON.stringify(after));
  check('no unexpected network/provider call', unexpected.length === 0);
  check('no renderer exception', errors.length === 0);
  check('no installed CSP violation', cspViolations.length === 0);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = { stage, message: String(error) }; process.exitCode = 1;
  await writeReport();
  if (page) {
    try {
      await page.screenshot({ path: path.join(output, 'failure.png'), timeout: 5000 });
      report.failureSurface = { url: page.url(), text: (await page.locator('body').innerText({ timeout: 5000 })).slice(0, 12000) };
    } catch { report.failureSurfaceUnavailable = true; }
  }
}
finally {
  // Persist the primary outcome before any cleanup can block on a native modal.
  await writeReport();
  if (app) {
    let timer;
    try { await Promise.race([app.close(), new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error('Electron close exceeded 15 seconds')), 15000); })]); }
    catch (error) {
      report.closeFailure = String(error); report.status = 'failed'; process.exitCode = 1;
      try { app.process().kill(); } catch { /* Exact launched child only; never a guessed PID. */ }
    } finally { clearTimeout(timer); }
  }
  // The runner is disposable; preserve the synthetic profile rather than
  // deleting app data. Uninstall only this harness's fresh installed executable.
  if (installed && !report.closeFailure) {
    try { await command(path.join(installDir, 'Uninstall OfferPilot Desktop.exe'), ['/S'], 120000); report.uninstalled = true; }
    catch (error) { report.uninstallFailure = String(error); report.status = 'failed'; process.exitCode = 1; }
  }
  await writeReport();
  console.log(JSON.stringify({ status: report.status, stage, checks: report.checks.length, failure: report.failure }));
}
// app.close may leave Playwright transport handles after its bounded failure.
// Evidence is flushed above; only this harness process exits. The disposable
// runner owns any remaining app descendants and performs final process cleanup.
process.exit(process.exitCode || 0);
