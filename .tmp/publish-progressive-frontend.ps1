$taskRoot = [System.IO.Path]::GetFullPath('C:\Users\Thyago\Documents\Visual Studio Code\PriceScout')
$taskStage = Join-Path $PSScriptRoot 'progressive-frontend'
$taskFiles = @('hooks/useProductMatchRun.ts','utils/api/contracts.ts','utils/api/services.ts','utils/api/rate-limit-scope.ts','utils/match-run-live.ts','utils/match-run-offers.ts','utils/match-run-live.test.ts','utils/match-run-offers.test.ts','components/ProductMatchBanner.tsx','components/ProductMatchResults.tsx','components/ProductMatchResults.module.css','app/admin/produtos/[id]/page.tsx','memory/working/2026-10-05-progressive-match.md')
foreach ($taskFile in $taskFiles) {
    $taskSource = [System.IO.Path]::GetFullPath((Join-Path $taskStage $taskFile))
    $taskTarget = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $taskFile))
    if (-not $taskTarget.StartsWith($taskRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Destino fora do projeto PriceScout' }
    if (-not (Test-Path -LiteralPath $taskSource -PathType Leaf)) { throw "Arquivo staging ausente: $taskFile" }
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($taskTarget)) | Out-Null
    Copy-Item -LiteralPath $taskSource -Destination $taskTarget
    Write-Output $taskFile
}
