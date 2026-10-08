// Secret-free preparation. No provider credential is referenced in this process.
import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { extractFile } from '@electron/asar';
import { getCurrentFuseWire, FuseState, FuseV1Options } from '@electron/fuses';
import { hash, treeFiles, verifyPayload, normalizeSourceText } from '../installed-ui/integrity.mjs';
import { validateMetadata } from '../installed-ui/contract.mjs';
import { DESKTOP_SOURCE_FILES } from '../installed-ui/desktop-source-manifest.mjs';
import { PIN, demand, safeCode, childEnvironment, validateFixedFiles, validateLiveProduct } from './contract.mjs';
import { githubReader } from './github-read.mjs';
import { preflight } from './preflight.mjs';

export async function quietCommand(command, args, env, timeout = 180000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    const chunks = []; let bytes = 0;
    const fail = () => reject(Object.assign(new Error('HELPER_COMMAND_FAILED'), { safeCode: 'HELPER_COMMAND_FAILED' }));
    const timer = setTimeout(() => { child.kill(); fail(); }, timeout);
    child.stderr.resume();
    child.stdout.on('data', data => { bytes += data.length; if (bytes < 8 * 1024 * 1024) chunks.push(data); });
    child.on('error', () => { clearTimeout(timer); fail(); });
    child.on('close', code => { clearTimeout(timer); if (code !== 0 || bytes >= 8 * 1024 * 1024) fail();
      else resolve(Buffer.concat(chunks).toString('utf8').replace(/^\uFEFF/, '').trim()); });
  });
}
export const exists = async (filename, access = fs.access) => {
  try { await access(filename); return true; } catch (error) { if (error?.code === 'ENOENT') return false; throw error; }
};
export async function prepare({ mode = 'live' } = {}) {
  demand(['live', 'mock'].includes(mode), 'INVALID_HARNESS');
  if (mode === 'live') validateLiveProduct();
  demand(process.platform === 'win32' && process.env.RUNNER_TEMP && process.env.APPDATA, 'HOSTED_WINDOWS_REQUIRED');
  demand(process.env.RUNNER_ENVIRONMENT === 'github-hosted', 'HOSTED_WINDOWS_REQUIRED');
  demand(!process.env.OFFERPILOT_REAL_AI_KEY, 'SECRET_PRESENT_DURING_PREPARATION');
  await validateFixedFiles();
  let approval;
  if (mode === 'live') approval = await preflight({ approval: true });
  else {
    demand(process.env.GITHUB_EVENT_NAME === 'push' && process.env.GITHUB_REPOSITORY === PIN.repository &&
      process.env.GITHUB_REF === `refs/heads/${PIN.branch}` && /^[0-9a-f]{40}$/.test(process.env.GITHUB_SHA || ''), 'TRIGGER_NOT_AUTHORIZED');
    approval = { helperSha: process.env.GITHUB_SHA, requestSha: process.env.GITHUB_SHA };
  }
  const read = githubReader(process.env.GH_TOKEN);
  const sourceEvidence = validateMetadata(...await Promise.all([
    read(`actions/runs/${PIN.runId}`), read(`actions/artifacts/${PIN.artifactId}`),
    read(`actions/runs/${PIN.runId}/artifacts?per_page=100`), read(`actions/runs/${PIN.runId}/jobs?per_page=100`),
    PIN.fullRegressionRunId === null ? null : read(`actions/runs/${PIN.fullRegressionRunId}`),
  ]));
  const root = path.join(process.env.RUNNER_TEMP, mode === 'live' ? 'offerpilot-bounded-ai' : 'offerpilot-bounded-ai-mock');
  demand(!await exists(root), 'SCRATCH_NOT_FRESH');
  const profile = path.join(process.env.APPDATA, 'OfferPilot Desktop');
  demand(!await exists(profile), 'PROFILE_NOT_FRESH');
  await fs.mkdir(root);
  const installer = path.join(process.env.AI_ARTIFACT_DIR, PIN.installer);
  demand(await hash(installer) === PIN.installerSha256, 'INSTALLER_SHA_MISMATCH');
  const installDir = path.join(root, '安装 Application');
  const exe = path.join(installDir, 'OfferPilot Desktop.exe');
  const backend = path.join(installDir, 'resources/backend/offerpilot-backend.exe');
  const env = childEnvironment(process.env);
  const zip = path.join(process.env.ProgramFiles, '7-Zip/7z.exe');
  const unpack = path.join(root, 'installer-payload'), payload = path.join(root, 'app-payload');
  await quietCommand(zip, ['x', installer, `-o${unpack}`, '-y'], env);
  const archives = (await treeFiles(unpack)).filter(name => path.basename(name) === 'app-64.7z');
  demand(archives.length === 1, 'PAYLOAD_COUNT_MISMATCH');
  await quietCommand(zip, ['x', path.join(unpack, archives[0]), `-o${payload}`, '-y'], env);
  const result = JSON.parse(await quietCommand('pwsh.exe', ['-NoProfile', '-NonInteractive', '-File',
    path.resolve('desktop/installed-ui/windows.ps1'), '-Mode', 'install'], {
    ...env, UI_INSTALLER: installer, UI_INSTALL_DIR: installDir, UI_USER_DATA: profile,
  }, 210000));
  demand(result.exitCode === 0 && !await exists(profile), 'INSTALLER_UNEXPECTED_LAUNCH');
  const count = await verifyPayload(installDir, payload);
  const source = path.resolve(process.env.AI_PRODUCT_SOURCE);
  for (const name of DESKTOP_SOURCE_FILES) {
    demand(normalizeSourceText(extractFile(path.join(installDir, 'resources/app.asar'), name)) ===
      normalizeSourceText(await fs.readFile(path.join(source, 'desktop', name))), 'PRODUCT_SOURCE_MISMATCH');
  }
  const before = await hash(exe);
  const fuse = await getCurrentFuseWire(exe);
  demand(fuse[FuseV1Options.EnableNodeCliInspectArguments] === FuseState.ENABLE && await hash(exe) === before,
    'EXISTING_INSPECT_FUSE_REQUIRED');
  await fs.writeFile(path.join(root, 'prepared.json'), JSON.stringify({ schema: 1, mode, root, profile, installDir, exe, backend,
    helperSha: approval.helperSha, requestSha: approval.requestSha, exeSha256: before,
    artifact: sourceEvidence, checkedPayloadFiles: count, profileOwned: false }));
  // No profile exists yet. Ownership is recorded atomically by run.mjs after exclusive mkdir.
  console.log('Pinned installer and installed payload verified without provider credentials.');
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try { await prepare(); } catch (error) { console.error(safeCode(error)); process.exitCode = 1; }
}
