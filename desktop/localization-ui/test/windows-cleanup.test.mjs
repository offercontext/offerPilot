import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const pwsh = process.platform === 'win32' ? 'pwsh.exe' : 'pwsh';
const available = spawnSync(pwsh, ['-NoProfile', '-NonInteractive', '-Command', '$PSVersionTable.PSVersion.ToString()'], { encoding: 'utf8' });
test('real PowerShell JSON round-trip retains exact process birth identity before mocked cleanup', {
  skip: process.platform !== 'win32' && available.error?.code === 'ENOENT' ? 'PowerShell unavailable; required on Windows runner' : false,
}, () => {
  assert.equal(available.status, 0, 'PowerShell must run on the actual Windows validation runner');
  const script = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../windows.ps1');
  const identity = { pid: 12345, parentPid: 12344, path: 'C:\\runner\\安装\\OfferPilot Desktop.exe', created: '2026-11-05T13:13:07.1234567Z' };
  const inputs = [identity, { ...identity, pid: 12346 }, { ...identity, parentPid: 1 },
    { ...identity, path: 'C:\\other\\OfferPilot Desktop.exe' }, { ...identity, created: '2026-11-05T13:13:07.1234568Z' },
    { ...identity, created: null }, { ...identity, created: 'invalid' }, { ...identity, path: identity.path.toUpperCase() }];
  const command = `
    $ErrorActionPreference = 'Stop'
    $global:stoppedIds = @()
    function Get-CimInstance { param($ClassName)
      [pscustomobject]@{ ProcessId=12345; ParentProcessId=12344; ExecutablePath=$env:LOCALIZATION_EXE;
        CreationDate=[DateTime]::Parse('2026-11-05T13:13:07.1234567Z', [Globalization.CultureInfo]::InvariantCulture,
          [Globalization.DateTimeStyles]::RoundtripKind) }
    }
    function Stop-Process { [CmdletBinding()]param($Id, [switch]$Force)
      if (-not $Force) { throw 'expected force' }
      $global:stoppedIds += [int]$Id
    }
    $results = @(foreach ($raw in ($env:LOCALIZATION_TEST_INPUTS | ConvertFrom-Json)) {
      $env:LOCALIZATION_OWNED_IDENTITIES = $raw
      $before = $global:stoppedIds.Count
      $result = . $env:LOCALIZATION_TEST_SCRIPT -Mode cleanup
      @{ result=($result | ConvertFrom-Json); mockedStops=($global:stoppedIds.Count - $before) }
    })
    @{ version=$PSVersionTable.PSVersion.ToString(); results=$results } | ConvertTo-Json -Depth 6 -Compress
  `;
  const child = spawnSync(pwsh, ['-NoProfile', '-NonInteractive', '-Command', command], {
    encoding: 'utf8', timeout: 30000, env: { ...process.env, LOCALIZATION_TEST_SCRIPT: script,
      LOCALIZATION_EXE: identity.path, LOCALIZATION_BACKEND: 'C:\\runner\\安装\\resources\\backend\\offerpilot-backend.exe',
      LOCALIZATION_TEST_INPUTS: JSON.stringify(inputs.map(item => JSON.stringify([item]))) },
  });
  assert.equal(child.status, 0, child.stderr);
  const output = JSON.parse(child.stdout.replace(/^\uFEFF/, '').trim());
  assert.deepEqual(output.results.map(item => item.result.stopped), [1, 0, 0, 0, 0, 0, 0, 1]);
  assert.deepEqual(output.results.map(item => item.mockedStops), [1, 0, 0, 0, 0, 0, 0, 1]);
});
