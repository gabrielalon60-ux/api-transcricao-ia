# Plano de Implementação — Gate 6: Clarificação Interativa BOT DF

> **Status**: APPROVED / COMPLETE
> **Gate 4**: APPROVED / COMPLETE / FROZEN
> **Gate 5**: APPROVED / COMPLETE / PUSHED
> **G5-APPROVED**: true
> **Gate 6**: APPROVED
> **G6-APPROVED**: true
> **Gate 7**: NOT STARTED
> **Gate 8**: NOT STARTED
> **Migrations**: NONE REQUIRED / ZERO CREATED
> **Persistent/staging/production/remote migration execution**: NOT AUTHORIZED

---

## 1. Objetivo e Escopo Autoritativo

O Gate 6 integra o `BusinessRulesEvaluatorService` puro e aprovado no Gate 5 ao lifecycle durável de `UserInteraction` aprovado no Gate 4E. O resultado é o runtime de clarificação do BOT DF para os dois campos P0 definidos em `.agents/TASKS_TESTS_GATES.md`:

1. `transaction_direction`;
2. `transaction_amount`.

O Gate 6 deve:

- executar o avaliador Gate 5 após `ACTIVE -> VALIDATING`;
- persistir os fatos avaliados já suportados por `processing_items`;
- selecionar uma pergunta por vez usando `QUESTION_PRIORITY`;
- usar o lifecycle existente `RESERVED -> WAITING/OUTBOUND_OUTCOME_UNKNOWN -> ANSWERED/CANCELLED/EXPIRED`;
- aplicar respostas de forma idempotente;
- retomar itens em `VALIDATING` após resposta válida;
- reexecutar o avaliador, aplicar os overrides duráveis de respostas e perguntar o próximo campo ou entregar ao PersistenceService;
- manter FIFO bloqueado para a conversa em espera e livre para outras conversas;
- expirar a pendência após uma hora e liberar o próximo item.

O Gate 6 não cria um segundo protocolo de interação, não redesenha o Gate 4 e não altera as regras puras do Gate 5.

---

## 2. Dependências Congeladas

### 2.1 Gate 4 / Gate 4E

Permanecem autoritativos e inalterados:

- estados e constraint física de `ProcessingItem`;
- `claim_next_ready_item` e FIFO por `(organization_id, instance_id, user_id, sequence)`;
- `BLOCKING_STATES`, incluindo `WAITING_USER_INPUT`;
- partial unique index `uq_processing_items_one_active_per_conversation`;
- `UserInteraction.generation` e `outbound_message_id` estável;
- índice de uma interação aberta por item;
- checkpoints `USER_PROMPT_RESERVED`, `USER_PROMPT_DISPATCHED`, `USER_PROMPT_ACKNOWLEDGED`, `USER_PROMPT_OUTCOME_UNKNOWN`, `USER_ANSWER_APPLIED`, `USER_ANSWER_REJECTED`, `USER_INPUT_EXPIRED` e `USER_CANCELLED`;
- idempotência por `events.external_message_id` e `user_answers.inbound_event_id`;
- `apply_user_answer`, `/cancelar`, sweeper de expiração e recuperação stale;
- `PersistenceService`, `DBWriterClient` e os resultados `COMMITTED`, `REJECTED`, `RETRYABLE_FAILURE` e `OUTCOME_UNKNOWN`.

### 2.2 Gate 5

Permanecem autoritativos e inalterados:

- `BusinessRulesEvaluatorService`;
- `FinancialEvaluationResult`;
- `is_eligible_for_auto_write`;
- `direction`, `amount`, `transaction_date`, `document_date_str`, `date_source`, `question_type` e `clarification_reason`;
- `Decimal` para valores;
- `ZoneInfo("America/Sao_Paulo")`;
- prioridade `transaction_direction > transaction_amount > document_classification`;
- formatter puro de sucesso e proibição de usá-lo antes de `COMMITTED`.

`clarification_reason` continua somente em memória e não será persistido.

---

## 3. Ponto Exato de Integração no Worker

O ponto autoritativo é `apps/orchestrator/src/orchestrator/fifo_worker.py::run_fifo_worker_loop`, imediatamente após:

```text
claim_next_ready_item
-> transition_active_to_validating
-> item em VALIDATING com lease e claimed_by do worker
```

O bloco atual baseado apenas em `select_question_type(validating)` será substituído por uma chamada a uma nova função de coordenação em `fifo_worker_service.py`:

```python
evaluate_and_persist_validating_item(db, item_id, worker_id, evaluator)
```

Essa função:

1. bloqueia o item `VALIDATING` com `FOR UPDATE`;
2. valida ownership/lease do worker;
3. adapta `normalized_data` para os argumentos congelados do Gate 5;
4. executa `BusinessRulesEvaluatorService.evaluate(...)`;
5. aplica o merge autoritativo de fatos confirmado por `UserAnswer(status="APPLIED")`;
6. persiste somente os fatos financeiros efetivos nessa transação; a decisão `question_type` permanece em memória;
7. retorna uma decisão de runtime em memória: `eligible` ou `clarification_required`.

O avaliador continua puro e não recebe `Session`, não altera modelos e não chama I/O.

---

## 4. Adaptação de `normalized_data`

O adapter deve usar exclusivamente os schemas já aprovados no Gate 3:

| `document_type` | amount | document date | payer | receiver |
|---|---|---|---|---|
| `pix_receipt` | `amount` | `transaction_date` | `sender_cpf_cnpj` | `receiver_cpf_cnpj` |
| `bank_receipt` | `amount` | `payment_date` | `payer_cpf_cnpj` | `recipient_cpf_cnpj` |
| `invoice` | `total_amount` | `invoice_date` | `customer_cpf_cnpj` | `supplier_cpf_cnpj` |
| `commercial_document` | `total_amount` | `document_date` | `customer_cpf_cnpj` | `supplier_cpf_cnpj` |

Regras:

- valores ausentes permanecem `None`;
- valores presentes são convertidos para `Decimal` sem `float` intermediário;
- data textual somente é entregue ao Gate 5 como ISO quando presente; o próprio Gate 5 decide validade/fallback;
- `message_received_at` vem do `ProcessingItem` e conserva o instante inbound;
- CPF/CNPJ não é reinterpretado pelo adapter; a normalização permanece no Gate 5;
- aliases fora dos schemas acima não serão inventados.

---

## 5. Estado Efetivo e Overrides de Respostas

`BusinessRulesEvaluatorService.evaluate` não consome um objeto de runtime nem um `ProcessingItem`. Seu contrato real são cinco argumentos nomeados:

```python
evaluate(
    amount: Decimal | None,
    document_date: str | None,
    message_received_at: datetime,
    payer_identifier: str | None,
    receiver_identifier: str | None,
) -> FinancialEvaluationResult
```

O avaliador obtém amount exclusivamente do argumento `amount`. Ele não lê `normalized_data` nem `processing_items.amount`. Direction é sempre derivada dentro do avaliador pelos argumentos `payer_identifier`/`receiver_identifier` e pelos identificadores DF configurados; não existe argumento direction.

Uma resposta válida possui duas representações duráveis já existentes:

- auditoria/proveniência: `UserAnswer(status="APPLIED", parsing_result={"value": ...}, applied_at=...)`, ligado à `UserInteraction` respondida;
- valor materializado: `ProcessingItem.direction` ou `ProcessingItem.amount`, gravado atomicamente por `apply_user_answer` antes de o item retornar a `VALIDATING`.

