param([Parameter(Mandatory=$true)][string]$RepositoryRoot,
      [Parameter(Mandatory=$true)][string]$Catalog,
      [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
$webRoot = Join-Path $repoRoot 'web'
$groupNames = @('components-core','components-chat','components-interview','components-offer','components-support','features','layout','lib','services','theme')
function Import-Functions([string]$Path, [string[]]$Names) {
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "Group script parse failed: $Path" }
    foreach ($name in $Names) {
        $function = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true))
        if ($function.Count -ne 1) { throw "Missing/duplicate group function: $name" }
        # Load only the repository's pure grouping functions; never execute main.
        . ([scriptblock]::Create($function[0].Extent.Text.Replace("function $name", "function global:$name")))
    }
}
$catalogRows = Get-Content -LiteralPath $Catalog -Raw -Encoding utf8 | ConvertFrom-Json
Import-Functions (Join-Path $repoRoot 'scripts/windows-pytest-groups.ps1') @('Get-TestFiles','Get-Groups')
$files = @(Get-TestFiles)
$groups = Get-Groups $files
$assignments = @($groups.Values | ForEach-Object { $_ })
foreach ($file in $files) {
    if (@($assignments | Where-Object { $_ -ceq $file }).Count -ne 1) { throw "Backend group overlap/missing: $file" }
}
$backend = @($catalogRows | Where-Object { $_.surface -eq 'backend' })
foreach ($row in $backend) {
    if (@($assignments | Where-Object { $_.Replace('\','/') -ceq $row.file }).Count -ne 1) {
        throw "Backend node missing/duplicated: $($row.id)"
    }
}
foreach ($file in @('tests\agent_loop\test_confirmation.py','tests\agent_loop\test_runner.py')) {
    if ($groups.agent -cnotcontains $file) { throw "Agent loop file outside agent group: $file" }
}
Import-Functions (Join-Path $repoRoot 'scripts/windows-vitest-groups.ps1') @('Get-GroupForFile')
$frontend = @($catalogRows | Where-Object { $_.surface -eq 'frontend' })
$frontendGroups = @{}
foreach ($row in $frontend) {
    $group = Get-GroupForFile ($row.file.Substring(4))
    if (-not $frontendGroups.ContainsKey($group)) { $frontendGroups[$group] = 0 }
    $frontendGroups[$group]++
}
$counts = @{}
foreach ($name in $groups.Keys) {
    $counts[$name] = @($backend | Where-Object { $groups[$name] -ccontains $_.file.Replace('/','\') }).Count
}
@{ backend_nodes=$backend.Count; backend_groups=$counts; frontend_files=$frontend.Count;
   frontend_groups=$frontendGroups; duplicate_or_missing=0; execution_status='catalog_checked';
   release_eligible=$false } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Output -Encoding utf8
Write-Output "Group catalog checked: $($backend.Count) backend nodes, $($frontend.Count) frontend files, exactly once"
