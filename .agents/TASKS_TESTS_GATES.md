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

### Tasks
- [ ] **G3-T01 P0** Adaptar serviço 1.0 ao contrato interno.
- [ ] **G3-T02 P0** Auth interna.
- [ ] **G3-T03 P0** Validar imagem/PDF.
- [ ] **G3-T04 P0** Limite de tamanho configurável.
- [ ] **G3-T05 P0** Validar assinatura/MIME real.
- [ ] **G3-T06 P0** Nota fiscal.
- [ ] **G3-T07 P0** PIX.
- [ ] **G3-T08 P0** Boleto.
- [ ] **G3-T09 P0** Cupom fiscal.
- [ ] **G3-T10 P0** Pedido.
- [ ] **G3-T11 P0** Orçamento.
- [ ] **G3-T12 P0** raw_extraction.
- [ ] **G3-T13 P0** normalized_data.
- [ ] **G3-T14 P0** quality_flags.
- [ ] **G3-T15 P0** usage/tokens.
- [ ] **G3-T16 P0** SHA-256.
- [ ] **G3-T17 P0** Cleanup.
- [ ] **G3-T18 P0** Timeout.
- [ ] **G3-T19 P0** 2 retries técnicos.
- [ ] **G3-T20 P0** Verificar `wuzapi/files`.

### Tests
- [ ] **G3-X01** Fixture NF.
- [ ] **G3-X02** Fixture PIX.
- [ ] **G3-X03** Fixture boleto.
- [ ] **G3-X04** Fixture cupom.
- [ ] **G3-X05** Fixture pedido.
- [ ] **G3-X06** Fixture orçamento.
- [ ] **G3-X07** Arquivo inválido.
- [ ] **G3-X08** Arquivo oversized.
- [ ] **G3-X09** MIME spoof.
- [ ] **G3-X10** Timeout → retry.
- [ ] **G3-X11** Falha definitiva → cleanup.
- [ ] **G3-X12** Sucesso → cleanup.
- [ ] **G3-X13** Tokens persistidos.
- [ ] **G3-X14** Binário/base64 ausente dos logs.

- [ ] **G3-APPROVED**

---

## GATE 4 — Fila persistente

### Tasks
- [ ] **G4-T01 P0** processing_items.
- [ ] **G4-T02 P0** Sequence por conversa.
- [ ] **G4-T03 P0** Estados.
- [ ] **G4-T04 P0** Extração paralela limitada.
- [ ] **G4-T05 P0** READY após extração.
- [ ] **G4-T06 P0** Worker FIFO.
- [ ] **G4-T07 P0** Um ACTIVE por conversa.
- [ ] **G4-T08 P0** Lock transacional.
- [ ] **G4-T09 P0** MAX_QUEUE configurável.
- [ ] **G4-T10 P0** Recovery após restart.
- [ ] **G4-T11 P0** Falha libera fila.

### Tests
- [ ] **G4-X01** Cinco arquivos recebem sequência 1..5.
- [ ] **G4-X02** IA termina 3,1,5,2,4 → negócio executa 1..5.
- [ ] **G4-X03** Dois workers não pegam mesmo item.
- [ ] **G4-X04** Não existem dois ACTIVE na conversa.
- [ ] **G4-X05** Usuário A não bloqueia B.
- [ ] **G4-X06** Restart preserva READY.
- [ ] **G4-X07** Restart de ACTIVE possui recovery definido.
- [ ] **G4-X08** Fila cheia → zero Gemini para excedente.
- [ ] **G4-X09** EXTRACTION_FAILED #1 libera #2.

- [ ] **G4-APPROVED**

---

## GATE 5 — Regras BOT DF

### Tasks
- [ ] **G5-T01 P0** Máquina de estados.
- [ ] **G5-T02 P0** amount > 0.
- [ ] **G5-T03 P0** document_date.
- [ ] **G5-T04 P0** fallback timestamp.
- [ ] **G5-T05 P0** date_source.
- [ ] **G5-T06 P0** Lista CPF/CNPJ placeholder.
- [ ] **G5-T07 P0** payer DF → expense.
- [ ] **G5-T08 P0** receiver DF → income.
- [ ] **G5-T09 P0** ambos → ambiguous.
- [ ] **G5-T10 P0** nenhum → unknown.
- [ ] **G5-T11 P0** Mensagem final.

### Tests
- [ ] **G5-X01** DF payer → expense.
- [ ] **G5-X02** DF receiver → income.
- [ ] **G5-X03** Ambos → não grava automaticamente.
- [ ] **G5-X04** Nenhum → não grava automaticamente.
- [ ] **G5-X05** amount 0 → não grava.
- [ ] **G5-X06** amount ausente → pergunta futura.
- [ ] **G5-X07** Data do documento usada.
- [ ] **G5-X08** Data ausente → timestamp.
- [ ] **G5-X09** Orçamento sem data → timestamp.
- [ ] **G5-X10** Item completo não pede confirmação.