Não existe coluna genérica de override. O mecanismo durável existente é a combinação `UserAnswer APPLIED` + campo materializado no `ProcessingItem`; nenhuma coluna nova será criada.

### 5.1 Precedência autoritativa

Para cada campo, a ordem é:

1. fato confirmado pelo usuário: última `UserAnswer APPLIED` daquele `question_type`, ordenada por `UserInteraction.generation` e `applied_at`, com o valor materializado no `ProcessingItem`;
2. fato financeiro válido já materializado no `ProcessingItem`;
3. fato da extração em `normalized_data`, conforme o adapter fechado da seção 4;
4. regra derivada payer/receiver do Gate 5, somente para direction.

Não há direction extraída direta no contrato de entrada Gate 5. Para amount, o coordenador escolhe, antes de chamar o avaliador, `user-confirmed/item.amount` e somente depois o amount de `normalized_data`. Para direction, o avaliador continua derivando o resultado bruto de payer/receiver e o coordenador aplica depois o override confirmado.

Se `UserAnswer.parsing_result` e o campo materializado divergirem, o runtime falha fechado e não despacha prompt nem Writer; não tenta adivinhar qual valor é correto.

### 5.2 Merge pós-avaliação

Após cada execução do Gate 5, a coordenação carrega respostas `APPLIED` ligadas a interações `ANSWERED` do mesmo item e calcula uma decisão efetiva, sem alterar o `FinancialEvaluationResult` bruto:

```text
effective_direction = direção user-confirmed
                      ou ProcessingItem.direction válida
                      ou raw_result.direction

evaluator_amount_input = amount user-confirmed/materializado
                         ou amount da extração

effective_amount = raw_result.amount
```

Não é criado um segundo avaliador. O Gate 5 continua avaliando os fatos extraídos; o Gate 6 apenas sobrepõe fatos explicitamente resolvidos pelo usuário e aplica a prioridade congelada:

1. se `effective_direction` não pertence a `{expense, income}`, perguntar `transaction_direction`;
2. senão, se `effective_amount` é `None` ou `<= 0`, perguntar `transaction_amount`;
3. senão, o item é elegível para o handoff Gate 4.

No cenário direction `payer=DF` e `receiver=DF`, a reexecução bruta continua retornando `ambiguous`, como exige o Gate 5 congelado; o merge Gate 6 retém `expense` da resposta `2/Despesa`, recalcula a decisão efetiva e remove a necessidade de nova pergunta direction. No cenário amount originalmente ausente, `Decimal("125.50")` já materializado é fornecido como argumento `amount` na reexecução, portanto o próprio Gate 5 retorna amount válido.

O Gate 5 permanece completamente congelado: nenhuma assinatura, regra, dataclass ou arquivo Gate 5 precisa ser modificado.

### 5.3 `EffectiveFinancialDecision`

Gate 6 cria em `fifo_worker_service.py` um value object privado e imutável, separado do resultado congelado Gate 5:

```python
@dataclass(frozen=True)
class EffectiveFinancialDecision:
    direction: str
    amount: Decimal | None
    transaction_date: datetime
    document_date_str: str | None
    date_source: str
    question_type: str | None
    clarification_reason: str | None
    is_eligible_for_auto_write: bool
```

Algoritmo determinístico:

1. executar Gate 5 e obter `raw_result`;
2. copiar sem alteração `transaction_date`, `document_date_str` e `date_source`;
3. validar a proveniência de cada `UserAnswer APPLIED` contra o campo materializado correspondente; divergência aborta a decisão, sem prompt e sem Writer;
4. calcular `effective_direction` pela precedência da seção 5.1;
5. calcular `effective_amount` a partir do amount selecionado como input do avaliador e validado/quantizado em `raw_result.amount`;
6. construir as pendências efetivas sem consultar `ProcessingItem.question_type`: direction está pendente fora de `{income, expense}`; amount está pendente quando ausente ou não positivo;
7. percorrer a `QUESTION_PRIORITY` congelada e selecionar a primeira pendência P0; `document_classification` permanece fora do Gate 6;
8. recomputar todos os metadados derivados:
   - sem pendência: `question_type=None`, `clarification_reason=None`, `is_eligible_for_auto_write=True`;
   - direction pendente: `question_type="transaction_direction"`, reason coerente `AMBIGUOUS_DIRECTION`/`UNKNOWN_DIRECTION` e ineligible;
   - amount pendente: `question_type="transaction_amount"`, reason coerente `MISSING_AMOUNT`/`INVALID_AMOUNT` e ineligible;
9. criar uma única `EffectiveFinancialDecision` internamente consistente.

`raw_result.question_type`, `raw_result.clarification_reason` e `raw_result.is_eligible_for_auto_write` nunca são copiados depois de um override; sempre são recomputados. Combinações obsoletas como `direction=expense` com `question_type=transaction_direction` não podem sobreviver.

```text
raw ambiguous + 100; answer expense
-> effective expense + 100 + question None + reason None + eligible

raw ambiguous + None; answer direction expense
-> effective expense + None + amount question + MISSING_AMOUNT + ineligible

raw rerun com amount input 125.50 + answer direction expense
-> effective expense + 125.50 + question None + reason None + eligible
```

---

## 6. Persistência dos Fatos Avaliados

O BOT DF/worker é o proprietário da materialização dos fatos Gate 5 em `ProcessingItem`.

Na transação de avaliação, antes da reserva de qualquer prompt, persistir:

| Resultado efetivo | Campo físico |
|---|---|
| `effective_amount` | `processing_items.amount` |
| `result.document_date_str` | `processing_items.document_date` |
| `result.transaction_date` | `processing_items.transaction_date` |
| `result.date_source` | `processing_items.date_source` |
| `effective_direction` | `processing_items.direction` |

`FinancialEvaluationResult.question_type` é somente a decisão em memória. `ProcessingItem.question_type` pertence ao lifecycle durável de interação. Embora a coluna nullable não tenha constraint que proíba preenchimento durante `VALIDATING`, não existe invariant/teste Gate 4E que autorize essa escrita antecipada. O contrato positivo existente é `dispatch_user_prompt`: somente na Boundary 4, ao materializar `WAITING` ou `OUTBOUND_OUTCOME_UNKNOWN`, ele grava atomicamente `item.status = WAITING_USER_INPUT`, `item.question_type`, `waiting_since` e `expires_at`.

Ordem obrigatória:

```text
lock VALIDATING
-> avaliar
-> aplicar overrides
-> persistir fatos financeiros; manter evaluator question_type em memória
-> commit
-> se houver pergunta, dispatch_user_prompt(question_type)
-> Boundary 4 materializa ProcessingItem.question_type junto com WAITING_USER_INPUT
-> se elegível, iniciar PERSISTING sem usar ProcessingItem.question_type como decisão atual
```

`apply_user_answer` atualmente preserva o último `ProcessingItem.question_type` ao retornar a `VALIDATING`. O coordenador ignora esse valor histórico: a próxima decisão vem exclusivamente da avaliação efetiva em memória; uma nova Boundary 4 o sobrescreve se houver outra pergunta. `clarification_reason` não é persistido. Nenhuma coluna nova é necessária.

---

## 7. Fluxo de Item Elegível

```text
READY
-> claim FIFO
-> ACTIVE
-> VALIDATING
-> BusinessRulesEvaluatorService
-> persistir fatos efetivos; decisão question_type permanece em memória
-> transition_validating_to_persisting
-> PERSISTING
-> claim_persistence_dispatch
-> Database Writer POST
-> resultado congelado Gate 4
```

Resultados:

