import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { quietCommand, exists } from './prepare.mjs';
import { childEnvironment, demand, safeCode } from './contract.mjs';
export async function cleanOwnedFiles(env = process.env, mode = 'live') {
  demand(['live', 'mock'].includes(mode), 'CLEANUP_PATH_UNVERIFIED');
  demand(env.RUNNER_TEMP && env.APPDATA && env.RUNNER_ENVIRONMENT === 'github-hosted', 'CLEANUP_HOST_UNVERIFIED');
  const root = path.join(env.RUNNER_TEMP, mode === 'live' ? 'offerpilot-bounded-ai' : 'offerpilot-bounded-ai-mock');
  const statePath = path.join(root, 'prepared.json');
  if (!await exists(statePath)) return { status: 'not-created', profileRemoved: false };
  const prepared = JSON.parse(await fs.readFile(statePath, 'utf8'));
  const profile = path.join(env.APPDATA, 'OfferPilot Desktop');
  demand(prepared.mode === mode && prepared.root === root && prepared.profile === profile && prepared.installDir === path.join(root, '安装 Application') &&
    prepared.exe === path.join(prepared.installDir, 'OfferPilot Desktop.exe') &&
    prepared.backend === path.join(prepared.installDir, 'resources/backend/offerpilot-backend.exe'), 'CLEANUP_PATH_UNVERIFIED');
  const windowsEnv = { ...childEnvironment(env), UI_EXE: prepared.exe, UI_BACKEND: prepared.backend };
  const command = mode => ['-NoProfile', '-NonInteractive', '-File', path.resolve('desktop/installed-ui/windows.ps1'), '-Mode', mode];
  const snapshot = JSON.parse(await quietCommand('pwsh.exe', command('snapshot'), windowsEnv, 30000));
  await quietCommand('pwsh.exe', command('cleanup'), { ...windowsEnv, UI_OWNED_IDENTITIES: JSON.stringify(snapshot.processes) }, 30000);
  const after = JSON.parse(await quietCommand('pwsh.exe', command('snapshot'), windowsEnv, 30000));
  demand(after.processes.length === 0, 'OWNED_PROCESS_CLEANUP_FAILED');
  if (prepared.profileOwned === true) await fs.rm(profile, { recursive: true, force: true, maxRetries: 0 });
  else demand(!await exists(profile), 'UNOWNED_PROFILE_PRESERVED');
  demand(!await exists(profile), 'PROFILE_CLEANUP_FAILED');
  await fs.rm(root, { recursive: true, force: true, maxRetries: 0 });
  return { status: 'passed', profileRemoved: prepared.profileOwned === true };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const result = await cleanOwnedFiles(process.env, process.argv.includes('--mock') ? 'mock' : 'live');
    console.log(result.status === 'passed' ? 'Owned temporary profile removed.' : 'No owned profile was created.');
  } catch (error) { console.error(safeCode(error)); process.exitCode = 1; }
}
