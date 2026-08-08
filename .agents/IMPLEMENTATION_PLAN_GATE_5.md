# Plan de Implementação — Gate 5: Regras BOT DF (Pure Component)

> **Status**: APPROVED / COMPLETE
> **Escopo**: Componente puro de regras financeiras de negócio
> **Gate 4**: APPROVED / COMPLETE / FROZEN
> **Gate 6**: NOT STARTED
> **Planning**: APPROVED
> **Implementation Plan**: APPROVED
> **Implementation**: COMPLETE
> **Verification**: PASSED
> **G5-APPROVED**: true
> **Migrations**: NONE REQUIRED
> **Persistent/staging/production/remote migration execution**: NOT AUTHORIZED

---

## 1. Objetivo do Gate 5

O Gate 5 implementa o **componente puro de avaliação de regras de negócio financeiras** do BOT DF (`BusinessRulesEvaluatorService`). Ele consome metadados de extração normalizada e aplica deterministicamente as regras financeiras da DF Holding:

1. Validação de valor (`amount > 0`);
2. Resolução de datas (`document_date` vs. `message_received_at` com timezone `America/Sao_Paulo`);
3. Classificação do indicador `date_source` (`DOCUMENT` ou `MESSAGE_TIMESTAMP`);
4. Normalização de CPF/CNPJ (apenas dígitos) e cruzamento contra lista configurada da DF Holding;
5. Classificação de direção (`expense`, `income`, `ambiguous`, `unknown`);
6. Derivação em memória de elegibilidade de auto-gravação (`is_eligible`);
7. Seleção de `question_type` reutilizando o vocabulário fechado do Fase 4E (`"transaction_direction"`, `"transaction_amount"`, `"document_classification"`);
8. Formatação pura da mensagem final de sucesso pós-`COMMITTED`.

O Gate 5 é um **componente puro**. Ele **NÃO** altera o runtime do `fifo_worker_service.py`, **NÃO** transita o item para `WAITING_USER_INPUT`, **NÃO** aloca `UserInteraction`, **NÃO** envia mensagens WUZAPI e **NÃO** modifica contratos do Gate 4. A integração no runtime do worker FIFO e o disparo de prompts de esclarecimento pertencem exclusivamente ao **Gate 6**.

---

## 2. Regras de Negócio Autorizativas (PRD RN-010 a RN-017)

- **RN-010 (Campos Mínimos)**: `amount` (Decimal > 0), `transaction_date` (DateTime com timezone), `direction` (`expense` ou `income`).
- **RN-011 (Data)**: Se houver `document_date` válido em formato ISO `YYYY-MM-DD`, utilizar a data do documento com `date_source = "DOCUMENT"`. Caso contrário, utilizar o timestamp da mensagem com `date_source = "MESSAGE_TIMESTAMP"`.
- **RN-012 (Lista CPF/CNPJ DF)**: Placeholders PRD: `CNPJ_1 = 00.000.000/0000-00`, `CNPJ_2 = 11.111.111/1111-11`, `CPF_1 = 000.000.000-00`, `CPF_2 = 111.111.111-11`.
- **RN-013 (Direction)**:
  - DF pagador e não recebedor -> `expense`
  - DF recebedor e não pagador -> `income`
  - DF nos dois lados -> `ambiguous` -> requer esclarecimento
  - DF em nenhum lado / dados ausentes -> `unknown` -> requer esclarecimento
- **RN-014 (Valor ausente/inválido)**: `amount` ausente, `<= 0` ou nulo não é elegível para gravação automática.
- **RN-016 (Sem confirmação obrigatória)**: Com `amount > 0` e `direction ∈ {expense, income}` válidos, autoriza a gravação automática via Gate 4 Persistence Engine.
- **RN-017 (Resposta Final)**: Exibe a mensagem de sucesso (`"✅ Gravado com sucesso..."`) **APENAS APÓS** confirmação `COMMITTED` do Database Writer.

---

## 3. Timezone Oficial de Negócio