- `COMMITTED -> COMPLETED`;
- `REJECTED -> PERSISTENCE_FAILED`;
- `RETRYABLE_FAILURE -> PERSIST_RETRYABLE` ou `PERSISTENCE_FAILED` após limite;
- `OUTCOME_UNKNOWN -> PERSIST_OUTCOME_UNKNOWN`, seguido de reconciliação Gate 4.

O Gate 6 não altera o payload, retries, idempotency key ou reconciliação do PersistenceService.

---

## 8. Fluxo de Clarificação

```text
READY
-> ACTIVE
-> VALIDATING
-> BusinessRulesEvaluatorService
-> persistir somente fatos financeiros efetivos
-> manter question_type da decisão em memória
-> dispatch_user_prompt(question_type)
   -> interação generation N / RESERVED
   -> USER_PROMPT_RESERVED commit
   -> USER_PROMPT_DISPATCHED commit
   -> chamada WUZAPI fora da transação
   -> WAITING ou OUTBOUND_OUTCOME_UNKNOWN
-> Boundary 4 grava item WAITING_USER_INPUT + question_type + waiting_since + expires_at
-> release do claim/lease de negócio
```

Somente uma interação aberta existe por item. O partial index de `ProcessingItem` garante no máximo um item bloqueante por conversa.

---

## 9. Contrato `transaction_direction`

### Prompt exato

```text
Este lançamento é uma entrada ou uma despesa?

1 - Entrada
2 - Despesa

Responda com 1 ou 2.
```

### Respostas já suportadas pelo parser congelado

Mapeiam para `income`:

```text
1
entrada
receita
income
credito
crédito
```

Mapeiam para `expense`:

```text
2
saida
saída
despesa
expense
debito
débito
```

A normalização é `strip().lower()`. Outros valores retornam `INVALID_DIRECTION_CHOICE`.

Resposta inválida:

- cria `UserAnswer(status="REJECTED")` idempotente por evento;
- mantém item em `WAITING_USER_INPUT`;
- mantém a mesma interação aberta;
- não cria nova geração;
- não reenvia cegamente o prompt;
- não renova `waiting_since` ou `expires_at`;
- usa a resposta de erro já aprovada: `⚠️ Resposta não compreendida. Por favor, tente novamente.`

Não existe limite autoritativo de tentativas inválidas. Tentativas distintas podem continuar até resposta válida, `/cancelar` ou TTL.

---

## 10. Contrato `transaction_amount`

### Prompt exato do PRD RN-014

```text
Qual é o valor deste lançamento?
```

### Gramática aceita

Preservar os formatos já cobertos pelo Gate 4E e adicionar o formato P0 G6-X04:

- inteiro positivo: `150`;
- decimal com ponto: `150.50`;
- decimal com vírgula: `150,50`;
- prefixo opcional `R$`: `R$ 150,50`, `R$1234.56`;
- agrupamento brasileiro com vírgula decimal: `1.200,50`;
- agrupamentos brasileiros adicionais válidos quando acompanhados por vírgula decimal: `1.234.567,89`.

Normalização:

1. remover espaços externos e prefixo opcional `R$`;
2. rejeitar qualquer sinal negativo;
3. quando houver ponto e vírgula, validar pontos como agrupadores de milhar, removê-los e usar a vírgula como decimal;
4. quando houver somente vírgula, usá-la como separador decimal;
5. quando houver somente ponto, preservar ponto decimal com uma ou duas casas; `1.234` é rejeitado por ambiguidade/mais de duas casas e não é reinterpretado como milhar;
6. construir `Decimal` diretamente;
7. exigir `> 0`;
8. quantizar para `Decimal("0.01")`.

Zero, negativo, texto misto, agrupamento inválido e mais de duas casas decimais retornam `INVALID_AMOUNT_FORMAT`. O parser usa full match, não converte por `float` e não aceita resultado parcial.

### Matriz exata do parser

O parser Gate 4E atual usa `re.search` e aceita prefixos parciais. A coluna "atual" registra fielmente esse comportamento; a coluna "Gate 6" é a extensão estrita mínima exigida por G6-T05/G6-X04. Casos que nunca foram aprovados por teste/PRD e só passavam por truncamento parcial tornam-se rejeitados.

| Entrada | Parser atual | Contrato Gate 6 |
|---|---|---|
| `1` | ACCEPTED -> `Decimal("1.00")` | ACCEPTED -> `Decimal("1.00")` |
| `1,2` | ACCEPTED -> `Decimal("1.20")` | ACCEPTED -> `Decimal("1.20")` |
| `1,20` | ACCEPTED -> `Decimal("1.20")` | ACCEPTED -> `Decimal("1.20")` |
| `1.20` | ACCEPTED -> `Decimal("1.20")` | ACCEPTED -> `Decimal("1.20")` |
| `1.234` | ACCEPTED parcialmente -> `Decimal("1.23")` | REJECTED |
| `1,234` | ACCEPTED parcialmente -> `Decimal("1.23")` | REJECTED |
| `1.234,56` | ACCEPTED incorretamente -> `Decimal("1.23")` | ACCEPTED -> `Decimal("1234.56")` |
| `R$ 1.234,56` | ACCEPTED incorretamente -> `Decimal("1.23")` | ACCEPTED -> `Decimal("1234.56")` |
| `R$125,50` | ACCEPTED -> `Decimal("125.50")` | ACCEPTED -> `Decimal("125.50")` |
| `0` | REJECTED | REJECTED |
| `-10` | REJECTED | REJECTED |
| `abc` | REJECTED | REJECTED |
| `12abc` | ACCEPTED parcialmente -> `Decimal("12.00")` | REJECTED |
| `1,2345` | ACCEPTED parcialmente -> `Decimal("1.23")` | REJECTED |

Resposta válida é persistida em `processing_items.amount` pelo `apply_user_answer` antes do commit que retorna o item a `VALIDATING`.

Resposta inválida segue o mesmo lifecycle descrito para direction: mesma interação, mesmo TTL, zero Writer POST e nenhuma nova geração.

---

## 11. `document_classification` no Gate 6

Decisão de escopo: **fora do Gate 6 P0**.

Fundamentos:

- G6-T01..T11 incluem somente direction, amount, parse, TTL, FIFO e continuação;
- G6-X01..X10 não possuem cenário de classificação;
- o Gate 3 já produz um dos quatro `document_type` suportados;
- `validate_structural_readiness` impede `unknown` de entrar em `READY`;
- o Gate 5 não retorna `document_classification` em seu fluxo atual.

O vocabulário, parser e constraints existentes permanecem congelados por compatibilidade. Gate 6 não gera prompt novo de `document_classification` e não remove suporte existente.

---

## 12. Reavaliação e Retomada após Resposta

`apply_user_answer` preserva o contrato aprovado:

```text
resposta válida
-> persistir UserAnswer APPLIED
-> persistir o campo normalizado no ProcessingItem
-> interação ANSWERED / resolved_at
-> item VALIDATING
-> limpar waiting_since/expires_at
-> commit
```

Estado durável exato após esse commit:

| Campo | Valor |
|---|---|
| `status` | `VALIDATING` |
| `claimed_by` | `NULL` |
| `lease_expires_at` | `NULL` |
| `heartbeat_at` | `NULL` |

`apply_user_answer` não limpa ownership diretamente; esses três campos já foram limpos pela Boundary 4 de `dispatch_user_prompt` ao entrar em `WAITING_USER_INPUT` e permanecem nulos durante a resposta.

