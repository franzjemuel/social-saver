import pytest

from core.repository import Repository


@pytest.mark.asyncio
async def test_invalid_provider_invocation_inputs_fail_before_database_access():
    class Pool:
        pass
    repo = Repository(Pool())
    with pytest.raises(ValueError):
        await repo.initialize_owned_highlight_provider_budget("u", "j", "p", 0)
    with pytest.raises(ValueError):
        await repo.reserve_owned_highlight_provider_invocation(
            "u", "j", "p", provider_namespace="", operation="unknown", maximum_micro_usd=1,
        )
    with pytest.raises(ValueError):
        await repo.finish_owned_highlight_provider_invocation("u", "j", "i", status="reserved")


def test_invocation_migration_uses_integer_budget_and_nonretryable_indeterminate_state():
    text = open("supabase/migrations/026_highlight_provider_invocation_ledger.sql").read()
    assert "budget_micro_usd bigint" in text
    assert "reserved_micro_usd bigint" in text
    assert "'indeterminate'" in text
    assert "provider_run_receipt" in text