O timezone oficial de negócio da DF Holding é:
```python
from zoneinfo import ZoneInfo
BUSINESS_TIMEZONE = ZoneInfo("America/Sao_Paulo")
```
Não utilizar offsets fixos (ex: `-03:00`) devido a alterações históricas ou regras de fuso horário.

---

## 4. Semântica e Representação da Data `DOCUMENT`

Quando `date_source == "DOCUMENT"`:
- Fonte semântica: Data textual de calendário ISO `"YYYY-MM-DD"` extraída diretamente do documento (ex: `"2026-08-01"`).
- Representação física de `transaction_date`: Objeto `datetime` timezone-aware ajustado para 00:00:00 no fuso `ZoneInfo("America/Sao_Paulo")`:
```python
from datetime import datetime, time

transaction_date = datetime.combine(
    parsed_document_date,
    time.min,
    tzinfo=BUSINESS_TIMEZONE,
)
```
- A conversão preserva a integridade do dia de calendário no fuso `America/Sao_Paulo`.
- `document_date` permanece armazenado e serializado na forma textual de calendário `"YYYY-MM-DD"`.

---

## 5. Semântica e Representação da Data `MESSAGE_TIMESTAMP`

Quando `date_source == "MESSAGE_TIMESTAMP"`:
- Fonte semântica: Instante real de recebimento da mensagem de entrada (`message_received_at`).
- Representação física de `transaction_date`: Instante real timezone-aware normalizado para UTC.
- O dia de calendário visível ao usuário é determinado após converter esse instante para `ZoneInfo("America/Sao_Paulo")`.

---

## 6. Contrato de `date_source`

O campo `date_source` admite estritamente dois valores duráveis:
- `"DOCUMENT"`: Quando a data foi obtida com sucesso do texto do documento.
- `"MESSAGE_TIMESTAMP"`: Quando a data do documento estava ausente, inválida ou vazia, utilizando o timestamp de recebimento da mensagem.

---

## 7. Normalização de CPF/CNPJ e Configuração de Placeholders

```python
import re

def normalize_digits(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits if digits else None
```

Configuração em `apps/orchestrator/src/orchestrator/config.py`:
```python
df_holding_identifiers: list[str] = [
    "00000000000000", # CNPJ_1
    "11111111111111", # CNPJ_2
    "00000000000",    # CPF_1
    "11111111111",    # CPF_2
]
```

---

## 8. Tabela de Decisão de Direção (`direction`)

| Payer Normalizado em `df_holding_identifiers` | Receiver Normalizado em `df_holding_identifiers` | Direção Resultante | Status da Direção | `question_type` em Falha de Elegibilidade |
|---|---|---|---|---|
| **SIM** | **NÃO** | `expense` | DETERMINADO | None (Elegível) |
| **NÃO** | **SIM** | `income` | DETERMINADO | None (Elegível) |
| **SIM** | **SIM** | `ambiguous` | INDETERMINADO | `"transaction_direction"` |
| **NÃO** | **NÃO** | `unknown` | INDETERMINADO | `"transaction_direction"` |

---

## 9. Regras de Elegibilidade de Valor (`amount`)

- `amount > Decimal("0.00")`: Válido.
- `amount == Decimal("0.00")` ou `amount < 0`: Inválido -> `question_type = "transaction_amount"`.
- `amount is None` / ausente: Inválido -> `question_type = "transaction_amount"`.

---

## 10. Mapeamento Estrito de `question_type`

O Gate 5 reutiliza estritamente o vocabulário fechado do Fase 4E (`user_interaction_service.py`):
```python
VALID_QUESTION_TYPES = {
    "transaction_direction",
    "transaction_amount",
    "document_classification",
}
```

**Prioridade em caso de múltiplos campos pendentes** (`QUESTION_PRIORITY`):
1. `direction` em (`"ambiguous"`, `"unknown"`, `None`) -> `"transaction_direction"`
2. `amount` em (`None`, `<= 0`) -> `"transaction_amount"`
3. `document_type` em (`None`, `"unknown"`) -> `"document_classification"`

---

## 11. Contrato da Classe `FinancialEvaluationResult`

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