O runtime atual não descobre esse item: `claim_next_ready_item` filtra exclusivamente `status == READY`; o loop não consulta `VALIDATING` sem lease; `startup_recover_claims` aceita somente leases ainda válidos; e `recover_stale_validating_items` exige lease não nula e expirada. O caminho legal Gate 6 é:

```text
WAITING_USER_INPUT
-> resposta APPLIED
-> VALIDATING sem ownership
-> dedicated atomic resume claim
-> VALIDATING com ownership/lease
```

Não existe transição intermediária para `READY`, `ACTIVE` nem novo incremento de `attempt_count`.

Como `claim_next_ready_item` só seleciona `READY`, o Gate 6 adicionará em `fifo_worker_service.py` um claim específico e idempotente para retomada:

```python
claim_next_resumable_validating_item(db, worker_id)
```

Elegibilidade do claim:

- `status == VALIDATING`;
- `claimed_by IS NULL`, `lease_expires_at IS NULL` e `heartbeat_at IS NULL`;
- nenhuma interação aberta `WAITING`/`OUTBOUND_OUTCOME_UNKNOWN`;
- ou existe `UserInteraction(status="ANSWERED")` com `UserAnswer(status="APPLIED")`, ou existe `RESERVED` recuperável sem `USER_PROMPT_DISPATCHED` correspondente ao seu `outbound_message_id`;
- não existe item de sequence menor, na mesma conversa, em estado não terminal;
- ordena por `message_received_at`, tenant/conversa e `sequence`, como o claim normal;
- executa `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`;
- ainda na mesma transação, revalida os predicados e grava `claimed_by=worker-id`, `heartbeat_at=now()` e `lease_expires_at=now()+INTERVAL '60 seconds'`;
- mantém `status=VALIDATING`, `sequence` e `attempt_count` inalterados; faz commit antes de retornar.

Forma SQL equivalente:

```sql
SELECT pi.*
FROM processing_items AS pi
WHERE pi.status = 'VALIDATING'
  AND pi.claimed_by IS NULL
  AND pi.lease_expires_at IS NULL
  AND pi.heartbeat_at IS NULL
  AND NOT EXISTS (/* open WAITING or OUTBOUND_OUTCOME_UNKNOWN interaction */)
  AND (EXISTS (/* ANSWERED interaction joined to APPLIED answer */)
       OR EXISTS (/* recoverable RESERVED without matching DISPATCHED */))
  AND NOT EXISTS (/* lower non-terminal sequence in same conversation */)
ORDER BY pi.message_received_at, pi.organization_id, pi.instance_id,
         pi.user_id, pi.sequence
FOR UPDATE SKIP LOCKED
LIMIT 1;

UPDATE processing_items
SET claimed_by = :worker_id,
    heartbeat_at = now(),
    lease_expires_at = now() + INTERVAL '60 seconds'
WHERE id = :locked_id
  AND status = 'VALIDATING'
  AND claimed_by IS NULL;
```

O row lock mais o update guard tornam impossível dois workers adquirirem o mesmo item. O vencedor adiciona o id ao `WorkerClaimTracker`; `renew_heartbeat` já suporta `VALIDATING` com ownership e lease válidos.

O loop processa esse claim antes de tentar um novo `READY`. O item permanece em `BLOCKING_STATES` com a mesma sequence, e o partial unique index continua garantindo um único bloqueante por conversa. Itens posteriores da conversa não podem ultrapassá-lo; conversas independentes continuam elegíveis.

Depois do claim:

```text
reexecutar Gate 5
-> aplicar overrides APPLIED
-> persistir fatos efetivos
-> próxima pergunta, se houver
-> ou PersistenceService automaticamente
```

Cada próxima pergunta usa nova `UserInteraction.generation`; a anterior permanece `ANSWERED`.

### 12.1 Recuperação stale do resume

O recovery Gate 4 atual correlaciona checkpoints de prompt pelo item inteiro. Depois da primeira resposta, um ACK de geração antiga não pode fazer um resume stale voltar para uma pergunta já respondida. Gate 6 estende `recover_stale_validating_items` sem mudar os resultados Gate 4 existentes:

1. correlacionar cada decisão de prompt ao `outbound_message_id` da geração aberta; checkpoints de gerações `ANSWERED` são apenas históricos;
2. se o lease do resume expirar, não houver interação aberta e existir `ANSWERED + APPLIED`, manter `status=VALIDATING`, limpar ownership/heartbeat/lease e registrar recovery; o dedicated resume claim torna o item elegível novamente;
3. se existir `RESERVED` atual sem `DISPATCHED` correspondente, preservar a geração e limpar o lease para o mesmo claim continuar o dispatch;
4. `DISPATCHED`, `ACKNOWLEDGED` e outcome unknown governam recovery somente quando pertencem ao `outbound_message_id` da geração aberta;
5. os demais casos legados continuam seguindo a matriz Gate 4.

Crash imediatamente após o resume claim produz lease expirável, detecção pelo sweeper e nova elegibilidade atômica. Nunca existe `VALIDATING` sem ownership que o worker não possa descobrir. Esta é integração Gate 6 generation-aware, não reabertura do comportamento Gate 4 para itens sem respostas humanas.

### 12.2 State machine exata: direction

```text
VALIDATING
-> evaluator bruto: direction=ambiguous, question_type=transaction_direction
-> persistir fatos financeiros (não ProcessingItem.question_type)
-> dispatch_user_prompt(transaction_direction)
-> RESERVED -> DISPATCHED -> WAITING/OUTBOUND_OUTCOME_UNKNOWN
-> item WAITING_USER_INPUT; Boundary 4 grava question_type
-> resposta válida "2"/"Despesa"
-> UserAnswer APPLIED + ProcessingItem.direction=expense
-> interação ANSWERED; item VALIDATING, sem waiting timestamps/lease
-> claim_next_resumable_validating_item atribui lease, sem mudar o estado
-> evaluator bruto reexecuta e ainda deriva ambiguous dos mesmos identificadores
-> merge encontra APPLIED direction e produz effective_direction=expense
-> se amount falta: nova pergunta transaction_amount
-> senão: PersistenceService
```

### 12.3 State machine exata: amount

```text
VALIDATING
-> evaluator bruto: question_type=transaction_amount
-> dispatch lifecycle -> WAITING_USER_INPUT
-> resposta válida "R$ 125,50"
-> UserAnswer APPLIED + ProcessingItem.amount=Decimal("125.50")
-> interação ANSWERED; item VALIDATING
-> claim resumível atribui lease
-> coordenador fornece ProcessingItem.amount como argumento amount ao evaluator
-> evaluator bruto retorna amount=Decimal("125.50") válido
-> merge preserva qualquer direction confirmada
-> próxima clarificação ou PersistenceService
```

### 12.4 State machine exata: direction e amount ausentes

```text
VALIDATING -> raw direction unresolved + amount None
-> prioridade escolhe transaction_direction
-> resposta direction APPLIED -> VALIDATING -> claim -> reavaliação/merge
-> effective_direction válida + amount None
-> escolhe transaction_amount; cria generation N+1 somente após N estar ANSWERED
-> resposta amount APPLIED -> VALIDATING -> claim -> reavaliação/merge
-> direction válida + amount válido
-> elegível -> PersistenceService
```

O coordenador nunca usa o `ProcessingItem.question_type` histórico para escolher a próxima pergunta. Isso impede loop no mesmo campo. O partial unique index de uma interação aberta por item, `create_or_get_open_interaction`, checkpoints idempotentes e o fechamento `ANSWERED` antes da nova geração impedem duplicatas.

