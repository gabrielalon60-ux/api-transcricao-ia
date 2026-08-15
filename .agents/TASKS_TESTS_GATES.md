# Tasks, Tests & Gates — Fase 1 DF Holding

Legenda: `[ ]` pendente · `[x]` concluído · **P0** obrigatório.

---

## GATE 0 — Arquitetura

### Tasks
- [ ] **G0-T01 P0** Aprovar PRD.
- [ ] **G0-T02 P0** Criar ADR da arquitetura.
- [ ] **G0-T03 P0** Aprovar estrutura do monorepo.
- [ ] **G0-T04 P0** Congelar contratos v1.
- [ ] **G0-T05 P0** Definir convenção de erros.
- [ ] **G0-T06 P0** Definir estados/transições.
- [ ] **G0-T07 P0** Definir service-to-service auth.
- [ ] **G0-T08 P0** Definir migrations.
- [ ] **G0-T09 P0** Definir local/staging/prod.
- [ ] **G0-T10 P0** Criar threat model inicial.

### Reviews
- [ ] **G0-X01** Responsabilidades sem sobreposição crítica.
- [ ] **G0-X02** Credencial DF prevista somente no Writer.
- [ ] **G0-X03** Correlation ID em todos os fluxos.
- [ ] **G0-X04** TBDs ligados ao gate que bloqueiam.

- [ ] **G0-APPROVED**

---

## GATE 1 — Fundação

### Tasks
- [x] **G1-T01 P0** Criar monorepo.
- [x] **G1-T02 P0** Criar Orchestrator.
- [x] **G1-T03 P0** Criar Bot DF.
- [x] **G1-T04 P0** Criar Transcription Service.
- [x] **G1-T05 P0** Criar Database Writer.
- [x] **G1-T06 P0** Criar packages contracts/security/observability.
- [x] **G1-T07 P0** Platform PostgreSQL.
- [x] **G1-T08 P0** Migrations iniciais.
- [x] **G1-T09 P0** Models iniciais.
- [x] **G1-T10 P0** Health/readiness.
- [x] **G1-T11 P0** Logging estruturado.
- [x] **G1-T12 P0** Correlation middleware.
- [x] **G1-T13 P0** `.env.example` sem secrets.
- [x] **G1-T14 P0** Docker Compose local.
- [ ] **G1-T15** CI lint/typecheck/tests.

### Tests
- [x] **G1-X01** Clone limpo → ambiente sobe.
- [x] **G1-X02** Migrations em DB vazio.
- [x] **G1-X03** Health de todos serviços.
- [x] **G1-X04** Restart preserva Platform DB.
- [x] **G1-X05** Scan de repo sem secrets.
- [x] **G1-X06** Scan de secrets completo com Gitleaks (worktree e histórico).

- [x] **G1-APPROVED**

---

## GATE 2 — Orquestrador / Cadastro

### Tasks
- [x] **G2-T01 P0** Endpoint webhook WUZAPI.
- [x] **G2-T02 P0** Assinatura/secret webhook (`x-hmac-signature`).
- [x] **G2-T03 P0** Normalizador payload.
- [x] **G2-T04 P0** Resolver instance.
- [x] **G2-T05 P0** Resolver organization.
- [x] **G2-T06 P0** Resolver bot.
- [x] **G2-T07 P0** Normalizar telefone (preservar 9º dígito e DDI).
- [x] **G2-T08 P0** Verificar user.
- [x] **G2-T09 P0** Idempotência por provider, external_instance_id e external_message_id.
- [x] **G2-T10 P0** `/cadastro`.
- [x] **G2-T11 P0** Hash/HMAC registration secret (`REGISTRATION_SECRET_PEPPER`).
- [x] **G2-T12 P0** Sanitizar cadastro dos logs e payloads.
- [x] **G2-T13 P0** Rate limit cadastro persistente concorrente (`registration_attempts` e `registration_rate_limits`).
- [x] **G2-T14 P0** Impedir telefone em duas organizações (constraint de unicidade).
- [x] **G2-T15 P0** Outbound WUZAPI.
- [x] **G2-T16 P0** Configurar chave HMAC no WUZAPI.
- [x] **G2-T17 P0** Reorganizar serviços para layout src (sem PYTHONPATH).
- [x] **G2-T18 P0** Autenticação interna Orchestrator->Bot via token Bearer (`ORCHESTRATOR_TO_BOT_TOKEN`).
- [ ] **G2-T19 P0** TBD-WUZAPI-HMAC-ENCODING de assinaturas (BLOCKED - Pendente de integração real).
- [ ] **G2-T20 P0** TBD-WUZAPI-UNKNOWN-INSTANCE-OUTBOUND de instâncias desconhecidas (BLOCKED - Desabilitado por segurança).
- [x] **G2-T21 P0** Isolar chamadas HTTP fora de transações ativas do banco.
- [x] **G2-T22 P0** Inicialização atômica do rate limit (`INSERT ... ON CONFLICT DO NOTHING`).
- [x] **G2-T23 P0** Implementar HMAC-SHA256 para PII nos logs com `LOG_PII_HASH_KEY`.
- [x] **G2-T24 P0** Bloqueio geral para `USER_ORGANIZATION_MISMATCH` em qualquer tipo de mensagem.

### Tests
- [x] **G2-X01** Instância A → DF → BOT DF (ROUTED).
- [x] **G2-X02** Instância inexistente → rejeição genérica e auditação (`INSTANCE_NOT_FOUND`).
- [x] **G2-X03** Desconhecido + arquivo → zero Gemini (envio de instruções).
- [x] **G2-X04** Cadastro correto → active (idempotente e resets rate limit).
- [x] **G2-X05** Cadastro incorreto → não cadastra (grava falha persistente).
- [x] **G2-X06** Brute force → rate limit persistente.
- [x] **G2-X07** Senha não aparece no log.
- [x] **G2-X08** Mesmo message ID de origem externa → um evento (idempotência e incremento de duplicate_count).
- [x] **G2-X09** Telefone da org A → não entra na B (erro de unicidade).
- [x] **G2-X10** Webhook sem assinatura ou assinatura inválida → 401.
- [x] **G2-X11** Webhook com corpo alterado e assinatura antiga → 401.
- [x] **G2-X12** Replay de webhook de instâncias diferentes com mesmo ID → eventos distintos.
- [x] **G2-X13** Transação concorrente no cadastro cria apenas um usuário.
- [x] **G2-X14** Autenticação interna Orchestrator->Bot via token Bearer.
- [x] **G2-X15** Execução de testes via uv run pytest sem PYTHONPATH.
- [x] **G2-X16** Replays concorrentes simultâneos de webhooks idênticos → processamento único.
- [x] **G2-X17** Concorrência do rate limit de cadastro (SELECT FOR UPDATE).
- [x] **G2-X18** Privacidade dos logs: sem número de telefone aberto ou senhas.
- [x] **G2-X19** Sem transações abertas durantes requisições HTTP externas.
- [x] **G2-X20** Concorrência de inicialização de rate limit na primeira falha.
- [x] **G2-X21** Validação de HMAC-SHA256 de correlação de logs usando `LOG_PII_HASH_KEY`.
- [x] **G2-X22** Mismatch de organização em mídias, cadastros e textos.
- [x] **G2-X23** Usuário inativo ou suspenso recebe mensagem desativada e gera `USER_INACTIVE`.
- [x] **G2-X24** Falha de notificação de WhatsApp preserva estado transacional de negócio.

- [x] **G2-APPROVED**

---