@dataclass(frozen=True)
class FinancialEvaluationResult:
    is_eligible_for_auto_write: bool
    direction: str                             # "expense", "income", "ambiguous", "unknown"
    amount: Optional[Decimal]                  # Quantizado com 2 casas decimais se válido
    transaction_date: datetime                 # Timezone-aware: 00:00 America/Sao_Paulo (DOCUMENT) ou UTC instant (MESSAGE_TIMESTAMP)
    document_date_str: Optional[str]           # ISO "YYYY-MM-DD" se DOCUMENT
    date_source: str                           # "DOCUMENT" ou "MESSAGE_TIMESTAMP"
    question_type: Optional[str]               # "transaction_direction", "transaction_amount", etc. se não elegível
    clarification_reason: Optional[str]        # "MISSING_AMOUNT", "INVALID_AMOUNT", "AMBIGUOUS_DIRECTION", etc. (In-memory diagnostic string)
```

**Contrato de `clarification_reason`**:
- É um valor puramente diagnóstica em memória no dataclass `FinancialEvaluationResult`.
- **NÃO** é persistido no banco de dados (`processing_items`).
- **NÃO** cria nenhuma coluna de banco de dados.
- **NÃO** exige nenhuma migração de banco de dados.
- O vocabulário durável de interação no banco permanece sendo `question_type`.
- O Gate 6 poderá consumir `clarification_reason` em memória para selecionar comportamento de runtime.

---

## 12. Derivação da Elegibilidade de Gravação Automática

A elegibilidade **NÃO É PERSISTIDA** em nenhuma nova coluna de banco. É derivada em memória:
```python
is_eligible = bool(
    amount is not None
    and amount > Decimal("0.00")
    and direction in ("expense", "income")
)
```

---

## 13. Contrato do Formatador da Mensagem de Sucesso

```python
def format_success_message(direction: str, amount: Decimal, display_date: date) -> str:
    lbl = "Despesa" if direction == "expense" else "Entrada"
    amt_str = f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    dt_str = display_date.strftime("%d/%m/%Y")
    return f"✅ Gravado com sucesso.\n\n{lbl} de {amt_str} realizada em {dt_str}."
