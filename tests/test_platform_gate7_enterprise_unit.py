from __future__ import annotations

from types import SimpleNamespace

from orchestrator.services.enterprise_command_service import (
    BUSY_MESSAGE,
    format_enterprise_command_prompt,
)
from orchestrator.services.enterprise_resolution_service import (
    build_enterprise_option_mapping,
)
from orchestrator.services.user_interaction_service import format_question_prompt


class FakeWriterClient:
    def list_enterprises(self, correlation_id: str) -> list[dict[str, str]]:
        assert correlation_id == "corr"
        return [
            {"id": "b", "display_name": "Árvore"},
            {"id": "c", "display_name": "Beta"},
            {"id": "a", "display_name": "arvore"},
        ]


def test_enterprise_options_are_deterministic_and_store_real_ids() -> None:
    mapping = build_enterprise_option_mapping(FakeWriterClient(), "corr")  # type: ignore[arg-type]
    assert [mapping[str(i)]["enterprise_id"] for i in range(1, 4)] == ["a", "b", "c"]


def test_command_prompt_appends_clear_as_n_plus_one() -> None:
    session = SimpleNamespace(
        option_mapping={"1": {"enterprise_id": "a", "display_name": "A"}},
        clear_option_position=2,
    )
    assert format_enterprise_command_prompt(session).endswith("2 - Limpar seleção")


def test_document_enterprise_prompt_uses_durable_mapping() -> None:
    prompt = format_question_prompt(
        "enterprise_selection",
        {"1": {"enterprise_id": "stable-id", "display_name": "Empresa"}},
    )
    assert "1 - Empresa" in prompt
    assert "stable-id" not in prompt


def test_busy_message_is_frozen() -> None:
    assert "Existe um lançamento aguardando sua resposta" in BUSY_MESSAGE
    assert "Conclua a pergunta atual" in BUSY_MESSAGE