### 12.5 Prova end-to-end: direction + amount

Entrada: payer DF, receiver DF, amount ausente e document date válida.

| Passo | Estado durável / decisão em memória |
|---|---|
| 1 | Raw Gate 5: `direction=ambiguous`, `amount=None`, data DOCUMENT válida, `question_type=transaction_direction`, reason `AMBIGUOUS_DIRECTION`, ineligible. |
| 2 | Effective decision idêntica: direction continua pendente e tem prioridade sobre amount. |
| 3 | Primeira pergunta: `transaction_direction`. |
| 4 | `RESERVED -> USER_PROMPT_DISPATCHED -> WAITING/OUTBOUND_OUTCOME_UNKNOWN`; Boundary 4: item `WAITING_USER_INPUT`, question direction, TTL 1h, lease liberado. |
| 5 | Usuário responde `2`. |
| 6 | `UserAnswer APPLIED`, parsing result `expense`; interaction generation 1 `ANSWERED`. |
| 7 | Item: `direction=expense`, `amount=NULL`, `status=VALIDATING`, ownership/lease/heartbeat nulos; data materializada permanece válida. |
| 8 | Dedicated resume claim bloqueia a linha, revalida provenance/FIFO e grava worker + lease 60s; status, sequence e attempt permanecem. |
| 9 | Segundo raw Gate 5: recebe amount `None`, deriva direction `ambiguous` novamente e pede direction. |
| 10 | Merge: APPLIED direction prevalece; effective `direction=expense`, `amount=None`, `question_type=transaction_amount`, reason `MISSING_AMOUNT`, ineligible. |
| 11 | Segunda pergunta: `transaction_amount`; generation 2 somente após generation 1 fechada. |
| 12 | Após o lifecycle do segundo prompt, usuário responde `R$ 125,50`. |
| 13 | Segundo `UserAnswer APPLIED`, parsing result `125.50`; interaction generation 2 `ANSWERED`. |
| 14 | Item: `direction=expense`, `amount=Decimal("125.50")`, `status=VALIDATING`, ownership/lease/heartbeat nulos. |
| 15 | Dedicated resume claim readquire atomicamente o mesmo item/sequence com novo lease. |
| 16 | Terceiro raw Gate 5 recebe amount `125.50`, valida/quantiza-o e ainda deriva direction `ambiguous`. |
| 17 | Merge final: `direction=expense`, `amount=125.50`, data preservada, `question_type=None`, `clarification_reason=None`, eligible. |
| 18 | `is_eligible_for_auto_write=True`; nenhuma pergunta adicional. |
| 19 | Handoff ao `PersistenceService`, que executa os resultados congelados Gate 4. Gate 6 não envia mensagem de sucesso. |
| 20 | O item conserva a menor sequence bloqueante até o handoff; itens posteriores da mesma conversa nunca são claimáveis, enquanto outras conversas continuam. |

Direction não é repetida porque a decisão efetiva ignora o question type bruto/histórico depois do override. Amount não é repetido porque o valor materializado vira input da terceira avaliação. As generations 1 e 2 não coexistem abertas, e todo estado `VALIDATING` sem ownership descrito acima é descoberto pelo dedicated resume claim.

---

## 13. Lifecycle `WAITING_USER_INPUT` e TTL

TTL autoritativo:

```text
WAITING_USER_INPUT_TTL_SECONDS = 3600
```

O relógio começa na Boundary 4 de `dispatch_user_prompt`, após a tentativa outbound, quando o resultado é materializado como `WAITING` ou `OUTBOUND_OUTCOME_UNKNOWN`:

```text
waiting_since = now_post
expires_at = now_post + 3600s
```

Os mesmos valores são gravados na interação e no item.

Regras:

- resposta inválida não renova TTL;
- nova pergunta após resposta válida cria nova geração e novo TTL a partir do novo outbound;
- `/cancelar` encerra item e interação como `CANCELLED`;
- o sweeper usa `FOR UPDATE SKIP LOCKED` e expira somente `expires_at < NOW()`;
- `WAITING_USER_INPUT -> EXPIRED`;
- interação aberta -> `EXPIRED`, com `resolved_at`;
- limpar timestamps/claims do item;
- gravar `USER_INPUT_EXPIRED` uma vez;
- item expirado não é gravado e exige reenvio de novo documento;
- `EXPIRED` é terminal e libera o próximo sequence.

Corrida resposta vs. expiração é serializada pelo lock do item: resposta primeiro produz `VALIDATING`; expiração primeiro produz resposta `LATE`/sem waiting item.

---

## 14. Garantias FIFO

Enquanto um item está `WAITING_USER_INPUT`:

- ele pertence a `BLOCKING_STATES`;
- o partial index impede segundo item bloqueante na mesma conversa;
- itens posteriores podem concluir extração e ficar `READY`;
- `claim_next_ready_item` rejeita os posteriores por blocking item e por earlier non-terminal;
- outras conversas continuam elegíveis e são ordenadas normalmente;
- respostas são roteadas pela identidade exata `(organization_id, instance_id, user_id)`;
- a interação aberta pertence ao item waiting encontrado sob lock;
- replay do mesmo inbound event retorna o mesmo `UserAnswer` sem reaplicar;
- a interaction generation e os checkpoints impedem prompt duplicado;
- `ANSWERED`, `CANCELLED` ou `EXPIRED` fecha a interação e permite continuação ou desbloqueio conforme o estado do item.

Novo arquivo durante espera segue o ingestion/extraction existente e chega a `READY`; ele não substitui nem responde à pergunta atual.

---

## 15. Outbound e `OUTBOUND_OUTCOME_UNKNOWN`

O runtime deve passar sempre um sender real a `dispatch_user_prompt`; o default de teste `prompt_sender_func=None` nunca pode representar sucesso em produção.

Adapter de produção:

- resolve `User.phone_number` da conversa;
- formata o texto pelo `question_type`;
- usa `WuzapiClient.send_text_message` por uma ponte síncrona explícita para o worker;
- preserva `outbound_message_id = msg_{item_id}_{generation}_{question_type}` nos checkpoints locais;
- realiza I/O somente depois de `USER_PROMPT_DISPATCHED` commitado.

O endpoint atual do WUZAPI não aceita idempotency key nem oferece consulta de status por `outbound_message_id`. Portanto, após `USER_PROMPT_DISPATCHED`, qualquer timeout/queda de conexão/resultado não confirmável é conservadoramente:

```text
interaction = OUTBOUND_OUTCOME_UNKNOWN
item = WAITING_USER_INPUT
```

Regra congelada: **não reenviar cegamente**.

Resolução no Gate 6:

- resposta do usuário prova entrega suficiente e fecha a interação como `ANSWERED`;
- `/cancelar` fecha como `CANCELLED`;
- ausência de resposta termina por TTL em `EXPIRED`;
- uma chamada repetida a `dispatch_user_prompt` reutiliza a interação/checkpoints e executa zero novo WUZAPI send.

Não será inventado polling/callback de status WUZAPI inexistente.

Esse contrato já é aceito por `apply_user_answer`: a busca da interação aberta inclui explicitamente `status.in_(["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"])`, enquanto o item deve estar em `WAITING_USER_INPUT`. Assim, uma resposta inbound pode resolver legalmente `OUTBOUND_OUTCOME_UNKNOWN`, aplicando o fato e fechando a interação como `ANSWERED`. Gate 6 deve adicionar um teste PostgreSQL específico desse caminho; nenhuma extensão de código é necessária para permiti-lo.