## GATE 3 — Transcrição
Plano Oficial: [.agents/IMPLEMENTATION_PLAN_GATE_3.md](file:///c:/Projetos%20VS%20Code/API%20Transcrição%20IA/.agents/IMPLEMENTATION_PLAN_GATE_3.md)

Document-type authority: Gate 3 supports six business document categories represented by four technical runtime labels by explicit product decision. Mapping: Nota fiscal -> `invoice`; Cupom fiscal -> `invoice`; Comprovante PIX -> `pix_receipt`; Boleto -> `bank_receipt`; Pedido -> `commercial_document`; Orçamento -> `commercial_document`. `unknown` is fallback-only, not a supported business category. The six fixture tasks remain separate business acceptance tests; they do not imply six separate runtime schemas.

System-prompt authority: `MAX_SYSTEM_PROMPT_SIZE_BYTES=262144` raw bytes (256 KiB), inclusive boundary; strict UTF-8; empty/whitespace-only invalid; startup validation required; shared runtime defensive validation required; validated prompt cached once per process; prompt/path/size changes require restart; runtime failure maps to `SYSTEM_PROMPT_INVALID`, HTTP 503, `retryable=false`; `/internal/extract` preserves Transaction A before defensive runtime prompt loading and persists terminal `FAILED` if the prompt becomes invalid after Transaction A.

Gate 3 approval: formally approved by explicit user instruction on 2026-08-04 (America/Sao_Paulo) after formal review result `REVIEW PASSED WITH FOLLOW-UPS`. Gate 3 application implementation is APPROVED and Gate 3 is COMPLETE. Production deployment, production database adoption, and WUZAPI production media-retention verification were not performed. Gate 4 was not started.

### Tasks
- [x] **G3-T01 P0** Rota interna `/internal/extract` e compatibilidade com rota legado `/extract`. DONE — internal route implemented; legacy route compatibility covered by focused Gate 3 tests and formal review.
- [x] **G3-T02 P0** Autenticação com token direcional `BOT_TO_TRANSCRIPTION_TOKEN` na rota interna. DONE — fails closed, timing-safe comparison, and no token leakage; covered by focused auth tests and formal review.
- [x] **G3-T03 P0** Validação estrutural de imagens (Pillow) e PDFs (pypdf) contra arquivos corrompidos/criptografados. DONE — covered by document-validation tests and focused Gate 3 suite.
- [x] **G3-T04 P0** Limite de tamanho dinâmico e leitura em chunks limitados (até `max_upload_size_bytes + 1`). DONE — bounded upload streaming and 413 behavior covered by focused tests.
- [x] **G3-T05 P0** Validação estrita de Magic Bytes (JPEG, PNG, WEBP, PDF) e coerência com MIME declarado. DONE — MIME/signature matrix covered by focused tests.
- [x] **G3-T06 P0** Extração de campos estruturados da Nota fiscal. DONE — fake-provider business fixture persisted/replayed as `invoice`; semantic Gemini OCR accuracy not claimed.
- [x] **G3-T07 P0** Extração de campos estruturados do PIX. DONE — fake-provider business fixture persisted/replayed as `pix_receipt`; semantic Gemini OCR accuracy not claimed.
- [x] **G3-T08 P0** Extração de campos estruturados do Boleto. DONE — fake-provider business fixture persisted/replayed as `bank_receipt`; semantic Gemini OCR accuracy not claimed.
- [x] **G3-T09 P0** Extração de campos estruturados do Cupom fiscal. DONE — fake-provider business fixture persisted/replayed as `invoice`; semantic Gemini OCR accuracy not claimed.
- [x] **G3-T10 P0** Extração de campos estruturados do Pedido. DONE — fake-provider business fixture persisted/replayed as `commercial_document`; semantic Gemini OCR accuracy not claimed.
- [x] **G3-T11 P0** Extração de campos estruturados do Orçamento. DONE — fake-provider business fixture persisted/replayed as `commercial_document`; semantic Gemini OCR accuracy not claimed.
- [x] **G3-T12 P0** Mapeamento do response contract contendo `raw_extraction` e `normalization` higienizados. DONE — response contract covered by six business fixtures, replay tests, and formal review.
- [x] **G3-T13 P0** Resolução package-aware do prompt central e validação no startup. PASS — authority-resolution: `importlib.resources` is not frozen; behavior is package/default prompt loading independent of repository CWD, package-data inclusion, explicit prompt path support, shared startup/runtime validation, and no repository-relative dependency. Evidence: prompt package-data config, PromptService tests, lifespan tests, focused Gate 3 suite.
- [x] **G3-T14 P0** Mapeamento de `quality_flags` e detecção de baixa qualidade. DONE — provider response mapping and fake-provider fixtures cover persisted `quality_flags`; semantic OCR accuracy not claimed.
- [x] **G3-T15 P0** Persistência de estatísticas de tokens e cálculo de custo via Decimal com base em tabelas de preços versionadas. DONE — nullable token usage, Decimal cost, NUMERIC(18,8), and usage logs covered by focused tests, migration tests, and Supabase evidence.
- [x] **G3-T16 P0** Geração incremental do hash `file_sha256` durante a leitura (apenas para auditoria metadata). DONE — SHA-256 audit metadata covered by focused tests and six-fixture persistence evidence.
- [x] **G3-T17 P0** Limpeza determinística de mídias em memória/temporárias em bloco `finally`. DONE — upload close/temp cleanup covered by focused tests; WUZAPI original-media retention remains production-operational follow-up outside Gate 3 completion.
- [x] **G3-T18 P0** Controle de timeouts de upload (total e chunk) e tratamento de exceções com códigos de erro sanitizados. DONE — upload/document/provider sanitized error behavior covered by focused tests and formal review.
- [x] **G3-T19 P0** Política de até 2 retries para falhas técnicas transitórias do Gemini. DONE — provider retry policy covered by focused tests; no Gemini call performed in closure.
- [x] **G3-T20 P0** Exclusão explícita de componentes de fila FIFO (propriedade do Gate 4). DONE — formal review confirmed no queue/FIFO/persistent retry/Gate 4 behavior.
- [x] **G3-T21 P0** Nota de rastreamento do diff preliminar de `extract.py`. DONE — legacy route compatibility and shared prompt/provider changes reviewed and tracked.
- [x] **G3-T22 P0** Parseamento de metadados via campo multipart JSON string (`metadata`). DONE — strict multipart JSON metadata parsing covered by focused route tests.
- [x] **G3-T23 P0** Propagação do cabeçalho de rastreamento `correlation_id` no contrato interno. DONE — approved metadata contract and persistence/replay behavior covered by focused tests and formal review.
- [x] **G3-T24 P0** Controle de idempotência de requisições baseado no `request_id` único no banco. DONE — replay/concurrency behavior covered by focused tests and isolated Supabase PostgreSQL evidence.
- [x] **G3-T25 P0** Sem retentativas de extração automáticas no Bot DF (exclusão de retries duplicados). DONE — formal review confirmed no Bot retry worker/FIFO behavior introduced in Gate 3.
- [x] **G3-T26 P0** TBD-TRANSCRIPTION-SCHEMA-MAPPING para inspeção física das tabelas legadas. DONE — schema mapping resolved (`.agents/transcription_schema_mapping.md`), ORM models implemented and locally validated.
- [x] **G3-T27 P0** Persistência de logs de uso e estatísticas por tentativa do provedor. DONE — per-attempt usage logs covered by focused tests, migration-source tests, and isolated Supabase evidence.
- [x] **G3-T28 P0** Validação estrutural de arquivos sob isolamento de subprocesso descartável com terminação real. DONE — subprocess timeout/terminate/kill/IPC validation covered by focused tests.
- [x] **G3-T29 P0** Validação de imagem Pillow (MAX_PIXELS, DecompressionBombWarning como erro, verify). DONE — image validation matrix covered by focused tests.
- [x] **G3-T30 P0** Detecção e rejeição de conteúdo ativo em PDFs (`PDF_ACTIVE_CONTENT_UNSUPPORTED`). DONE — structured PDF active-content traversal covered by focused tests.
- [x] **G3-T31 P0** Limite de concorrência local de validações com acquisition timeout (`VALIDATION_CAPACITY_EXCEEDED`). DONE — semaphore acquisition timeout covered by focused tests; process-local only, not Gate 4 queueing.
- [x] **G3-T32 P0** Transporte serializável para subprocesso (bytes vs path temporário pelo limite max memory). DONE — subprocess transport and temp materialization/cleanup covered by focused tests.
- [x] **G3-T33 P0** Canal de IPC para retorno do ValidationResult e tratamento de crash/IPC error. DONE — malformed IPC/crash behavior covered by focused tests.
- [x] **G3-T34 P0** Validação de assinatura WEBP RIFF e payload size plausível. DONE — WEBP signature behavior covered by focused tests.
- [x] **G3-T35 P0** PDF active-content traversal cycle/depth e object limits. DONE — PDF traversal depth/object limits covered by focused tests.
- [x] **G3-T36 P0** Tratamento determinístico de fallback FAILED e PERSISTENCE_ERROR (incluindo recovery). DONE — failed replay, persistence failure, and compensation evidence covered by focused tests and isolated Supabase evidence.
- [x] **G3-T37 P0** Verificar se Docker/Uvicorn inicia o serviço Transcription com exatamente 1 worker por réplica. PASS — Docker Compose transcription command explicitly uses `uvicorn transcription.main:app ... --workers 1`; static regression test asserts the committed command remains one-worker. This protects process-local validation concurrency only and is not Gate 4 queueing.

### Migration Source Review Artifacts
- Dedicated Transcription Alembic source exists under `apps/transcription/alembic/` with `alembic_version_transcription`.
- Canonical migration chain exists: `transcription_1_0_baseline` → `gate3_schema`.
- Read-only Profile A, Profile B, and post-Gate-3 schema verifiers exist under `apps/transcription/src/transcription/database/migrations/`.
- Explicit Profile B reconciliation source exists and requires Profile B verifier preflight.
- Isolated source tests exist in `tests/test_transcription_migration_sources.py`.
- Validation executed: Python compileall PASS; import check PASS; Ruff PASS; mypy PASS for new verifier modules; focused migration-source tests PASS; dedicated Alembic heads/history PASS; offline SQL generated and statically inspected.
- Disposable PostgreSQL validation executed on PostgreSQL 15 in isolated Docker infrastructure only: `localhost:55432/transcription_gate3_test`, container `transcription_gate3_migration_test`, volume `transcription_gate3_migration_test_data`.
- Disposable validation PASS: fresh migration to head, historical baseline, canonical Gate 3 upgrade with legacy rows, Profile A verifier/stamp/upgrade, Profile B verifier/reconciliation/stamp, unsupported drift rejection, enum order, cost conversion, attempt-number backfill, uniqueness replacement, post-state verifiers.
- Full pytest suite PASS with `DATABASE_URL`, `TRANSCRIPTION_DATABASE_URL`, and `GATE3_DISPOSABLE_DATABASE_URL` bound to the disposable PostgreSQL target: 43 passed.
- Source defects found and corrected during disposable validation: Gate 3 enum insertion order now uses `SUCCEEDED BEFORE FAILED`; verifiers fail closed for missing columns instead of raising `KeyError`; Profile B reconciliation rolls back read-only preflight transaction before DDL.
- Disposable container and volume removed after validation.
- Preserved PostgreSQL Profile B adoption runbook prepared at `.agents/GATE_3_PROFILE_B_ADOPTION_RUNBOOK.md`.
- Adoption evidence template prepared at `.agents/GATE_3_PROFILE_B_ADOPTION_EVIDENCE_TEMPLATE.md`.
- Final safety/correctness audit of the preserved-database adoption runbook completed; documentation hardened for explicit identity checks, command safety, hold ordering, transaction ownership, and numeric conversion evidence.
- Preserved-database adoption execution remains unauthorized and not performed.
- No preserved-database upgrade, stamp, downgrade, reconciliation, DDL, or DML was executed.
- No Gate 3 adoption/migration application task is marked complete by this source-only review.

### Application Implementation Progress
- Internal application implementation has begun without marking Gate 3 complete.
- Added internal `/internal/extract` route source, `BOT_TO_TRANSCRIPTION_TOKEN` authentication source, metadata response schemas, subprocess-backed document validation source, and two-transaction internal extraction service source.
- Transcription runtime DB configuration now uses `TRANSCRIPTION_DATABASE_URL`; no `DATABASE_URL` fallback is used by the Transcription session.
- Startup `Base.metadata.create_all()` was removed; Alembic remains schema authority.
- Provider contract and Gemini adapter now preserve nullable usage, Decimal cost, strict supported signatures, and no unknown-token-to-zero conversion.
- Legacy `/extract` remains present with API-key authentication; shared usage logging now supplies explicit `attempt_number=1` for Gate 3 schema compatibility.
- ORM alignment note: `Application.api_key_hash` is mapped to the historical baseline column name `applications.api_key` to preserve legacy API-key auth against the approved migration source without changing migration history.
- Verification executed for this application implementation: compileall PASS; Ruff PASS; mypy PASS on touched Gate 3 source files; focused Gate 3 source/application tests PASS (11 passed); existing safe test suite PASS (35 passed) with destructive PostgreSQL migration integration excluded.
- Second-pass verification expanded focused route/auth/metadata/validator/retry/compensation/legacy mapping coverage. Latest evidence: compileall PASS; Ruff PASS; mypy PASS on touched Gate 3 modules; focused Gate 3 + migration-source tests PASS (37 passed); safe full suite PASS (61 passed) with destructive PostgreSQL migration integration excluded.
- Safe isolated Supabase integration PASS against sanitized target `db.btdkssnuwdtjnmcpfxjm.supabase.co:5432/postgres`; `alembic_version_transcription = gate3_schema`; fake provider only; inserted 3 request UUID rows, 1 extraction row, and 4 usage rows; cleanup removed all 3 request IDs and verified 0 remaining request rows for those IDs.
- Gate 3 remains incomplete: the full required authentication, metadata, file-validation, idempotency/concurrency, persistence-failure, provider retry, legacy regression, and Supabase integration acceptance test matrix has not all been implemented or executed.
- Remaining incomplete acceptance areas include real PostgreSQL same-ID concurrency through simultaneous route/service execution, full HTTP-level legacy `/extract` regression matrix, six document-type extraction fixtures, deeper generated PDF object/depth-limit fixtures, and startup prompt packaging tests.
- Third-pass metadata contract clarification recorded in `.agents/IMPLEMENTATION_PLAN_GATE_3.md` and `.agents/transcription_schema_mapping.md`: current internal metadata uses `request_id`, `bot_instance_id`, `correlation_id`, `received_at`, and `source=WHATSAPP`; obsolete older fields are not part of the current internal route contract.
- Third-pass local verification PASS: compileall, Ruff, mypy on touched Gate 3 modules, focused Gate 3 + migration-source tests (42 passed), and safe full suite excluding destructive PostgreSQL migration integration (66 passed).
- Third-pass Supabase isolated-row verification PASS: Transaction A visibility, same-ID race using PostgreSQL uniqueness (`[200, 409]`, exactly 1 provider call), physical replays for `PROCESSING`/`FAILED`/`PERSISTENCE_FAILED`/`SUCCEEDED` with 0 provider calls, scoped inserts `(requests=6, extractions=3, usage_logs=2)`, scoped deletes `(requests=6, extractions=3, usage_logs=2)`, and final total Transcription table counts all 0.
- Remaining incomplete acceptance areas now include real PostgreSQL Transaction B failure injection/compensation-failure physical proof, six approved document-type fixtures, deeper generated PDF object/depth-limit fixtures, complete subprocess cancellation/forced-kill/temp-cleanup matrix, and startup prompt packaging tests.
- Fourth-pass Transaction B/compensation verification PASS on isolated Supabase test database. Production code now explicitly flushes Transaction B before commit and uses a separate SQLAlchemy session/transaction for compensation. Deterministic test-only session-proxy failures covered success extraction insert, success usage insert, success flush, handled-failure usage insert, handled-failure flush, compensation flush failure, and compensation commit failure.
- Fourth-pass physical evidence: Transaction B failures left no extraction or usage remnants; compensation success physically committed `PERSISTENCE_FAILED`; compensation failure physically left `PROCESSING`; provider call counts remained the original attempt counts; failed replay reconstruction returned exact persisted error mappings for `UNSUPPORTED_FILE_TYPE`, `PROVIDER_TEMPORARY_ERROR`, `INTERNAL_ERROR`, and `PERSISTENCE_ERROR` with zero provider calls. Scoped Supabase cleanup removed all 11 test requests; final total Transcription table counts all 0.
- Fourth-pass sanitization defect corrected: compensation failure logs no longer emit stack traces. Supabase recheck confirmed sanitized single-line compensation failure log, HTTP 500 `PERSISTENCE_ERROR`, physical status `PROCESSING`, and no extraction/usage remnants.
- Six approved business document-category fixture acceptance passed using the approved four runtime labels and fake providers only. This proves orchestration/contract mapping, persistence, usage attempt logging, SHA-256 audit metadata, and replay behavior; it does not prove semantic Gemini OCR accuracy.

### Tests
- [x] **G3-X01** Fixture Nota Fiscal (verificar mapeamento e integridade). PASS — fake-provider business fixture persisted/replayed as `invoice`.
- [x] **G3-X02** Fixture PIX (verificar mapeamento e integridade). PASS — fake-provider business fixture persisted/replayed as `pix_receipt`.
- [x] **G3-X03** Fixture Boleto (verificar mapeamento e integridade). PASS — fake-provider business fixture persisted/replayed as `bank_receipt`.
- [x] **G3-X04** Fixture Cupom (verificar mapeamento e integridade). PASS — fake-provider business fixture persisted/replayed as `invoice`.
- [x] **G3-X05** Fixture Pedido (verificar mapeamento e integridade). PASS — fake-provider business fixture persisted/replayed as `commercial_document`.
- [x] **G3-X06** Fixture Orçamento (verificar mapeamento e integridade). PASS — fake-provider business fixture persisted/replayed as `commercial_document`.
- [x] **G3-X07** Separação de autenticação: token interno vs chave de API externa. PASS — focused tests cover internal token rejection on legacy route and legacy API-key separation.
- [x] **G3-X08** Leitura limitada por streaming e rejeição imediata com HTTP 413. PASS — focused upload tests cover bounded streaming and `FILE_TOO_LARGE`.
- [x] **G3-X09** Validação de Magic Bytes contra MIME spoofing. PASS — focused validation tests cover signature/MIME mismatch.
- [x] **G3-X10** Encriptação PDF e limites de páginas rejeitados localmente como não-retryable. PASS — focused PDF validation tests cover encrypted/page-limit rejection.
- [x] **G3-X11** Decompressão de imagem extrema e limites de pixel (decompression bomb defense). PASS — focused image validation tests cover Pillow pixel/decompression defenses.
- [x] **G3-X12** Limpeza de arquivos temporários garantida em falhas, timeouts e sucessos. PASS — focused temp-cleanup tests cover success/failure/timeout cleanup.
- [x] **G3-X13** Duplicação de SHA-256 sob IDs de requisição diferentes é aceita. PASS — focused persistence tests cover SHA-256 as audit metadata only, not dedupe authority.
- [x] **G3-X14** Persistência Decimal de uso estável e sem recálculo retroativo. PASS — focused and migration tests cover Decimal/NUMERIC usage persistence.
- [x] **G3-X15** Carregamento de prompt do pacote local e falha no startup se ausente. PASS — package/default prompt loads outside repo CWD; explicit prompt path works; exact 262144-byte boundary accepted; 262145 bytes, missing, directory, invalid UTF-8, empty/whitespace-only, and malformed/zero/negative size configuration fail safely; startup validates prompt; runtime defensive failure maps to `SYSTEM_PROMPT_INVALID` HTTP 503 and persists internal `FAILED` after Transaction A.
- [x] **G3-X16** Isolamento da fila do Bot DF (sem controle de concorrência ou FIFO no Gate 3). PASS — formal review confirmed no queue/FIFO/persistent retry worker was introduced.
- [x] **G3-X17** Até 2 retries técnicos aplicados apenas em erros transitórios do provedor. PASS — focused retry tests cover transient-only retry behavior.
- [x] **G3-X18** Auditoria de logs sem dados sensíveis, tokens ou binários de arquivo. PASS — focused sanitization tests and formal diff/security review found no secret/content logging issue.
- [x] **G3-X19** Teste de assinatura de PDF estrita em b"%PDF-" no byte zero e falhas com lixo. PASS — focused PDF signature tests cover strict `%PDF-` at byte zero.
- [x] **G3-X20** Teste de parseamento multipart JSON metadata estruturado e rejeição de UUIDs inválidos. PASS — focused metadata tests cover strict JSON, UUID validation, timezone, source, and extra-field rejection.
- [x] **G3-X21** Teste de idempotência de request_id concorrente (HTTP 409 em progresso e replays). PASS — focused replay tests and isolated Supabase concurrency evidence cover this behavior.
- [x] **G3-X22** Teste de isolamento de processo no parser de arquivos com timeout. PASS — focused subprocess timeout/terminate/kill tests cover this behavior.
- [x] **G3-X23** Teste de detecção e rejeição de scripts/conteúdo ativo no PDF. PASS — focused structured PDF active-content tests cover this behavior.
- [x] **G3-X24** Teste de compatibilidade de API Keys legadas em rotas externas. PASS — focused legacy `/extract` API-key regression tests cover this behavior.

- [x] **G3-APPROVED** APPROVED — explicit user approval recorded on 2026-08-04 (America/Sao_Paulo). Gate 3 application implementation APPROVED; Gate 3 COMPLETE. Production deployment/database adoption not performed. Gate 4 NOT STARTED.

---

## GATE 4 — Fila persistente
Plano Oficial: [.agents/IMPLEMENTATION_PLAN_GATE_4.md](file:///c:/Projetos%20VS%20Code/API%20Transcrição%20IA/.agents/IMPLEMENTATION_PLAN_GATE_4.md)
Schema Mapping: [.agents/gate4_queue_schema_mapping.md](file:///c:/Projetos%20VS%20Code/API%20Transcrição%20IA/.agents/gate4_queue_schema_mapping.md)
Decisões Arquiteturais: [.agents/GATE_4_DECISIONS_REQUIRED.md](file:///c:/Projetos%20VS%20Code/API%20Transcrição%20IA/.agents/GATE_4_DECISIONS_REQUIRED.md)

Status:
- HOLD 2: APPROVED by explicit user instruction on 2026-08-04.
- HOLD 3: APPROVED by explicit user instruction on 2026-08-04.
- Phase 4A (Platform DB Schema & Migration): APPROVED.
- Phase 4B (Durable Ingestion Path in Orchestrator): APPROVED on 2026-08-04.
- Phase 4C (Concurrent Extraction Dispatch & READY Transition): APPROVED by explicit user instruction on 2026-08-04.
- Platform Gate 4 migration sources (`7a8f9c1b2d3e`, `8b9a0c1d2e3f`, `9c0a1b2c3d4e`): APPROVED.
- Phase 4D (FIFO Worker Claim Engine & Monotonic Blocked Execution): APPROVED on 2026-08-05.
- Phase 4E (Business Worker Heartbeat, Stale Recovery, Interaction Cycle & User Answer Ledger): APPROVED on 2026-08-05.
- Migration `9e0a1b2c3d5e` source: APPROVED.
- Migration `9f1b2c3d4e5f` source: APPROVED.
- Platform migration `a1b2c3d4e5f6` source: APPROVED.
- Database Writer migration `a1b2c3d4e5f6` source: APPROVED.
- Phase 4F (Database Writer Integration & Gate 4 Final Closure): APPROVED by explicit user instruction on 2026-08-08.
- HOLD 4: APPROVED.
- HOLD 5: APPROVED on 2026-08-05.
- Gate 4 overall: APPROVED / COMPLETE on 2026-08-08.
- Persistent migration execution: NOT AUTHORIZED.
- Staging migration execution: NOT AUTHORIZED.
- Production migration execution: NOT AUTHORIZED.
- Remote database execution: NOT AUTHORIZED.
- Gate 4 baseline verified: Phase 4F (98 passed), Gate 4 (210 passed), Full Project Suite (300 passed, 12 skipped*, 0 failed, 0 errors).

### Tasks
- [x] **G4-T01 P0** processing_items.
- [x] **G4-T02 P0** Sequence por conversa.
- [x] **G4-T03 P0** Estados.
- [x] **G4-T04 P0** Extração paralela limitada.
- [x] **G4-T05 P0** READY após extração.
- [x] **G4-T06 P0** Worker FIFO.
- [x] **G4-T07 P0** Um ACTIVE por conversa.
- [x] **G4-T08 P0** Lock transacional.
- [x] **G4-T09 P0** MAX_QUEUE configurável.
- [x] **G4-T10 P0** Recovery após restart.
- [x] **G4-T11 P0** Falha libera fila.

### Tests
- [x] **G4-X01** Cinco arquivos recebem sequência 1..5.
- [x] **G4-X02** IA termina 3,1,5,2,4 → negócio executa 1..5.
- [x] **G4-X03** Dois workers não pegam mesmo item.
- [x] **G4-X04** Não existem dois ACTIVE na conversa.
- [x] **G4-X05** Usuário A não bloqueia B.
- [x] **G4-X06** Restart preserva READY.
- [x] **G4-X07** Restart de ACTIVE possui recovery definido.
- [x] **G4-X08** Fila cheia → zero Gemini para excedente.
- [x] **G4-X09** EXTRACTION_FAILED #1 libera #2.

- [x] **G4-APPROVED** APPROVED — explicit user approval recorded on 2026-08-08 (America/Sao_Paulo). Gate 4 is APPROVED and COMPLETE.

---

## GATE 5 — Regras BOT DF
Plano Oficial: [.agents/IMPLEMENTATION_PLAN_GATE_5.md](file:///c:/Projetos%20VS%20Code/API%20Transcrição%20IA/.agents/IMPLEMENTATION_PLAN_GATE_5.md)

Status:
- Gate 4 overall: APPROVED / COMPLETE / FROZEN on 2026-08-08.
- Gate 5 planning: APPROVED on 2026-08-08.
- Gate 5 implementation plan: APPROVED.
- Gate 5 implementation: COMPLETE.
- Gate 5 verification: PASSED.
- Gate 5 overall: APPROVED on 2026-08-08.
- Gate 5 migrations: NONE REQUIRED.
- Gate 6: APPROVED / COMPLETE on 2026-08-08.
- Persistent/staging/production/remote migration execution: NOT AUTHORIZED.

### Tasks
- [x] **G5-T01 P0** Máquina de estados. (`BusinessRulesEvaluatorService` + `FinancialEvaluationResult`)
- [x] **G5-T02 P0** amount > 0. (`validate_amount`)
- [x] **G5-T03 P0** document_date. (`resolve_transaction_date` with `America/Sao_Paulo`)
- [x] **G5-T04 P0** fallback timestamp. (MESSAGE_TIMESTAMP fallback)
- [x] **G5-T05 P0** date_source. (`"DOCUMENT"` or `"MESSAGE_TIMESTAMP"`)
- [x] **G5-T06 P0** Lista CPF/CNPJ placeholder. (`config.df_holding_identifiers` + `normalize_digits`)
- [x] **G5-T07 P0** payer DF → expense. (`classify_direction`)
- [x] **G5-T08 P0** receiver DF → income. (`classify_direction`)
- [x] **G5-T09 P0** ambos → ambiguous. (`classify_direction`)
- [x] **G5-T10 P0** nenhum → unknown. (`classify_direction`)
- [x] **G5-T11 P0** Mensagem final. (`format_success_message`)

### Tests
- [x] **G5-X01** DF payer → expense. (63 tests passed)
- [x] **G5-X02** DF receiver → income.
- [x] **G5-X03** Ambos → não grava automaticamente.
- [x] **G5-X04** Nenhum → não grava automaticamente.
- [x] **G5-X05** amount 0 → não grava.
- [x] **G5-X06** amount ausente → pergunta futura.
- [x] **G5-X07** Data do documento usada.
- [x] **G5-X08** Data ausente → timestamp.
- [x] **G5-X09** Orçamento sem data → timestamp.
- [x] **G5-X10** Item completo não pede confirmação.

- [x] **G5-APPROVED = true**

### Final Verification Evidence
- Gate 5 tests: **63 passed, 0 skipped, 0 failed, 0 errors**.
- Frozen Gate 4 regression: **210 passed, 0 skipped, 0 failed, 0 errors**.
- Complete project suite: **375 passed, 0 skipped, 0 failed, 0 errors**.
- Static verification: **compileall PASS; Ruff PASS; mypy PASS; git diff --check PASS**.
- Reproducibility: **PostgreSQL 15 disposable test environment used**; `tzdata` declared in `apps/orchestrator/pyproject.toml`; `uv.lock` updated; `ZoneInfo("America/Sao_Paulo")` verified after frozen dependency sync.
- Scope integrity: `fifo_worker_service.py` unchanged; zero WUZAPI integration; zero Database Writer/PersistenceService integration; zero Gate 5 migrations.
- Database safety: no persistent, staging, production, or remote database touched.

---

## GATE 6 — Conversação

Status:
- Gate 6 planning: APPROVED.
- Gate 6 implementation plan: APPROVED.
- Gate 6 implementation HOLD: APPROVED.
- Gate 6 implementation: COMPLETE.
- Gate 6 verification: PASSED.
- Gate 6 overall: APPROVED on 2026-08-08.
- G6-APPROVED: true.
- Gate 6 migrations: NONE REQUIRED.
- Gate 7: APPROVED / COMPLETE; implementation COMPLETE, verification PASSED, final review APPROVED; `G7-APPROVED = true`.
- Gate 8: APPROVED / COMPLETE; implementation COMPLETE, verification PASSED, final review APPROVED; `G8-APPROVED = true`.

### Tasks
- [x] **G6-T01 P0** WAITING_USER_INPUT.
- [x] **G6-T02 P0** Pergunta direction.
- [x] **G6-T03 P0** Pergunta amount.
- [x] **G6-T04 P0** Parse 1/2.
- [x] **G6-T05 P0** Parse valor pt-BR.
- [x] **G6-T06 P0** Uma pergunta por conversa.
- [x] **G6-T07 P0** TTL 1h.
- [x] **G6-T08 P0** EXPIRED.
- [x] **G6-T09 P0** Novo arquivo durante espera.
- [x] **G6-T10 P0** Continuar após resolução.
- [x] **G6-T11 P0** Continuar após expiração.

### Tests
- [x] **G6-X01** Terceiro de cinco pergunta; #4/#5 aguardam.
- [x] **G6-X02** `1` resolve direction do item ativo.
- [x] **G6-X03** `2` resolve direction do item ativo.
- [x] **G6-X04** `1.200,50` normaliza corretamente.
- [x] **G6-X05** Resposta inválida mantém pergunta.
- [x] **G6-X06** Novo arquivo durante pendência vira READY.
- [x] **G6-X07** Expira em 1h.
- [x] **G6-X08** Expiração libera próximo.
- [x] **G6-X09** Item expirado exige reenvio.
- [x] **G6-X10** Máximo um WAITING por conversa.

- [x] **G6-APPROVED = true**

### Implementation Verification Evidence
- Gate 6 tests: **64 passed, 0 skipped, 0 failed, 0 errors**.
- Frozen Gate 4 regression: **210 passed, 0 skipped, 0 failed, 0 errors**.
- Frozen Gate 5 regression: **63 passed, 0 skipped, 0 failed, 0 errors**.
- Complete safe project suite: **439 passed, 0 skipped, 0 failed, 0 errors**.
- Static verification: **compileall PASS; Ruff PASS; mypy PASS; git diff --check PASS**.
- Test environment: **PostgreSQL 15 disposable only**.
- Database safety: **no persistent/staging/production/remote database touched; disposable resources cleaned afterward**.
- Scope integrity: **`business_rules_evaluator.py` unchanged; WuzapiClient implementation unchanged; Gate 4 persistence behavior preserved; zero Gate 7/8 work; zero final success-message runtime integration**.
- Migrations: **NONE REQUIRED; NONE CREATED OR MODIFIED**; persistent/staging/production/remote execution remains **NOT AUTHORIZED**.
- Late-answer defect: implementation testing exposed a foreign-key defect and corrected it within the already-authorized `user_interaction_service.py` Gate 6 scope; final architecture remains aligned with the approved contract.

---

## GATE 7 — Database Writer

Status: **APPROVED / COMPLETE**; implementation **COMPLETE**, verification **PASSED**, final review **APPROVED**.

Planning inputs updated on 2026-08-11:
- approved local MVP business destination is **expense-only**;
- Gate 5/6 `income|expense` evaluation remains frozen;
- local logical contracts are known for `financial_records`, `suppliers`, and `enterprises`;
- production enterprise schema remains external input;
- enterprise resolution is implemented as a post-Gate-6/pre-persistence requirement;
- effective `income` has the approved additive terminal outcome `IGNORED / INCOME_OUT_OF_SCOPE`, releases FIFO, and bypasses amount, enterprise, supplier, Writer, persistence retry, and Gate 7 final notification;
- Gate 8 owns the future idempotent informational message for `IGNORED / INCOME_OUT_OF_SCOPE`;
- the final Gate 7 HOLD contract and plan were approved; local MVP Phase A implementation was authorized and completed;
- both new migrations were created and executed only in disposable PostgreSQL 15 verification;
- production Phase B and persistent/staging/production/remote migration execution remain unauthorized;
- `G7-APPROVED = true`; formal implementation acceptance is complete; Gate 8 is APPROVED / COMPLETE with `G8-APPROVED = true`.

### Tasks
- [x] **G7-T01 P0** Fechar schema DF.
- [x] **G7-T02 P0** Contrato write.
- [x] **G7-T03 P0** Secret da conexão.
- [x] **G7-T04 P0** TLS.
- [x] **G7-T05 P0** Usuário DB mínimo.
- [x] **G7-T06 P0** Validação request.
- [x] **G7-T07 P0** Transação.
- [x] **G7-T08 P0** Idempotency key.
- [x] **G7-T09 P0** Timeout.
- [x] **G7-T10 P0** Retry técnico.
- [x] **G7-T11 P0** Sanitização de erros.
- [x] **G7-T12 P0** Return record ID.
- [x] **G7-T13 P0** Local MVP `financial_records` expense-only contract and defaults (`expense_type_id=NULL`, `comments=NULL`, `is_deleted=false`, `deleted_at=NULL`, `origin=WHATSAPP`).
- [x] **G7-T14 P0** Local MVP read-only supplier lookup by normalized unique CNPJ, with snapshot provenance and duplicate-match fail-closed behavior.
- [x] **G7-T15 P0** Local MVP enterprise read contract and mandatory `enterprise_id` before Writer DML.
- [x] **G7-T16 P0** Persistent `/empreendimento` command with dynamic deterministic list, durable option-position mapping, and last option `Limpar seleção`.
- [x] **G7-T17 P0** Platform-side idempotent chat-enterprise binding and clear operation; no supplier/enterprise DML.
- [x] **G7-T18 P0** Per-document enterprise clarification when no persistent binding exists; answer applies only to the active `ProcessingItem`.
- [x] **G7-T19 P0** Enterprise precedence: persistent chat binding, otherwise document-specific answer; unresolved enterprise forbids Writer POST.
- [x] **G7-T20 P0** Additive Platform interaction/storage contract for `enterprise_selection`, durable option mapping, and `ProcessingItem.enterprise_id`, subject to separate migration approval.
- [x] **G7-T21 P0** Expense-only early runtime guard: effective `income` atomically becomes terminal `IGNORED` with durable `outcome_reason=INCOME_OUT_OF_SCOPE`, clears claims, and performs zero amount/enterprise question, supplier lookup, expense Writer POST/row, persistence retry, or Gate 7 final notification.
- [x] **G7-T22 P0** Versioned Writer request carrying resolved `enterprise_id`, effective `transaction_date`, `date_source`, and optional normalized supplier CNPJ snapshot.
- [x] **G7-T23 P0** Writer/adapter privileges: INSERT financial record; read-only suppliers/enterprises; same-transaction idempotency ledger.
- [x] **G7-T24 P0** Separate local-MVP schema/migration plan from future client-database DDL/adoption; neither execution is authorized by planning.
- [x] **G7-T25 P0** Enforce one active human interaction across `UserInteraction` and `EnterpriseCommandSession` by locking the exact `conversation_queue_counters` row before cross-table check/create; retain per-table partial indexes as defense-in-depth.
- [x] **G7-T26 P0** Command-first inbound text routing, including idempotent busy response for `/empreendimento` during a document question and exclusive numeric-answer ownership.
- [x] **G7-T27 P0** Durable enterprise-command reservation/dispatch/outbound-unknown/answer/expiry lifecycle with dedicated idempotent command answers and no blind resend.

### Tests
- [x] **G7-X01** Write happy path.
- [x] **G7-X02** Mesma idempotency key → um registro.
- [x] **G7-X03** Timeout/retry sem duplicidade.
- [x] **G7-X04** Campo inválido → sem retry.
- [x] **G7-X05** Falha parcial → rollback.
- [x] **G7-X06** BOT não possui DB URL DF.
- [x] **G7-X07** Orchestrator não possui DB URL DF.
- [x] **G7-X08** Credencial ausente de logs.
- [x] **G7-X09** Usuário DB não consegue operação indevida.
- [x] **G7-X10** `expense` completo + empreendimento resolvido grava um `financial_records` e retorna record ID.
- [x] **G7-X11** Effective `income` becomes `IGNORED / INCOME_OUT_OF_SCOPE`, calls no expense Writer, creates zero `financial_records` row, performs zero supplier lookup, and sends zero Gate 7 final notification.
- [x] **G7-X12** CNPJ de fornecedor com uma correspondência exata preenche `supplier_id` e preserva snapshot.
- [x] **G7-X13** Fornecedor desconhecido mantém `supplier_id = NULL` e preserva `supplier_cnpj_snapshot`.
- [x] **G7-X14** CNPJ duplicado em fornecedores falha fechado, sem escolher linha e sem INSERT financeiro.
- [x] **G7-X15** Criação WhatsApp grava `expense_type_id=NULL`, `comments=NULL`, `origin=WHATSAPP`, `is_deleted=false`, `deleted_at=NULL`.
- [x] **G7-X16** `/empreendimento` persiste o `enterprise_id` real selecionado para a conversa 1:1.
- [x] **G7-X17** Última opção dinâmica `Limpar seleção` remove o vínculo; não existe comando separado de limpeza.
- [x] **G7-X18** Mapa durável `posição -> enterprise_id` preserva o significado da resposta mesmo após reordenação do cadastro.
- [x] **G7-X19** Vínculo persistente resolve automaticamente empreendimento de documento futuro.
- [x] **G7-X20** Sem vínculo, item entra em pergunta de empreendimento e permanece FIFO-bloqueante.
- [x] **G7-X21** Resposta de empreendimento por documento materializa somente o item e não cria vínculo do chat.
- [x] **G7-X22** Enterprise não resolvida causa zero Writer POST.
- [x] **G7-X23** Fluxo WhatsApp não cria, edita nem exclui fornecedor.
- [x] **G7-X24** Fluxo WhatsApp não cria, edita nem exclui empreendimento.
- [x] **G7-X25** Expiração/cancelamento da pergunta por documento segue o lifecycle aprovado; interação de comando usa seu contrato próprio sem fabricar `ProcessingItem`.
- [x] **G7-X26** Role local do Writer pode ler suppliers/enterprises e inserir financial_records/ledger, mas não pode alterar cadastros nem executar DDL/DELETE/TRUNCATE.
- [x] **G7-X27** Once direction is known as `income`, no `transaction_amount` or `enterprise_selection` interaction is created.
- [x] **G7-X28** `IGNORED` is terminal/non-blocking: the next same-conversation sequence becomes claimable.
- [x] **G7-X29** Claim, startup recovery, stale recovery, replay, cancellation, interaction lookup, persistence dispatch/retry, and reconciliation never reopen or select `IGNORED`.
- [x] **G7-X30** Gate 8 mapping is documented exactly: `IGNORED / INCOME_OUT_OF_SCOPE` idempotently sends the approved informational message, while Gate 7 sends none.
- [x] **G7-X31** Open `transaction_amount` plus concurrent `/empreendimento`: document interaction remains the sole owner; command creates no session and returns the idempotent busy response.
- [x] **G7-X32** Open enterprise command plus worker requiring `transaction_direction`: command remains sole owner; no UserInteraction or prompt is created.
- [x] **G7-X33** Simultaneous command-session and document-prompt creation under physical PostgreSQL concurrency: exactly one OPEN owner commits and both tables are never OPEN together.
- [x] **G7-X34** OPEN command plus new document: ingestion/extraction/normalization and READY persist, but same-conversation business claim is blocked; other conversations proceed.
- [x] **G7-X35** Command ANSWERED with enterprise selection: barrier closes, earliest deferred/READY item resumes and observes the new binding.
- [x] **G7-X36** Command ANSWERED with `Limpar seleção`: barrier closes, earliest item resumes and uses normal per-document enterprise fallback when required.
- [x] **G7-X37** Command EXPIRED: prior binding remains unchanged, no document is expired/cancelled, and earliest work resumes.
- [x] **G7-X38** Duplicate/concurrent numeric command answer changes the binding at most once and returns the durable committed outcome on replay.
- [x] **G7-X39** Valid answer to `OUTBOUND_OUTCOME_UNKNOWN` is accepted against the stable mapping/outbound identity with zero resend.
- [x] **G7-X40** `/empreendimento` during `transaction_amount` is parsed only as a command and never as an amount answer.
- [x] **G7-X41** Numeric command selection is routed only to the OPEN command session and never to UserInteraction.

### Previous correction evidence mapping (superseded by Correction Pass 2)

Every authoritative acceptance ID is tied to executable evidence (function names are exact):

- **G7-X01** â€” `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X02** â€” `test_concurrent_same_key_serializes_before_financial_insert` and `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X03** â€” `test_precommit_operational_error_is_retryable`, `test_deadline_exhaustion_starts_no_business_dml`, and `test_writer_deadline_refuses_new_db_operation`.
- **G7-X04** â€” `test_v2_strict_invalid_shapes_are_rejected` and `test_v2_amount_rejects_noncanonical_values`.
- **G7-X05** â€” `test_flush_integrity_error_is_sanitized_rejection` and `test_missing_enterprise_rejects_and_rolls_back`.
- **G7-X06** â€” `test_orchestrator_settings_has_no_df_database_url`.
- **G7-X07** â€” `test_orchestrator_settings_has_no_df_database_url`.
- **G7-X08** â€” `test_writer_generic_handler_is_sanitized`, `test_writer_tls_validation_does_not_accept_substring_trick`, and `test_writer_requires_database_url_and_explicit_disposable_insecure_mode`.
- **G7-X09** â€” `test_disposable_writer_role_is_least_privilege` (physical restricted-role operations).
- **G7-X10** â€” `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X11** â€” `test_income_ignored_releases_fifo_and_is_not_recovered`, `test_known_income_skips_amount_requirement_in_decision_composition`, and `test_income_guard_precedes_questions_and_persistence`.
- **G7-X12** â€” `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X13** â€” `test_unknown_supplier_is_nullable_and_snapshot_preserved`.
- **G7-X14** â€” `test_duplicate_supplier_lookup_fails_closed_before_insert`.
- **G7-X15** â€” `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X16** â€” `test_command_selection_upserts_and_clear_deletes_binding`.
- **G7-X17** â€” `test_command_selection_upserts_and_clear_deletes_binding` and `test_command_prompt_appends_clear_as_n_plus_one`.
- **G7-X18** â€” `test_enterprise_options_are_deterministic_and_store_real_ids`, `test_document_enterprise_prompt_uses_durable_mapping`, and `test_invalid_duplicate_and_late_command_answers`.
- **G7-X19** â€” `test_current_binding_materializes_for_future_document` and `test_stale_binding_is_preserved_but_not_materialized`.
- **G7-X20** â€” `test_document_enterprise_answer_materializes_item_only` and frozen `test_7_prompt_dispatch_and_answer_flow`.
- **G7-X21** â€” `test_document_enterprise_answer_materializes_item_only`.
- **G7-X22** â€” `test_missing_enterprise_rejects_and_rolls_back` and `test_persistence_transition_fails_closed_on_missing_prerequisite`.
- **G7-X23** â€” `test_disposable_writer_role_is_least_privilege` (supplier DML denied) and `test_v2_happy_path_supplier_match_and_replay` (lookup only).
- **G7-X24** â€” `test_enterprise_api_is_minimal_and_read_only` and `test_disposable_writer_role_is_least_privilege` (enterprise DML denied).
- **G7-X25** â€” frozen `test_10_cancel_command_and_unblocking_eligibility`, frozen `test_11_expiration_sweeper_and_unblocking_eligibility`, and `test_terminal_item_cannot_reserve_prompt`.
- **G7-X26** â€” `test_disposable_writer_role_is_least_privilege`.
- **G7-X27** â€” `test_known_income_skips_amount_requirement_in_decision_composition` and `test_income_guard_precedes_questions_and_persistence`.
- **G7-X28** â€” `test_income_ignored_releases_fifo_and_is_not_recovered`.
- **G7-X29** â€” `test_income_ignored_releases_fifo_and_is_not_recovered`, `test_ignored_constraint_rejects_wrong_reason`, and `test_terminal_item_cannot_reserve_prompt`.
- **G7-X30** â€” `test_income_guard_precedes_questions_and_persistence` plus the exact Gate 8 ownership assertion in this authoritative gate ledger; Gate 7 contains no final-notification call.
- **G7-X31** â€” `test_open_document_interaction_makes_command_busy` (including zero enterprise-list call and unchanged TTL).
- **G7-X32** â€” `test_both_open_protocols_use_shared_counter_lock` and `test_simultaneous_command_and_document_prompt_exactly_one_owner`.
- **G7-X33** â€” `test_simultaneous_command_and_document_prompt_exactly_one_owner`.
- **G7-X34** â€” `test_open_command_blocks_ready_then_answer_resumes` and `test_ready_claim_has_query_and_locked_command_barrier`.
- **G7-X35** â€” `test_open_command_blocks_ready_then_answer_resumes`.
- **G7-X36** â€” `test_clear_command_releases_ready_item_to_document_fallback`.
- **G7-X37** â€” `test_reserved_command_expiry_releases_without_binding_change`.
- **G7-X38** â€” `test_concurrent_same_event_command_answer_is_applied_once` and `test_invalid_duplicate_and_late_command_answers`.
- **G7-X39** â€” `test_outcome_unknown_answer_uses_stable_mapping_without_resend`.
- **G7-X40** â€” `test_new_normal_text_routes_without_unbound_event_crash` and `test_open_document_interaction_makes_command_busy`.
- **G7-X41** â€” `test_router_sends_numeric_answer_only_to_open_command`, `test_command_answer_does_not_reuse_user_answer`, and `test_router_records_late_answer_for_recent_expired_command`.

### Correction Pass 2 authoritative acceptance evidence

Every G7-X acceptance has direct executable evidence. Function names and evidence types are exact:

- **G7-X01** [DISPOSABLE POSTGRES] - `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X02** [DISPOSABLE POSTGRES] - `test_concurrent_same_key_serializes_before_financial_insert` and `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X03** [DISPOSABLE POSTGRES, UNIT] - `test_precommit_operational_error_is_retryable`, `test_deadline_exhaustion_starts_no_business_dml`, `test_post_race_zero_budget_starts_no_lookup_and_retries_known_rollback`, `test_post_race_lookup_uses_remaining_budget`, and `test_post_race_lookup_does_not_start_after_deadline`.
- **G7-X04** [DISPOSABLE POSTGRES, UNIT] - `test_v2_strict_invalid_shapes_are_rejected`, `test_invalid_v2_amount_is_rejected_before_hash_or_database`, and `test_v2_amount_rejects_noncanonical_values`.
- **G7-X05** [DISPOSABLE POSTGRES] - `test_flush_integrity_error_is_sanitized_rejection` and `test_missing_enterprise_rejects_and_rolls_back`.
- **G7-X06** [UNIT] - `test_orchestrator_settings_has_no_df_database_url`.
- **G7-X07** [UNIT] - `test_orchestrator_settings_has_no_df_database_url`.
- **G7-X08** [UNIT] - `test_writer_generic_handler_is_sanitized`, `test_writer_tls_validation_does_not_accept_substring_trick`, and `test_writer_requires_database_url_and_explicit_disposable_insecure_mode`.
- **G7-X09** [SECURITY PHYSICAL] - `test_disposable_writer_role_is_least_privilege`.
- **G7-X10** [DISPOSABLE POSTGRES] - `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X11** [DISPOSABLE POSTGRES] - `test_income_worker_path_has_zero_writer_supplier_prompt_or_notification` and `test_income_ignored_releases_fifo_and_is_not_recovered`.
- **G7-X12** [DISPOSABLE POSTGRES] - `test_v2_happy_path_supplier_match_and_replay` and `test_supplier_format_variants_share_one_canonical_replay`.
- **G7-X13** [DISPOSABLE POSTGRES] - `test_unknown_supplier_is_nullable_and_snapshot_preserved`.
- **G7-X14** [UNIT] - `test_duplicate_supplier_lookup_fails_closed_before_insert`.
- **G7-X15** [DISPOSABLE POSTGRES] - `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X16** [DISPOSABLE POSTGRES] - `test_command_selection_upserts_and_clear_deletes_binding`.
- **G7-X17** [DISPOSABLE POSTGRES, UNIT] - `test_command_selection_upserts_and_clear_deletes_binding` and `test_command_prompt_appends_clear_as_n_plus_one`.
- **G7-X18** [DISPOSABLE POSTGRES, UNIT] - `test_enterprise_options_are_deterministic_and_store_real_ids`, `test_document_enterprise_prompt_uses_durable_mapping`, and `test_invalid_duplicate_and_late_command_answers`.
- **G7-X19** [DISPOSABLE POSTGRES] - `test_current_binding_materializes_for_future_document` and `test_stale_binding_is_preserved_but_not_materialized`.
- **G7-X20** [DISPOSABLE POSTGRES] - `test_missing_binding_creates_enterprise_selection_and_blocks_fifo`.
- **G7-X21** [DISPOSABLE POSTGRES] - `test_document_enterprise_answer_materializes_item_only`.
- **G7-X22** [DISPOSABLE POSTGRES] - `test_stale_binding_is_preserved_but_not_materialized`, `test_missing_binding_creates_enterprise_selection_and_blocks_fifo`, and `test_persistence_transition_fails_closed_on_missing_prerequisite`.
- **G7-X23** [SECURITY PHYSICAL, DISPOSABLE POSTGRES] - `test_disposable_writer_role_is_least_privilege` and `test_v2_happy_path_supplier_match_and_replay`.
- **G7-X24** [SECURITY PHYSICAL, DISPOSABLE POSTGRES] - `test_enterprise_api_is_minimal_and_read_only` and `test_disposable_writer_role_is_least_privilege`.
- **G7-X25** [DISPOSABLE POSTGRES] - `test_10_cancel_command_and_unblocking_eligibility`, `test_11_expiration_sweeper_and_unblocking_eligibility`, and `test_terminal_item_cannot_reserve_prompt`.
- **G7-X26** [SECURITY PHYSICAL] - `test_disposable_writer_role_is_least_privilege`.
- **G7-X27** [DISPOSABLE POSTGRES] - `test_income_worker_path_has_zero_writer_supplier_prompt_or_notification`.
- **G7-X28** [DISPOSABLE POSTGRES] - `test_income_ignored_releases_fifo_and_is_not_recovered`.
- **G7-X29** [DISPOSABLE POSTGRES, MIGRATION] - `test_income_ignored_releases_fifo_and_is_not_recovered`, `test_ignored_constraint_rejects_wrong_reason`, `test_terminal_item_cannot_reserve_prompt`, and `test_gate7_migration_upgrade_and_previous_revision_round_trip`.
- **G7-X30** [DISPOSABLE POSTGRES] - `test_income_worker_path_has_zero_writer_supplier_prompt_or_notification`; Gate 7 itself remains notification-free, while Gate 8 now owns the implemented terminal informational outcome.
- **G7-X31** [REAL WEBHOOK, DISPOSABLE POSTGRES] - `test_real_webhook_enterprise_command_during_amount_prompt_is_busy` and `test_open_document_interaction_makes_command_busy`.
- **G7-X32** [DISPOSABLE POSTGRES] - `test_open_command_remains_owner_when_worker_requires_direction`.
- **G7-X33** [DISPOSABLE POSTGRES] - `test_simultaneous_command_and_document_prompt_exactly_one_owner`.
- **G7-X34** [DISPOSABLE POSTGRES] - `test_open_command_barrier_skips_conversation_without_mutating_ready_item` and `test_ready_claim_has_query_and_locked_command_barrier`.
- **G7-X35** [DISPOSABLE POSTGRES] - `test_open_command_barrier_skips_conversation_without_mutating_ready_item`.
- **G7-X36** [DISPOSABLE POSTGRES] - `test_clear_command_releases_ready_item_to_document_fallback`.
- **G7-X37** [DISPOSABLE POSTGRES] - `test_reserved_command_expiry_releases_without_binding_change`.
- **G7-X38** [REAL WEBHOOK, DISPOSABLE POSTGRES] - `test_real_webhook_duplicate_command_answer_has_one_business_effect`, `test_concurrent_same_event_command_answer_is_applied_once`, and `test_invalid_duplicate_and_late_command_answers`.
- **G7-X39** [DISPOSABLE POSTGRES] - `test_outcome_unknown_answer_uses_stable_mapping_without_resend`.
- **G7-X40** [REAL WEBHOOK] - `test_real_webhook_enterprise_command_during_amount_prompt_is_busy`.
- **G7-X41** [REAL WEBHOOK, DISPOSABLE POSTGRES] - `test_router_sends_numeric_answer_only_to_open_command`, `test_real_webhook_cancelled_command_answer_is_late_without_reopen`, `test_router_records_late_answer_for_recent_expired_command`, `test_real_webhook_duplicate_command_answer_has_one_business_effect`, and `test_real_webhook_duplicate_user_answer_has_one_applied_mutation`.

- [x] **G7-APPROVED = true**

### Implementation Verification Evidence

- Gate 7 Correction Pass 2 focused suite: **126 passed, 0 skipped, 0 failed, 0 errors**; the authoritative G7-X01 through G7-X41 map above records direct evidence and its evidence type.
- Frozen Gate 4 regression: **210 passed, 0 skipped, 0 failed, 0 errors**.
- Frozen Gate 5 regression: **63 passed, 0 skipped, 0 failed, 0 errors**.
- Frozen Gate 6 regression: **64 passed, 0 skipped, 0 failed, 0 errors**.
- Complete safe project suite: **565 passed, 0 skipped, 0 failed, 0 errors**.
- Static verification: **compileall PASS; Ruff PASS; mypy PASS; git diff --check PASS**.
- Test environment: **PostgreSQL 15 disposable only**; both migrations validated through physical upgrade/constraint/index/round-trip behavior.
- Database safety: **no persistent/staging/production/Supabase/remote database touched; disposable databases, roles, and container removed afterward**.
- Scope integrity: **`business_rules_evaluator.py` unchanged; WuzapiClient implementation unchanged; Gate 4 persistence semantics and Writer v1 behavior preserved; zero final notification and zero Gate 8 work**.
- Migrations: **CREATED for Platform and local DF MVP; execution outside disposable tests remains NOT AUTHORIZED**.
- Final governance: **Gate 7 APPROVED / COMPLETE**; implementation **COMPLETE**; verification **PASSED**; final review **APPROVED**; `G7-APPROVED = true`.

---

## GATE 8 — E2E

Status: **APPROVED / COMPLETE**. Product, architecture, plan, and implementation-HOLD contracts are approved; implementation is **COMPLETE**; verification is **PASSED**; Correction Pass 1 and Correction Pass 2 are **COMPLETE**; final approval review is **PASSED / APPROVED**. `G8-APPROVED = true`.

### Tasks
- [x] **G8-T01 P0** Define the exact terminal `ProcessingItem` -> final-notification mapping.
- [x] **G8-T02 P0** Implement durable idempotent final-notification intent.
- [x] **G8-T03 P0** Implement concurrency-safe reservation ensuring one logical notification owner.
- [x] **G8-T04 P0** Implement stable final outbound identity and WUZAPI dispatch.
- [x] **G8-T05 P0** Implement `ACKNOWLEDGED` and `OUTBOUND_OUTCOME_UNKNOWN` behavior with no blind resend.
- [x] **G8-T06 P0** Implement safe recovery of `RESERVED` notifications that have not begun outbound dispatch.
- [x] **G8-T07 P0** Integrate durable `COMPLETED` expense outcome with the frozen Gate 5 `format_success_message` implementation.
- [x] **G8-T08 P0** Integrate `IGNORED / INCOME_OUT_OF_SCOPE` with the exact approved informational message.
- [x] **G8-T09 P0** Integrate sanitized final notifications for `EXTRACTION_FAILED` and terminal `PERSISTENCE_FAILED`.
- [x] **G8-T10 P0** Enforce fail-closed final-notification eligibility so nonterminal or ambiguous business states can never emit false success/failure.
- [x] **G8-T11 P0** Implement Gate 8 E2E, replay, concurrency, outbound ambiguity, FIFO-independence, and correlation evidence for G8-X01 through G8-X12.
- [x] **G8-T12 P0** Complete Gate 8 regression/static verification/governance evidence.

### Frozen product contract
- `COMPLETED` is eligible only with durable Writer `COMMITTED`, expense direction, amount/date, a successful direct or reconciled persistence Execution, and a persisted committed record ID.
- `IGNORED / INCOME_OUT_OF_SCOPE` receives the approved informational message and preserves zero Writer business effect.
- `EXTRACTION_FAILED` receives one logical sanitized processing-failure notification based on terminal status; provider identity/details are never required or exposed.
- Terminal `PERSISTENCE_FAILED` receives one logical sanitized persistence-failure notification. `PERSISTING`, `PERSIST_RETRYABLE`, and `PERSIST_OUTCOME_UNKNOWN` receive no final success or failure notification.
- `FAILED` and `EXPIRED` receive no new Gate 8 notification. `CANCELLED` preserves its existing acknowledgement and receives no additional Gate 8 final notification.
- Business terminalization releases FIFO before final delivery; final notification transport neither blocks nor changes business state. Strict final-message ordering is not required.
- Scheduling isolation is mandatory: the existing `fifo_worker` runtime supervises an independent business FIFO loop and a single-concurrency final-notification loop/thread. Business claiming never awaits final WUZAPI I/O; notifier scans at most 100 candidates, uses independent short DB sessions, holds no connection/lock during send, and catches notifier failures without terminating business processing.
- Deterministic scheduling evidence must prove slow/timeout/exception/backlog notifier paths do not starve business claims, concurrent loops preserve business FIFO, shutdown after `DISPATCHED` yields UNKNOWN without resend, and shutdown at `RESERVED` preserves safe recovery.
- Local Gate 8 acceptance uses deterministic local/fake boundaries and disposable PostgreSQL 15; real cellphone/external WUZAPI/real client DB validation is later and separately authorized.

### Tests obrigatórios
- [x] **G8-X01** PIX → expense → grava → WhatsApp.
- [x] **G8-X02** PIX → income → `IGNORED / INCOME_OUT_OF_SCOPE` → zero gravação em `financial_records` → mensagem informativa expense-only idempotente → WhatsApp.
- [x] **G8-X03** Direction ambígua → pergunta → grava.
- [x] **G8-X04** Valor ausente → pergunta → grava.
- [x] **G8-X05** Data ausente → timestamp → grava.
- [x] **G8-X06** Cinco documentos → FIFO.
- [x] **G8-X07** Dois usuários simultâneos.
- [x] **G8-X08** Replay webhook.
- [x] **G8-X09** Gemini/extraction unavailable -> `EXTRACTION_FAILED` -> zero Writer business write -> one logical approved extraction-failure notification.
- [x] **G8-X10** Database transient/retryable -> no premature notification; eventual `COMPLETED` -> exactly one success; terminal `PERSISTENCE_FAILED` -> exactly one approved failure; `PERSIST_OUTCOME_UNKNOWN` -> no success/failure until reconciliation resolves it.
- [x] **G8-X11** Eligible terminal outcome -> durable dispatch intent -> outbound unavailable/ambiguous -> `OUTBOUND_OUTCOME_UNKNOWN` -> no blind resend and unchanged business state.
- [x] **G8-X12** Correlation ID reconstrói E2E.

### Actual Verified G8-X Evidence Matrix

| Acceptance | Exact passing test function(s) and actual file | Evidence category |
|---|---|---|
| G8-X01 | `test_g8_x01_pix_expense_commits_and_sends_one_success` — `tests/test_platform_gate8_e2e_disposable_postgres.py` | LOCAL E2E / DISPOSABLE POSTGRES |
| G8-X02 | `test_g8_x02_pix_income_is_ignored_and_sends_one_information_message` — E2E file; `test_real_webhook_income_runs_guard_with_physical_zero_writer_rows` — real-webhook file | REAL BUSINESS / REAL WEBHOOK / PHYSICAL ZERO-WRITER PROOF |
| G8-X03 | `test_g8_x03_ambiguous_direction_answer_commits_and_sends_success` — E2E file; `test_real_webhook_direction_clarification_reaches_writer_committed` — real-webhook file | SIGNED WEBHOOK / APPLIED ANSWER / ACTUAL WRITER COMMITTED |
| G8-X04 | `test_g8_x04_missing_amount_answer_commits_and_sends_success` — E2E file; `test_real_webhook_amount_clarification_reaches_writer_committed` — real-webhook file | SIGNED WEBHOOK / APPLIED ANSWER / ACTUAL WRITER COMMITTED |
| G8-X05 | `test_g8_x05_missing_date_uses_timestamp_and_sends_success` — E2E file; `test_success_formatter_adapter_reuses_frozen_gate5_formatter` — unit file | UNIT / LOCAL E2E / DISPOSABLE POSTGRES |
| G8-X06 | `test_g8_x06_five_documents_preserve_business_fifo_without_notification_barrier` — E2E file; `test_notification_backlog_does_not_starve_business_claims`, `test_business_and_notifier_loops_preserve_fifo_sequence` — unit file | LOCAL E2E / DISPOSABLE POSTGRES / SCHEDULING |
| G8-X07 | `test_slow_final_sender_does_not_delay_next_business_claim` — unit file | TWO USERS / BLOCKED REAL NOTIFIER / REAL BUSINESS CLAIM AND COMPLETION |
| G8-X08 | `test_g8_x08_original_webhook_replay_has_one_full_effect` — E2E file; `test_real_original_webhook_expense_replay_has_one_final_logical_outcome` — real-webhook file | ORIGINAL SIGNED WEBHOOK REPLAY / ACTUAL WRITER / DISPOSABLE POSTGRES |
| G8-X09 | `test_g8_x09_extraction_unavailable_sends_one_sanitized_failure` — E2E file; `test_final_user_messages_are_exact_and_sanitized` — unit file | FAILURE / LOCAL E2E / UNIT |
| G8-X10 | `test_g8_x10_actual_retryable_then_committed`, `test_g8_x10_actual_unknown_reconciles_committed`, `test_g8_x10_actual_writer_rejection_sends_failure` — E2E file; `test_retryable_and_persistence_outcome_unknown_create_no_intent` — disposable final-notifications file | ACTUAL PERSISTENCE / ACTUAL WRITER / RECONCILIATION |
| G8-X11 | `test_g8_x11_outbound_unknown_is_not_resent` — E2E file; `test_final_sender_timeout_does_not_stop_business_loop`, `test_notifier_exception_does_not_stop_business_loop` — unit file; `test_shutdown_after_dispatched_recovers_unknown_without_resend` — disposable final-notifications file | OUTBOUND AMBIGUITY / REAL RUNTIME RECOVERY / SCHEDULING |
| G8-X12 | `test_g8_x12_physical_correlation_chain` — E2E file | PHYSICAL PLATFORM + WRITER AUDIT CHAIN |

File aliases in this matrix: E2E file = `tests/test_platform_gate8_e2e_disposable_postgres.py`; real-webhook file = `tests/test_platform_gate8_real_webhook_disposable_postgres.py`; unit file = `tests/test_platform_gate8_final_notifications_unit.py`; disposable final-notifications file = `tests/test_platform_gate8_final_notifications_disposable_postgres.py`.

### Implementation Verification Evidence

- Gate 8 focused after Correction Pass 1: **46 passed, 0 skipped, 0 failed, 0 errors**.
- Frozen regressions: **Gate 4 210 passed; Gate 5 63 passed; Gate 6 64 passed; Gate 7 126 passed**, all with 0 skipped, 0 failed, and 0 errors.
- Complete project suite: **611 passed, 0 skipped, 0 failed, 0 errors**.
- Static verification: **compileall PASS; Ruff PASS; mypy PASS; git diff --check PASS**.
- Physical verification: **PostgreSQL 15 disposable only**; Platform, local Writer, and Gate 3 disposable profiles were local to the same disposable container and cleaned afterward.
- Correction hardening: reservation excludes existing shared ACK/UNKNOWN finalization keys, caller batch size is capped at 100, and deterministic 59/60-second grace evidence proves no pre-grace UNKNOWN and no resend.
- Corrected evidence: the four Gate 8 files now use signed original-document webhooks, the real extraction dispatcher boundary, database-backed FIFO claims, actual Gate 4/5/6/7 business/PersistenceService paths, the actual local Gate 7 Writer, physical `financial_records`/`write_ledger` queries, and actual notifier-runtime shutdown/restart paths.
- Correction Pass 2 restart evidence: `test_shutdown_after_dispatched_recovers_unknown_without_resend` starts the production notifier loop for the original durable dispatch, simulates process loss before finalization, then starts fresh notifier runtimes with fresh sessions at the injected 59- and 60-second clocks. The 59-second runtime produces no finalization/resend; the 60-second runtime produces exactly one UNKNOWN on the shared final key, no ACK, no resend, one total outbound attempt, and no business-state mutation. No recovery helper is invoked directly as the post-restart acceptance step.
- Scope: new final-notification service, `fifo_worker.py` scheduling integration, and exactly four Gate 8 test files; no frozen Gate 4–7 test file modified.
- Safety: **zero Gate 8 migrations**; no persistent/staging/production/Supabase/remote DB, external WUZAPI, Gemini, cellphone, or client database touched.
- Governance: Gate 8 **APPROVED / COMPLETE / PUSHED**; implementation **COMPLETE**; verification **PASSED**; Correction Pass 1 and Correction Pass 2 **COMPLETE**; final approval review **PASSED / APPROVED**; `G8-APPROVED = true`; Gate 9 contract is **CLOSED**, implementation, verification, repository closure, final review, and explicit user approval are **COMPLETE / APPROVED**, and `G9-APPROVED = true`; Gate 10 is **NOT STARTED**; Production Phase B is **NOT IMPLEMENTED**.

- [x] **G8-APPROVED = true**

---

## GATE 9 — Operação

Status: **APPROVED / COMPLETE / PUSHED**. Gate 8 is APPROVED / COMPLETE / PUSHED. Gate 9 correction code, host/disposable-reporting/physical verification, cleanup, repository-scope closure, repeated final application review, explicit user approval, and Git closure are complete. `G9-APPROVED = true`.

Read-only discovery findings:

- existing `executions` is sufficient for P0 checkpoint/correlation queries; no new Execution column or synthetic event is proposed;
- Transcription `usage_logs` is the populated attempt-level token/cost source of truth, including retries and nullable unknown usage;
- Platform `service_usage` has no runtime producer/consumer and cannot represent unknown usage because tokens are non-null with zero defaults;
- G9-D01 **CLOSED**: Transcription `usage_logs` is authoritative; successful, failed, and retried provider attempts count; unknown usage remains NULL and makes totals PARTIAL;
- G9-D02 **CLOSED**: G9-T02 reports from authoritative component-owned sources; Gate 9 does not populate Platform `service_usage`, create a projection/sync job, or add synthetic Writer/WUZAPI usage rows;
- migration decision: **NO GATE 9 MIGRATION REQUIRED**;
- token aggregation is per ProcessingItem/Transcription request, includes failed/retried attempts, separates input/output/provider total, and exposes unknown attempt counts without zero substitution;
- G9-D03 **CLOSED**: E2E business duration uses only unequivocal durable terminal timestamps; it includes human wait, excludes final-notification delivery, never falls back to `ProcessingItem.updated_at`, and reports `duration_available=false` when no authoritative terminal timestamp exists;
- P0 operations use bounded read-only CLI/queries, not a web dashboard or external monitoring vendor;
- backup/restore scope is Platform PostgreSQL 15 only and disposable during Gate 9 verification;
- Gate 9 P0 records local Docker `json-file` retention guidance (`10m`, 5 files) but leaves `docker-compose.yml` unchanged; VPS enforcement belongs to Gate 10;
- implementation-plan safety corrections are incorporated: PostgreSQL-enforced read-only reporting, 24-hour default/31-day maximum UTC windows, 100-default/1,000-maximum detailed rows, deterministic truncation, dedicated no-fallback configuration, strict loopback/PostgreSQL 15 checks, tool-owned backup containment, and invocation-owned restore/cleanup;
- the exact source/documentation/test inventory was preserved; no shared helper, speculative index, migration, model, dependency, Docker/runtime configuration, Gate 10, or P1 work was added;
- Gate 9 requires no VPS, real external service, client DB, production Phase B, or Gate 10 work;
- authoritative discovery/decision record: `.agents/IMPLEMENTATION_PLAN_GATE_9.md`;
- unresolved product decisions: NONE; identified safety ambiguities and first-final-review code blockers: CORRECTED AND FRESHLY VERIFIED; repeated final application review: PASSED / APPROVED; explicit user approval: GRANTED on 2026-08-15.

### Tasks
- [x] **G9-T01 P0** executions.
- [x] **G9-T02 P0** service_usage.
- [x] **G9-T03 P0** Tokens.
- [x] **G9-T04 P0** Duração.
- [x] **G9-T05 P0** Error codes.
- [x] **G9-T06 P0** Queries/dashboard operacional.
- [x] **G9-T07 P0** Backup Platform DB.
- [x] **G9-T08 P0** Restore runbook/script.
- [x] **G9-T09 P0** Retenção de logs.
- [x] **G9-T10 P0** Runbook de incidentes.

### Tests
- [x] **G9-X01** Tokens por documento.
- [x] **G9-X02** Tokens por organização.
- [x] **G9-X03** Duração E2E.
- [x] **G9-X04** Falha localizável por correlation ID.
- [x] **G9-X05** Backup gera artefato válido.
- [x] **G9-X06** Restore em ambiente limpo.

- [x] **G9-APPROVED = true**

Implementation and verification evidence (2026-08-15): Gate 9 focused **62 passed**; frozen Gate 4/5/6/7/8 regressions **210/63/64/126/46 passed**; complete safe host suite **671 passed** plus the two separately executed physical backup/restore tests, for **673 combined passed**. All recorded passing suites had 0 skipped, 0 failed, and 0 errors. compileall, Ruff, Gate 9-targeted mypy, `git diff --check`, credential/DSN leakage inspection, repository-scope inspection, and separate authorized-untracked-file trailing-whitespace inspection passed. Physical evidence used only invocation-owned tmpfs PostgreSQL 15 resources; database/container/image/artifact cleanup completed and no persistent/staging/production/client/Supabase/remote/VPS database or real external service was touched. The final pytest-created `gate9-pytest-tmp/` residue was removed by a true elevated Windows Administrator session after exact-path validation, ownership/access correction, and symlink removal; independent post-cleanup verification confirmed `Test-Path=false` and no Git-status residue. No Gate 9 migration, dependency, lockfile, Docker/runtime change, Gate 10 work, or Production Phase B implementation occurred.

Final-review correction evidence (2026-08-15): the four P1 code blockers are corrected in the existing authorized files. The correction unit suite passed **64 tests**, the affected disposable PostgreSQL 15 reporting integrations passed **4 tests**, and fresh physical G9-X05/X06 re-verification passed **2 tests**, all with 0 skipped, 0 failed, and 0 errors. compileall, Ruff, and targeted mypy passed. The physical runner shared the disposable PostgreSQL 15 server's network namespace, satisfying resolver and connected-server loopback checks while exercising real custom-format backup, catalog/hash validation, clean generated restore, row-identity comparison, exact target cleanup, and missing-authorization rejection. The repeated final review additionally verified bounded cross-batch organization aggregation/detail memory and complete correlation child/Writer collection coverage beyond the first ProcessingItem page. Every temporary database, container, runner image/definition, artifact, and test directory was removed. Final review result: **PASSED / APPROVED**. The user explicitly approved Gate 9 on 2026-08-15; `G9-APPROVED = true`.

---

## GATE 10 — Segurança / Release

### Tasks
- [ ] **G10-T01 P0** CPF/CNPJ reais.
- [ ] **G10-T02 P0** Secrets novos de produção.
- [ ] **G10-T03 P0** HTTPS.
- [ ] **G10-T04 P0** Fechar portas/serviços internos.
- [ ] **G10-T05 P0** Restringir WUZAPI admin.
- [ ] **G10-T06 P0** Restringir SSH.
- [ ] **G10-T07 P0** Rate limits.
- [ ] **G10-T08 P0** Dependency audit.
- [ ] **G10-T09 P0** Imagens Docker pinadas.
- [ ] **G10-T10 P0** Container hardening.
- [ ] **G10-T11 P0** Confirmar media retention WUZAPI.
- [ ] **G10-T12 P0** Sanitização de logs.
- [ ] **G10-T13 P0** Restore recente.
- [ ] **G10-T14 P0** Rollback documentado.

### Security Tests
- [ ] **G10-X01** Webhook sem assinatura.
- [ ] **G10-X02** Token interno inválido.
- [ ] **G10-X03** Brute force cadastro.
- [ ] **G10-X04** Arquivo oversized.
- [ ] **G10-X05** MIME spoof.
- [ ] **G10-X06** Payload malformado.
- [ ] **G10-X07** Replay.
- [ ] **G10-X08** Race condition FIFO.
- [ ] **G10-X09** Race condition idempotency.
- [ ] **G10-X10** Busca de API keys/tokens em logs.
- [ ] **G10-X11** Busca de secrets no Git history da release.
- [ ] **G10-X12** Platform DB não público.
- [ ] **G10-X13** Database Writer não público.
- [ ] **G10-X14** DB user sem DROP/ALTER indevidos.
- [ ] **G10-X15** Temporários removidos.
- [ ] **G10-X16** WUZAPI segue política de mídia.
- [ ] **G10-X17** Restart abrupto não duplica gravação.
- [ ] **G10-X18** Restore validado.

### Release Checklist
- [ ] Schema DF aprovado.
- [ ] CPF/CNPJ reais.
- [ ] E2E staging aprovado.
- [ ] Segurança aprovada.
- [ ] Backup/restore aprovado.
- [ ] Runbook operacional.
- [ ] Runbook rollback.
- [ ] Tag de release.
- [ ] Changelog.
- [ ] **G10-APPROVED / FASE 1 RELEASED**
