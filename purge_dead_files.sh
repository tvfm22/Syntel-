#!/usr/bin/env bash
# =============================================================================
# Syntel - Dead File Removal Script
# =============================================================================
# Safely removes temporary development artifacts and unused files.
#
# Usage:
#   chmod +x purge_dead_files.sh
#   ./purge_dead_files.sh          # dry-run (shows what would be removed)
#   ./purge_dead_files.sh --force  # actually remove
#
# To preview before running:  ./purge_dead_files.sh
# To execute removal:        ./purge_dead_files.sh --force
# =============================================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=true

if [[ "${1:-}" == "--force" ]]; then
    DRY_RUN=false
    echo "=== REMOVAL MODE ==="
else
    echo "=== DRY RUN - no files will be removed ==="
    echo "Re-run with --force to actually delete."
    echo ""
fi

# Dead files list
DEAD_FILES=(
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

removed_count=0
skipped_count=0

for relpath in "${DEAD_FILES[@]}"; do
    fullpath="$PROJECT_DIR/$relpath"

    if [[ ! -f "$fullpath" ]]; then
        echo "  [SKIP]  $relpath  (already gone)"
        ((skipped_count++))
        continue
    fi

    size=$(wc -c < "$fullpath" | tr -d ' ')

    if $DRY_RUN; then
        echo "  [DRY]   $relpath  ($(numfmt --to=iec "$size" 2>/dev/null || echo "$size bytes"))"
    else
        rm "$fullpath"
        echo "  [REMOVED]  $relpath  ($(numfmt --to=iec "$size" 2>/dev/null || echo "$size bytes"))"
        ((removed_count++))
    fi
done

echo ""

if $DRY_RUN; then
    echo "Done. ${#DEAD_FILES[@]} files checked, none removed (dry run)."
else
    echo "Done. $removed_count removed, $skipped_count already gone."
fi

if [[ -f "$PROJECT_DIR/syntel/scripts/tests/test_bubble_api.py" ]]; then
    echo ""
    echo "NOTE: A copy of test_bubble_api.py still exists at:"
    echo "      syntel/scripts/tests/test_bubble_api.py"
    echo "      (keeping it - scripts/tests/ is the proper location)"
fi