`WuzapiClient.send_text_message(phone, text)` já aceita texto arbitrário. A ponte síncrona pertence ao chamador no `fifo_worker.py`: resolve o telefone, formata o prompt, executa o método async existente e traduz conclusão confirmada em `True` e exceção/resultado não confirmável em `False`. O lifecycle existente já transforma `True` em `WAITING/ACKNOWLEDGED` e `False` em `OUTBOUND_OUTCOME_UNKNOWN`. Nenhum requisito Gate 6 exige modificar `wuzapi.py`.

---

## 16. Fronteira da Mensagem Final de Sucesso

O formatter Gate 5 permanece puro. Elegibilidade não autoriza mensagem final.

Contrato:

- somente um `COMMITTED` durável pode autorizar `format_success_message`;
- `REJECTED`, `RETRYABLE_FAILURE` e `PERSIST_OUTCOME_UNKNOWN` nunca autorizam sucesso;
- `UserInteraction` não será reutilizado para sucesso, pois seu schema representa perguntas e exige `question_type` fechado;
- o PRD atribui outbound ao Orchestrator;
- a integração completa da mensagem final pertence ao Gate 8 (`E2E` e mensagens finais), não às tasks P0 do Gate 6.

Assim, Gate 6 termina seu escopo ao entregar ao PersistenceService e preservar a fronteira pós-`COMMITTED`; não envia mensagem final de sucesso.

---

## 17. Matriz de Falhas

| Evento | Estado/efeito obrigatório |
|---|---|
| Resposta direction inválida | `UserAnswer.REJECTED`; item/interação continuam waiting; TTL inalterado; zero Writer POST |
| Resposta amount inválida/zero/negativa | mesmo comportamento, `INVALID_AMOUNT_FORMAT` |
| Repetidas respostas inválidas | uma linha por evento distinto; replay idempotente; sem nova geração; até TTL/cancelamento |
| Expiração | item e interação `EXPIRED`; zero Writer POST; FIFO liberado |
| `/cancelar` | item e interação `CANCELLED`; zero Writer POST; FIFO liberado |
| Falha local antes de `USER_PROMPT_DISPATCHED` | zero WUZAPI; não fingir ACK; manter recuperação segura em `VALIDATING/RESERVED` |
| Falha/timeout após `USER_PROMPT_DISPATCHED` | `OUTBOUND_OUTCOME_UNKNOWN`; waiting até resposta/cancelamento/TTL; sem blind resend |
| Item cancelado durante callback tardio | callback não reabre item/interação; resposta posterior é late |
| Writer `REJECTED` | Gate 4: `PERSISTENCE_FAILED`; sem mensagem de sucesso |
| Writer `RETRYABLE_FAILURE` | Gate 4: `PERSIST_RETRYABLE` com backoff/limite; sem mensagem de sucesso |
| Writer `OUTCOME_UNKNOWN` | Gate 4: `PERSIST_OUTCOME_UNKNOWN` e GET de reconciliação; sem novo POST cego; sem mensagem de sucesso |
| Writer `COMMITTED` | Gate 4: `COMPLETED`; sucesso apenas elegível para futura integração Gate 8 |

---

## 18. Mapeamento G6-T01 a G6-T11

| Task | Implementação planejada |
|---|---|
| G6-T01 WAITING_USER_INPUT | resultado não elegível -> persist facts -> lifecycle `dispatch_user_prompt` |
| G6-T02 Pergunta direction | prompt e parser congelado `transaction_direction` |
| G6-T03 Pergunta amount | prompt RN-014 e parser `transaction_amount` |
| G6-T04 Parse 1/2 | `1 -> income`, `2 -> expense` |
| G6-T05 Parse valor pt-BR | extensão estrita para `1.200,50` e agrupamentos válidos |
| G6-T06 Uma pergunta por conversa | priority + partial indexes + uma interação aberta |
| G6-T07 TTL 1h | `3600s` desde Boundary 4 outbound |
| G6-T08 EXPIRED | sweeper congelado, zero persistência financeira |
| G6-T09 Novo arquivo durante espera | novo item pode chegar a READY, mas não pode ser claimed |
| G6-T10 Continuar após resolução | claim de `VALIDATING`, reavaliação, próxima pergunta ou Writer |
| G6-T11 Continuar após expiração | `EXPIRED` terminal libera próximo READY |

---

## 19. Matriz de Aceitação G6-X01 a G6-X10

### G6-X01 — terceiro de cinco pergunta; #4/#5 aguardam

- Estado inicial DB: cinco itens mesma conversa, sequences 1..5; #1/#2 terminais; #3 READY; #4/#5 READY.
- ProcessingItem: #3 `READY -> ACTIVE -> VALIDATING -> WAITING_USER_INPUT`; #4/#5 permanecem READY.
- UserInteraction: generation 1, `transaction_direction` ou `transaction_amount`, termina `WAITING`.
- Evaluator: #3 não elegível e retorna a pergunta de maior prioridade.
- Prompt: texto exato do question type.
- Resposta: ausente durante a asserção de bloqueio.
- Normalização: N/A.
- Próximo estado do item: #3 waiting; #4/#5 READY não claimed.
- Próximo estado da interação: `WAITING`.
- Writer POST: NÃO.
- FIFO: mesma conversa bloqueada; conversa distinta continua.
- WUZAPI: exatamente um send com outbound identity estável.
- Expiry: ainda não expira.
- Efeitos proibidos: nenhum claim de #4/#5, nenhuma segunda interação, nenhum Writer POST.

### G6-X02 — `1` resolve direction

- Estado inicial DB: item `WAITING_USER_INPUT`; interaction `WAITING/transaction_direction`.
- ProcessingItem: direction extraída `ambiguous`/`unknown`.
- Evaluator: originalmente não elegível.
- Prompt: direction exato.
- Resposta: `1`.
- Normalização: `income`.
- Próximo estado do item: `VALIDATING`, depois reavaliado; próxima pergunta ou `PERSISTING`.
- Próximo estado da interação: `ANSWERED`, `resolved_at` preenchido.
- Writer POST: SIM somente se amount já for válido após reavaliação; caso contrário NÃO.
- FIFO: continua bloqueado pelo mesmo item até terminal/espera seguinte.
- WUZAPI: confirmação de resposta existente; nenhum resend do prompt anterior.
- Expiry: timestamps anteriores limpos; novo TTL apenas se houver nova pergunta.
- Efeitos proibidos: não mapear `1` para expense; não pular amount faltante.

### G6-X03 — `2` resolve direction

- Mesmo estado inicial de G6-X02.
- Resposta: `2`.
- Normalização: `expense`.
- Estados, Writer/FIFO/WUZAPI/expiry: iguais a G6-X02.
- Efeitos proibidos: não mapear `2` para income; não persistir antes de resposta APPLIED.

### G6-X04 — `1.200,50` normaliza corretamente

- Estado inicial DB: item waiting com interaction `transaction_amount`; direction válida.
- Evaluator: não elegível por amount ausente/inválido.
- Prompt: `Qual é o valor deste lançamento?`.
- Resposta: `1.200,50`.
- Normalização: `Decimal("1200.50")`.
- Próximo estado do item: `VALIDATING -> PERSISTING` quando nenhum outro campo falta.
- Próximo estado da interação: `ANSWERED`.
- Writer POST: SIM após reavaliação elegível, payload amount `1200.50`.
- FIFO: continua bloqueado até resultado Writer.
- WUZAPI: sem novo prompt se elegível.
- Expiry: waiting timestamps limpos.
- Efeitos proibidos: não normalizar como `1.20`; não usar float; não enviar Writer antes do commit APPLIED.

