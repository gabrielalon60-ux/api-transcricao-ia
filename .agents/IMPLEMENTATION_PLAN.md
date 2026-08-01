# Implementation Plan — Plataforma WhatsApp DF Holding — Fase 1


## GATE 0 — Congelamento do desenho

**Entregas:** PRD, ADR, contratos v1, estados, autenticação interna, estratégia migrations, ambientes.

**Aprovação:** nenhuma implementação de negócio antes de contratos/limites de responsabilidade estarem coerentes.

## FASE/GATE 1 — Fundação

Implementar:

- monorepo;
- quatro apps;
- packages `contracts/security/observability`;
- Platform PostgreSQL;
- migrations;
- models iniciais;
- health/readiness;
- correlation middleware;
- logs estruturados;
- Compose local.

Gate: clone limpo sobe, migrations passam, DB persiste, sem secrets.

## FASE/GATE 2 — Orquestrador / identidade / cadastro

Implementar:

- webhook WUZAPI;
- auth/assinatura;
- normalização;
- idempotência;
- instance→organization→bot;
- users;
- `/cadastro`;
- hash/HMAC;
- rate limit;
- outbound WUZAPI.

Gate: desconhecido não chama IA; cadastro/roteamento/replay corretos.

## FASE/GATE 3 — Transcrição IA

Adaptar 1.0 para serviço interno:

- contrato;
- auth;
- validação image/PDF;
- seis tipos de documento;
- normalized_data;
- flags de qualidade;
- usage/tokens;
- timeout/retries;
- cleanup;
- SHA-256;
- teste WUZAPI retention.

Gate: fixtures dos seis tipos, cleanup, tokens, falhas controladas.

## FASE/GATE 4 — Extração paralela + fila FIFO

Implementar:

- processing_items;
- sequence transacional;
- estados;
- concorrência IA;
- READY;
- worker FIFO;
- um ACTIVE por conversa;
- lock transacional;
- limite de fila;
- recovery após restart.

Gate obrigatório: cinco arquivos, extração fora de ordem, execução 1→5, sem concorrência indevida.

## FASE/GATE 5 — BOT DF / regras financeiras

Implementar:

- máquina de estados;
- amount;
- date/fallback;
- lista CPF/CNPJ;
- payer/receiver;
- expense/income/unknown/ambiguous;
- mensagem final.

Gate: regras determinísticas passam e item completo não pede confirmação.

## FASE/GATE 6 — Interação

Implementar:

- WAITING_USER_INPUT;
- pergunta direction;
- pergunta amount;
- parse resposta;
- TTL 1h;
- expiração;
- novo arquivo durante espera;
- continuação automática.

Gate: terceiro de cinco pausa, resolve/expira e fila continua.

## FASE/GATE 7 — Database Writer

Pré-requisito: schema DF.

Implementar:

- contrato;
- secret só no Writer;
- TLS;
- usuário mínimo;
- transação;
- idempotency key;
- timeout/retries;
- sanitização.

Gate: retry não duplica e credencial não vaza.

## FASE/GATE 8 — E2E

Integrar fluxo completo e mensagens finais.

Gate: celular real → DB DF → resposta real, auditável por correlation ID.

## FASE/GATE 9 — Operação / observabilidade

Implementar:

- executions;
- service_usage;
- custos/tokens;
- métricas;
- backup/restore;
- runbooks.

Gate: investigar erro e restaurar banco em ambiente limpo.

## FASE/GATE 10 — Segurança / Release Candidate

P0:

- HTTPS;
- fechar serviços internos;
- restringir WUZAPI admin/SSH;
- webhook HMAC;
- rate limits;
- secrets novos;
- DB Platform privado;
- DB DF TLS/min privilege;
- upload validation;
- logs sanitizados;
- versões pinadas;
- dependências auditadas;
- hardening de containers;
- backup/restore;
- rollback.

Gate: staging E2E + testes de segurança + restore + checklist de produção.

---

# 20. Estratégia de deploy

## Local
Docker Compose + PostgreSQL local.

## Staging
Topologia equivalente à produção, com WhatsApp/DB/secrets de teste.

## Produção — Dokploy/VPS

```text
Traefik / HTTPS
       │
       ├── WUZAPI (admin restrito)
       └── edge necessário

Rede interna:
Orchestrator
Bot DF
Transcription
Database Writer
Platform PostgreSQL
```

- migrations forward-only em produção;
- feature flags para regras futuras;
- cada gate gera checkpoint/tag;
- rollback documentado.
