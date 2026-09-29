#!/usr/bin/env bash
set -euo pipefail
: "${STAGING_PROJECT_ID:?Set STAGING_PROJECT_ID}"
supabase link --project-ref "$STAGING_PROJECT_ID"
supabase db push --dry-run
read -r -p "Apply migrations to STAGING only? Type STAGING: " answer
[ "$answer" = "STAGING" ] || exit 1
supabase db push
supabase migration list