### G6-X05 — resposta inválida mantém pergunta

- Estado inicial DB: item waiting; interaction waiting/aberta.
- Evaluator: pergunta atual não resolvida.
- Prompt: prompt original já enviado.
- Resposta: valor fora da gramática (`3` para direction ou `abc/0/-1` para amount).
- Normalização: `None` + error code específico.
- Próximo estado do item: permanece `WAITING_USER_INPUT`.
- Próximo estado da interação: permanece `WAITING` ou `OUTBOUND_OUTCOME_UNKNOWN`.
- Writer POST: NÃO.
- FIFO: permanece bloqueado.
- WUZAPI: somente mensagem de erro aprovada; não recriar/reemitir prompt.
- Expiry: `expires_at` original inalterado.
- Efeitos proibidos: nenhuma nova generation; nenhum TTL reset; nenhum fato inválido aplicado.

### G6-X06 — novo arquivo durante pendência vira READY

- Estado inicial DB: item #N waiting e interação aberta.
- ProcessingItem novo: ingestion/extraction existente -> READY com sequence posterior.
- Evaluator do waiting: não é reexecutado pelo novo arquivo.
- Prompt/resposta/normalização: interação atual inalterada.
- Próximo estado: novo item permanece READY.
- UserInteraction: nenhuma nova para o novo item.
- Writer POST: NÃO para o novo item.
- FIFO: conversa bloqueada pelo item anterior; outras conversas livres.
- WUZAPI: zero prompt relativo ao novo item.
- Expiry: TTL do item anterior inalterado.
- Efeitos proibidos: novo arquivo não substitui pergunta, não vira resposta e não pula sequence.

### G6-X07 — expira em 1h

- Estado inicial DB: item/interação waiting com `expires_at = waiting_since + 3600s`.
- Evaluator: não elegível; prompt já processado.
- Resposta: nenhuma.
- Normalização: N/A.
- Próximo estado antes do limite: waiting; após `expires_at < NOW()`: `EXPIRED`.
- Próximo estado da interação: `EXPIRED` com `resolved_at`.
- Writer POST: NÃO.
- FIFO: bloqueado antes, liberado depois.
- WUZAPI: nenhum resend.
- Expiry: checkpoint `USER_INPUT_EXPIRED` único.
- Efeitos proibidos: não expirar em exatamente menos de 3600s; não gravar item.

### G6-X08 — expiração libera próximo

- Estado inicial DB: item #1 expired candidate, item #2 READY mesma conversa.
- ProcessingItem: sweeper faz #1 `WAITING_USER_INPUT -> EXPIRED`; worker pode claimar #2.
- UserInteraction: #1 `EXPIRED`; #2 nenhuma até sua avaliação.
- Evaluator/prompt: #2 só depois do commit de expiração.
- Resposta/normalização: nenhuma para #1.
- Writer POST: NÃO para #1; condicional para #2 após sua própria avaliação.
- FIFO: próximo sequence liberado; ordem preservada.
- WUZAPI: zero resend para #1.
- Expiry: timestamps de #1 limpos no item.
- Efeitos proibidos: não reviver #1; não claimar #2 antes do terminal commit.

### G6-X09 — item expirado exige reenvio

- Estado inicial DB: item `EXPIRED`, interação `EXPIRED`.
- ProcessingItem: permanece terminal; resposta tardia não o reabre.
- UserInteraction: permanece fechada.
- Evaluator/prompt: não executados para o item expirado.
- Resposta: tardia ou novo documento.
- Normalização: resposta tardia = `LATE`; novo documento segue novo item/sequence.
- Próximo estado: expirado inalterado; novo arquivo cria novo processamento.
- Writer POST: NÃO para expirado.
- FIFO: expirado não bloqueia.
- WUZAPI: nenhuma retomada automática do prompt antigo.
- Expiry: já consumada.
- Efeitos proibidos: não reutilizar item, interaction generation ou writer key antigos.

### G6-X10 — máximo um WAITING por conversa

- Estado inicial DB: corrida entre workers/itens da mesma conversa.
- ProcessingItem: no máximo um estado bloqueante permitido pelo partial index.
- UserInteraction: no máximo uma aberta por item; apenas o item FIFO elegível recebe interação.
- Evaluator: pode executar sob locks, mas somente o vencedor materializa o lifecycle.
- Prompt: exatamente um dispatch owner/checkpoint/outbound call.
- Resposta/normalização: direcionadas ao único item waiting da conversa.
- Próximo estado: um waiting; demais READY ou inalterados.
- Writer POST: NÃO enquanto a pergunta estiver aberta.
- FIFO: conversa bloqueada uma vez, sem bloquear outras conversas.
- WUZAPI: exatamente uma chamada para a generation vencedora.
- Expiry: um único TTL correspondente.
- Efeitos proibidos: duas interações abertas, dois prompts, cross-conversation answer ou dupla aplicação.

---

## 20. Arquivos Exatos Propostos

### Modificar

- `apps/orchestrator/src/orchestrator/services/fifo_worker_service.py`
  - adapter de inputs Gate 3;
  - coordenação Gate 5 + overrides;
  - persistência atômica dos fatos;
  - claim de retomada `VALIDATING`.
- `apps/orchestrator/src/orchestrator/fifo_worker.py`
  - chamar Gate 5 no ponto exato;
  - processar retomadas;
  - escolher prompt vs PersistenceService;
  - sempre fornecer sender real em produção.
- `apps/orchestrator/src/orchestrator/services/user_interaction_service.py`
  - formatter fechado dos dois prompts P0;
  - extensão estrita de `parse_amount_answer` para PT-BR com milhar;
  - preservar lifecycle/idempotência existentes.
- `apps/orchestrator/src/orchestrator/services/stale_recovery_service.py`
  - tornar recovery de `VALIDATING` sensível à generation/outbound identity;
  - recuperar lease expirado após resposta sem reabrir pergunta respondida;
  - preservar integralmente a matriz legada quando não existe answer/resume Gate 6.

### Não modificar

- `business_rules_evaluator.py`;
- `wuzapi.py`;
- `persistence_service.py`;
- `db_writer`;
- modelos e migrations;
- Gate 4 tests congelados;
- dependências e runtime configuration.

---

## 21. Testes Exatos Propostos

### Novos

- `tests/test_platform_gate6_interaction_unit.py`
  - prompts exatos;
  - direction grammar congelada;
  - PT-BR amount grammar, incluindo `1.200,50`;
  - rejeições, prioridade e overrides.
- `tests/test_platform_gate6_runtime_unit.py`
  - ponto de integração do worker;
  - retomada `VALIDATING`;
  - precedência user-confirmed > item > extração > derivação;
  - direction ambígua não sobrescreve resposta APPLIED;
  - amount APPLIED é entrada da reavaliação;
  - `EffectiveFinancialDecision` recompõe eligibility/question/reason;
  - worker prioriza claim resumível antes de READY;
  - sender real obrigatório;
  - zero blind resend;
  - zero sucesso antes de `COMMITTED`;
  - matriz unitária de falhas.
- `tests/test_platform_gate6_interaction_disposable_postgres.py`
  - G6-X01..G6-X10 completos;
  - resposta válida fecha `OUTBOUND_OUTCOME_UNKNOWN` como `ANSWERED`;
  - divergência entre `UserAnswer APPLIED` e fato materializado falha fechado;
  - dois workers disputam um resume e exatamente um adquire row/lease;
  - crash após resume claim expira lease, recovery ignora ACK histórico e permite novo resume;
  - trace direction + amount prova generations 1/2, zero repetição e FIFO;
  - locks, races, generations, checkpoints, TTL e FIFO físicos;
  - Gate 5 evaluator real e WUZAPI/Writer fakes locais.