```
- Elegibilidade (`is_eligible == True`) **NÃO PRODUZ AUTOMATICAMENTE** a mensagem de sucesso. A elegibilidade apenas autoriza o despacho para o Gate 4 Persistence Engine.
- A mensagem de sucesso é formatada e autorizada **EXCLUSIVAMENTE APÓS** resposta `COMMITTED` confirmada pelo Database Writer.
- Respostas `REJECTED`, `RETRYABLE_FAILURE` e `PERSIST_OUTCOME_UNKNOWN` **NUNCA** produzem a mensagem de sucesso.
- O formatador puro de mensagem é testado de forma independente em testes unitários sob invocação simulada pós-`COMMITTED`.

---

## 14. Fronteira do Componente Puro do Gate 5

O Gate 5 constrói o serviço `BusinessRulesEvaluatorService` que recebe um `ProcessingItem` (ou seu payload de extração e timestamp) e retorna um `FinancialEvaluationResult`.

O Gate 5 **NÃO** altera o arquivo `fifo_worker_service.py` e **NÃO** altera a execução do worker de fila em ambiente de runtime.

---

## 15. Fronteira de Runtime Adiada para o Gate 6

No Gate 6, o worker de fila em runtime chamará o `BusinessRulesEvaluatorService`:
- Se `result.is_eligible_for_auto_write == True`: invoca `PersistenceService.dispatch_persistence(...)` (fluxo Gate 4).
- Se `result.is_eligible_for_auto_write == False`: invoca `user_interaction_service.dispatch_user_prompt(...)` (fluxo Gate 4E/Gate 6), reservando prompt, enviando via WUZAPI e transitando para `WAITING_USER_INPUT`.

---

## 16. Propriedade do Estado `WAITING_USER_INPUT`

A transição para `WAITING_USER_INPUT` pertence **UNICA E EXCLUSIVAMENTE** ao serviço `user_interaction_service.dispatch_user_prompt(...)` (Transação 3 após confirmação do envio do prompt). O Gate 5 **NUNCA** altera diretamente o status do item para `WAITING_USER_INPUT`.

---

## 17. Fronteira Congelada do Gate 4

O Gate 5 respeita integralmente e não altera a arquitetura aprovada do Gate 4:
- Fila FIFO monotônica e locks por conversa;
- `PersistenceService` e despacho exclusivo com `persistence_generation`;
- `DBWriterClient` e comunicação HTTP com o Database Writer;
- Indiferença a tentativas duplicadas e reconciliação `GET /writes/{key}`.

---

## 18. Arquivos Exatos a Criar / Modificar

### NOVO:
- `apps/orchestrator/src/orchestrator/services/business_rules_evaluator.py`

### MODIFICAR:
- `apps/orchestrator/src/orchestrator/config.py` (adicionar `df_holding_identifiers` aos Settings)

---

## 19. Testes Exatos a Adicionar

### NOVO:
- `tests/test_platform_gate5_business_rules_unit.py` (testes de unidade e componente cobrindo todas as regras de avaliação, normalização, matriz de direção, regras de valor, datas e formatação).

---

## 20. Mapeamento de Tasks G5-T01 a G5-T11

| Task ID | Descrição | Mapeamento no Componente Gate 5 |
|---|---|---|
| **G5-T01 P0** | **Máquina de estados** | Classe `BusinessRulesEvaluatorService` e modelo `FinancialEvaluationResult`. |
| **G5-T02 P0** | **amount > 0** | Validação em `BusinessRulesEvaluatorService._eval_amount`. |
| **G5-T03 P0** | **document_date** | Validação em `BusinessRulesEvaluatorService._eval_date` com timezone `America/Sao_Paulo`. |
| **G5-T04 P0** | **fallback timestamp** | Fallback para `message_received_at` quando `document_date` for ausente/inválido. |
| **G5-T05 P0** | **date_source** | Atribuição de `"DOCUMENT"` ou `"MESSAGE_TIMESTAMP"` no resultado. |
| **G5-T06 P0** | **Lista CPF/CNPJ placeholder** | Leitura de `settings.df_holding_identifiers` e normalização `normalize_digits`. |
| **G5-T07 P0** | **payer DF → expense** | Matriz de direção em `BusinessRulesEvaluatorService._eval_direction`. |
| **G5-T08 P0** | **receiver DF → income** | Matriz de direção em `BusinessRulesEvaluatorService._eval_direction`. |
| **G5-T09 P0** | **ambos → ambiguous** | Matriz de direção em `BusinessRulesEvaluatorService._eval_direction`. |
| **G5-T10 P0** | **nenhum → unknown** | Matriz de direção em `BusinessRulesEvaluatorService._eval_direction`. |
| **G5-T11 P0** | **Mensagem final** | Função pura `format_success_message(...)`. |

---

## 21. Mapeamento de Testes G5-X01 a G5-X10

| Teste ID | Cenário | Tipo de Teste | Validação no Teste de Unidade |
|---|---|---|---|
| **G5-X01** | DF Payer Only | Unidade / Componente | `direction == "expense"`, `is_eligible == True`, `question_type == None`. |
| **G5-X02** | DF Receiver Only | Unidade / Componente | `direction == "income"`, `is_eligible == True`, `question_type == None`. |
| **G5-X03** | DF Both Sides | Unidade / Componente | `direction == "ambiguous"`, `is_eligible == False`, `question_type == "transaction_direction"`. |
| **G5-X04** | DF Neither Side | Unidade / Componente | `direction == "unknown"`, `is_eligible == False`, `question_type == "transaction_direction"`. |
| **G5-X05** | Amount Zero | Unidade / Componente | `amount == 0`, `is_eligible == False`, `question_type == "transaction_amount"`. |
| **G5-X06** | Amount Missing | Unidade / Componente | `amount == None`, `is_eligible == False`, `question_type == "transaction_amount"`. |
| **G5-X07** | Document Date Used | Unidade / Componente | Data do documento utilizada, `date_source == "DOCUMENT"`, 00:00 `America/Sao_Paulo`. |
| **G5-X08** | Date Missing | Unidade / Componente | Fallback para `message_received_at`, `date_source == "MESSAGE_TIMESTAMP"`, instante UTC. |
| **G5-X09** | Budget Date Fallback | Unidade / Componente | Orçamento sem data utiliza `message_received_at`, `date_source == "MESSAGE_TIMESTAMP"`. |
| **G5-X10** | Complete Item | Unidade / Componente | Asserta `is_eligible == True` e `question_type == None`. O formatador da mensagem de sucesso é testado de forma independente sob simulação de evento pós-`COMMITTED`. |

---

## 22. Sequência de Implementação

1. Criar `apps/orchestrator/src/orchestrator/services/business_rules_evaluator.py`;
2. Atualizar `apps/orchestrator/src/orchestrator/config.py` para incluir a lista padrão `df_holding_identifiers`;
3. Criar `tests/test_platform_gate5_business_rules_unit.py` com cobertura completa da matriz G5-X01 a G5-X10;
4. Executar verificação estática (`compileall`, `ruff check`, `mypy`);
5. Executar suite de testes do Gate 5.

---

## 23. Comandos de Verificação Estática

```powershell
python -m compileall packages/db apps/orchestrator apps/db_writer tests
python -m ruff check packages/db/src/db/models.py apps/orchestrator/src/orchestrator/ apps/db_writer/src/db_writer/ tests/
python -m mypy apps/orchestrator/src/orchestrator/services/business_rules_evaluator.py
```

---

## 24. Comandos de Verificação de Testes

```powershell
python -m pytest -o "pythonpath=packages/security/src packages/observability/src packages/db/src apps/orchestrator/src packages/transcription/src apps/bot_df/src apps/db_writer/src" tests/test_platform_gate5_business_rules_unit.py -v
```

---

## 25. Decisão sobre Migrações

**ZERO NOVAS MIGRAÇÕES SÃO NECESSÁRIAS**. Todas as colunas físicas de banco necessárias já existem na tabela `processing_items` do schema aprovado.

---

## 26. Itens Fora do Escopo

- Modificação de `fifo_worker_service.py` (Gate 6);
- Transição de status em banco para `WAITING_USER_INPUT` (Gate 6);
- Disparo de requisições HTTP para WUZAPI (Gate 6);
- Execução de retries ou sweeps de expiração (Gate 6);
- Qualquer alteração nos modelos ou tabelas do Gate 4.

---

## 27. Expectativa de Rollback e Recuperação

Sendo um componente puro sem estado em banco de dados e sem efeitos colaterais de I/O, o `BusinessRulesEvaluatorService` é 100% determinístico e idempotente. Em caso de falha de execução, a função lança exceções sanitizadas que permitem ao chamador registrar a falha sem afetar a fila persistente.

---

## 28. Status Final e Aprovação

- Gate 4: **APPROVED / COMPLETE / FROZEN**.
- Gate 5 Planning: **APPROVED**.
- Gate 5 Implementation Plan: **APPROVED**.
- Gate 5 Implementation: **COMPLETE**.
- Gate 5 Verification: **PASSED**.
- Gate 5: **APPROVED**.
- `G5-APPROVED = true`.
- Gate 6: **NOT STARTED**.
- Gate 5 migrations: **NONE REQUIRED**.
- Persistent/staging/production/remote migration execution: **NOT AUTHORIZED**.

### Final Verification Evidence

- Gate 5 tests: **63 passed, 0 skipped, 0 failed, 0 errors**.
- Frozen Gate 4 regression: **210 passed, 0 skipped, 0 failed, 0 errors**.
- Complete project suite: **375 passed, 0 skipped, 0 failed, 0 errors**.
- Static verification: **compileall PASS; Ruff PASS; mypy PASS; git diff --check PASS**.
- PostgreSQL 15 disposable test environment used; no persistent, staging, production, or remote database touched.
- `tzdata` declared in `apps/orchestrator/pyproject.toml`; `uv.lock` updated; `ZoneInfo("America/Sao_Paulo")` reproducibility verified.
- `fifo_worker_service.py` unchanged.
- Zero WUZAPI integration.
- Zero Database Writer/PersistenceService integration.
- Zero Gate 5 migrations.