- [ ] **G5-APPROVED**

---

## GATE 6 — Conversação

### Tasks
- [ ] **G6-T01 P0** WAITING_USER_INPUT.
- [ ] **G6-T02 P0** Pergunta direction.
- [ ] **G6-T03 P0** Pergunta amount.
- [ ] **G6-T04 P0** Parse 1/2.
- [ ] **G6-T05 P0** Parse valor pt-BR.
- [ ] **G6-T06 P0** Uma pergunta por conversa.
- [ ] **G6-T07 P0** TTL 1h.
- [ ] **G6-T08 P0** EXPIRED.
- [ ] **G6-T09 P0** Novo arquivo durante espera.
- [ ] **G6-T10 P0** Continuar após resolução.
- [ ] **G6-T11 P0** Continuar após expiração.

### Tests
- [ ] **G6-X01** Terceiro de cinco pergunta; #4/#5 aguardam.
- [ ] **G6-X02** `1` resolve direction do item ativo.
- [ ] **G6-X03** `2` resolve direction do item ativo.
- [ ] **G6-X04** `1.200,50` normaliza corretamente.
- [ ] **G6-X05** Resposta inválida mantém pergunta.
- [ ] **G6-X06** Novo arquivo durante pendência vira READY.
- [ ] **G6-X07** Expira em 1h.
- [ ] **G6-X08** Expiração libera próximo.
- [ ] **G6-X09** Item expirado exige reenvio.
- [ ] **G6-X10** Máximo um WAITING por conversa.

- [ ] **G6-APPROVED**

---

## GATE 7 — Database Writer

### Tasks
- [ ] **G7-T01 P0** Fechar schema DF.
- [ ] **G7-T02 P0** Contrato write.
- [ ] **G7-T03 P0** Secret da conexão.
- [ ] **G7-T04 P0** TLS.
- [ ] **G7-T05 P0** Usuário DB mínimo.
- [ ] **G7-T06 P0** Validação request.
- [ ] **G7-T07 P0** Transação.
- [ ] **G7-T08 P0** Idempotency key.
- [ ] **G7-T09 P0** Timeout.
- [ ] **G7-T10 P0** Retry técnico.
- [ ] **G7-T11 P0** Sanitização de erros.
- [ ] **G7-T12 P0** Return record ID.

### Tests
- [ ] **G7-X01** Write happy path.
- [ ] **G7-X02** Mesma idempotency key → um registro.
- [ ] **G7-X03** Timeout/retry sem duplicidade.
- [ ] **G7-X04** Campo inválido → sem retry.
- [ ] **G7-X05** Falha parcial → rollback.
- [ ] **G7-X06** BOT não possui DB URL DF.
- [ ] **G7-X07** Orchestrator não possui DB URL DF.
- [ ] **G7-X08** Credencial ausente de logs.
- [ ] **G7-X09** Usuário DB não consegue operação indevida.

- [ ] **G7-APPROVED**

---

## GATE 8 — E2E

### Tests obrigatórios
- [ ] **G8-X01** PIX → expense → grava → WhatsApp.
- [ ] **G8-X02** PIX → income → grava → WhatsApp.
- [ ] **G8-X03** Direction ambígua → pergunta → grava.
- [ ] **G8-X04** Valor ausente → pergunta → grava.
- [ ] **G8-X05** Data ausente → timestamp → grava.
- [ ] **G8-X06** Cinco documentos → FIFO.
- [ ] **G8-X07** Dois usuários simultâneos.
- [ ] **G8-X08** Replay webhook.
- [ ] **G8-X09** Gemini indisponível.
- [ ] **G8-X10** DB indisponível.
- [ ] **G8-X11** WUZAPI outbound indisponível.
- [ ] **G8-X12** Correlation ID reconstrói E2E.

- [ ] **G8-APPROVED**

---

## GATE 9 — Operação

### Tasks
- [ ] **G9-T01 P0** executions.
- [ ] **G9-T02 P0** service_usage.
- [ ] **G9-T03 P0** Tokens.
- [ ] **G9-T04 P0** Duração.
- [ ] **G9-T05 P0** Error codes.
- [ ] **G9-T06 P0** Queries/dashboard operacional.
- [ ] **G9-T07 P0** Backup Platform DB.
- [ ] **G9-T08 P0** Restore runbook/script.
- [ ] **G9-T09 P0** Retenção de logs.
- [ ] **G9-T10 P0** Runbook de incidentes.

### Tests
- [ ] **G9-X01** Tokens por documento.
- [ ] **G9-X02** Tokens por organização.
- [ ] **G9-X03** Duração E2E.
- [ ] **G9-X04** Falha localizável por correlation ID.
- [ ] **G9-X05** Backup gera artefato válido.
- [ ] **G9-X06** Restore em ambiente limpo.

- [ ] **G9-APPROVED**

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
