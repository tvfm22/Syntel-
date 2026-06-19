# =============================================================================
# Syntel - Dead File Removal Script (PowerShell)
# =============================================================================
# Usage:
#   .\purge_dead_files.ps1           # dry-run
#   .\purge_dead_files.ps1 -Force    # actually remove
# =============================================================================

param(
    [switch]$Force
)

$ProjectDir = Split-Path -Parent $PSCommandPath
$DryRun = -not $Force

$DeadFiles = @(
    "check_templates.py"
    "create_sample_users.py"
    "fix_users_without_cafe.py"
    "list_users.py"
    "reset_passwords.py"
    "test_403_fix.py"
    "db_data.json"
    "wget-log"
    "CSRF_FIX_GUIDE.md"
    "FIX_403_ERROR_REPORT.md"
    "syntel/test_bubble_api.py"
)

if ($DryRun) {
    Write-Host "=== DRY RUN - no files will be removed ===" -ForegroundColor Yellow
    Write-Host "Re-run with: .\purge_dead_files.ps1 -Force"
    Write-Host ""
} else {
    Write-Host "=== REMOVAL MODE ===" -ForegroundColor Red
}

$removed = 0
$skipped = 0

foreach ($relpath in $DeadFiles) {
    $fullpath = Join-Path $ProjectDir $relpath

    if (-not (Test-Path $fullpath)) {
        Write-Host "  [SKIP]  $relpath  (already gone)" -ForegroundColor Gray
        $skipped++
        continue
    }

    $item = Get-Item $fullpath
    $size = "{0:N0} bytes" -f $item.Length

    if ($DryRun) {
        Write-Host "  [DRY]   $relpath  ($size)" -ForegroundColor DarkYellow
    } else {
        Remove-Item $fullpath -Force
        Write-Host "  [REMOVED]  $relpath  ($size)" -ForegroundColor Green
        $removed++
    }
}

Write-Host ""
if ($DryRun) {
    Write-Host "Done. $($DeadFiles.Count) files checked, none removed (dry run)." -ForegroundColor Yellow
} else {
    Write-Host "Done. $removed removed, $skipped already gone." -ForegroundColor Green
}

$copyPath = Join-Path $ProjectDir "syntel/scripts/tests/test_bubble_api.py"
if (Test-Path $copyPath) {
    Write-Host ""
    Write-Host "NOTE: A copy of test_bubble_api.py still exists at:" -ForegroundColor Cyan
    Write-Host "      syntel/scripts/tests/test_bubble_api.py" -ForegroundColor Cyan
    Write-Host "      (keeping it - scripts/tests/ is the proper location)" -ForegroundColor Cyan
}