Testes Gate 4/Gate 5 existentes não serão alterados para fabricar compatibilidade.

---

## 22. Ordem de Implementação Futura

1. Adicionar testes unitários de prompt/parser e fazê-los falhar pelo motivo esperado.
2. Implementar formatter P0 e extensão PT-BR mínima no `user_interaction_service`.
3. Implementar adapter de schemas e coordenação Gate 5 em `fifo_worker_service`.
4. Implementar claim idempotente de retomada `VALIDATING`.
5. Tornar stale recovery generation-aware para resume/answers, preservando casos Gate 4.
6. Integrar caminho inicial e retomada no `fifo_worker.py`.
7. Integrar no `fifo_worker.py` o sender real sobre o cliente WUZAPI existente, sem alterar `wuzapi.py`, sem falso ACK e sem blind resend.
8. Adicionar testes PostgreSQL G6-X01..X10 e os races/recovery de resume.
9. Rodar regressão Gate 4 e Gate 5 sem modificar seus testes.
10. Rodar suíte completa em PostgreSQL 15 descartável.
11. Inspecionar diff e aguardar autorização explícita de implementação; não iniciar automaticamente.

---

## 23. Verificação Futura

Pythonpath obrigatório:

```text
packages/security/src
packages/observability/src
packages/db/src
apps/orchestrator/src
apps/transcription/src
apps/bot_df/src
apps/db_writer/src
```

Comandos estáticos:

```powershell
python -m compileall packages/db apps/orchestrator apps/db_writer tests
python -m ruff check packages/db/src/db/models.py apps/orchestrator/src/orchestrator/ apps/db_writer/src/db_writer/ tests/
python -m mypy apps/orchestrator/src/orchestrator/services/fifo_worker_service.py apps/orchestrator/src/orchestrator/services/user_interaction_service.py apps/orchestrator/src/orchestrator/services/stale_recovery_service.py apps/orchestrator/src/orchestrator/fifo_worker.py
```

Testes Gate 6:

```powershell
python -m pytest -o "pythonpath=packages/security/src packages/observability/src packages/db/src apps/orchestrator/src apps/transcription/src apps/bot_df/src apps/db_writer/src" tests/test_platform_gate6_interaction_unit.py tests/test_platform_gate6_runtime_unit.py tests/test_platform_gate6_interaction_disposable_postgres.py -v --no-header --tb=short
```

Regressões obrigatórias:

- Gate 4 completo: zero failures/errors/skips não intencionais;
- Gate 5: 63 passed, zero failures/errors;
- suíte `tests/` completa: zero failures/errors;
- `git diff --check`: PASS.

PostgreSQL-dependent tests devem usar somente PostgreSQL 15 descartável local, com bancos separados quando necessário. Nenhuma URL persistente/remota pode estar presente.

---

## 24. Conclusão de Migrations

**ZERO NOVAS MIGRATIONS SÃO NECESSÁRIAS.**

O schema existente já possui:

- todos os fatos financeiros em `processing_items`;
- `question_type`, `waiting_since` e `expires_at`;
- `user_interactions` com generation/status/outbound identity;
- `user_answers` com parsing/idempotência;
- checkpoints em `executions`;
- constraints e partial indexes para FIFO/interação.

Não criar, editar nem executar migration durante Gate 6 sem nova autorização explícita.

---

## 25. Fora do Escopo

- alterar Gate 4 FIFO, persistence outcomes ou Database Writer;
- alterar regras puras/frozen do Gate 5;
- `document_classification` P0;
- perguntar data ao usuário;
- baixa confiança/quality threshold ainda TBD;
- mensagem final de sucesso runtime (Gate 8);
- retry cego de prompt WUZAPI;
- inventar API de status WUZAPI;
- inserir CPF/CNPJ reais (Gate 10/pre-production);
- mudar schema, migrations, dependências ou deployment/runtime configuration;
- executar banco persistente/staging/produção/remoto;
- iniciar Gate 7/8 ou aprovar Gate 6 automaticamente.

---

## 26. Decisões Diferidas Não Bloqueantes

- Payload/capacidades exatas da versão WUZAPI implantada continuam no TBD-004; Gate 6 usa somente o `send_text_message` já existente.
- Reconciliação por status provider não existe no contrato atual; `OUTBOUND_OUTCOME_UNKNOWN` resolve somente por resposta, cancelamento ou TTL, sem resend.
- Mensagem final permanece atribuída ao Orchestrator, mas sua entrega idempotente será planejada no Gate 8.
- CPF/CNPJ reais permanecem dependência de Gate 10/pre-production.

Nenhuma dessas decisões impede a implementação P0 descrita neste plano.

---

## 27. Blockers e HOLD

Blockers de arquitetura encontrados: **nenhum**.

O plano resolve o gap entre resposta `APPLIED -> VALIDATING` e retomada pelo worker sem alterar o estado congelado do Gate 4E e sem modificar o avaliador Gate 5.

Final governance status:

```text
GATE 6 APPROVED / COMPLETE
```

Verification evidence: Gate 6 **64 passed**; Gate 4 **210 passed**; Gate 5 **63 passed**; complete suite **439 passed**; zero skips/failures/errors. compileall, Ruff, mypy, and `git diff --check` passed in PostgreSQL 15 disposable testing.

Gate 6 verification is `PASSED` and `G6-APPROVED = true`. Gate 7 and Gate 8 remain `NOT STARTED`.

---

## 28. Formal Approval Closure

Final accepted implementation contracts:

- `EffectiveFinancialDecision` remains separate from the raw Gate 5 result;
- `APPLIED` answers provide durable provenance, and divergence from the materialized `ProcessingItem` fact fails closed;
- Boundary 4 of `dispatch_user_prompt` owns durable `ProcessingItem.question_type`, `WAITING_USER_INPUT`, `waiting_since`, and `expires_at`;
- resumable `VALIDATING` work uses the dedicated atomic claim with `FOR UPDATE SKIP LOCKED`, preserving sequence and `attempt_count`;
- stale recovery is generation/outbound-aware, so historical answered checkpoints cannot reopen an old question;
- direction retains the exact approved aliases; amount uses full-match `Decimal` parsing with approved PT-BR grouping;
- `OUTBOUND_OUTCOME_UNKNOWN` accepts a valid answer and never causes blind resend;
- waiting blocks only the same conversation, while unrelated conversations continue;
- Gate 6 sends no final success notification; final success delivery remains outside Gate 6.

Implementation testing exposed a late-answer foreign-key defect. It was corrected inside the already-authorized `user_interaction_service.py` Gate 6 scope and is not an architectural deviation.

Final evidence:

- Gate 6: 64 passed, 0 skipped, 0 failed, 0 errors;
- Gate 4 regression: 210 passed, 0 skipped, 0 failed, 0 errors;
- Gate 5 regression: 63 passed, 0 skipped, 0 failed, 0 errors;
- complete project suite: 439 passed, 0 skipped, 0 failed, 0 errors;
- compileall, Ruff, mypy, and `git diff --check`: PASS;
- PostgreSQL 15 disposable environment only; resources cleaned afterward;
- no persistent/staging/production/remote database touched;
- `business_rules_evaluator.py` and WuzapiClient implementation unchanged;
- Gate 4 persistence behavior preserved;
- zero migrations and zero Gate 7/8 work.
