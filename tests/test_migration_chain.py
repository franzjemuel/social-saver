from pathlib import Path
import re

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


def _versions():
    rows = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        match = re.fullmatch(r"(\d{3})_(.+)\.sql", path.name)
        assert match, f"invalid migration filename: {path.name}"
        rows.append((int(match.group(1)), path.name))
    return rows


def test_migration_versions_are_unique_and_contiguous():
    rows = _versions()
    versions = [v for v, _ in rows]
    assert len(versions) == len(set(versions)), f"duplicate migration versions: {rows}"
    assert versions == list(range(1, len(versions) + 1)), f"migration chain has a gap or reorder: {rows}"


def test_entitlements_precede_telegram_stars():
    names = [name for _, name in _versions()]
    assert names.index("006_entitlements.sql") < names.index("007_telegram_stars.sql")
