# PRD — Plataforma WhatsApp DF Holding — Fase 1

**Status:** Draft para aprovação  
**Objetivo:** especificar a primeira fase da nova arquitetura WUZAPI → Orquestrador → BOT DF Holding → Serviços especializados → Banco DF Holding → resposta via WhatsApp.

> Princípio central: **WUZAPI transporta; Orquestrador roteia; BOT aplica regra de negócio; serviços especializados executam.**

---

## 1. Contexto

A solução atual de Transcrição IA já foi validada ponta a ponta com WUZAPI, FastAPI, Gemini e PostgreSQL/Supabase. A próxima evolução transforma essa integração em uma plataforma capaz de atender múltiplas empresas, múltiplas instâncias WhatsApp e múltiplos serviços, sem acoplar regras de negócio ao WUZAPI ou ao serviço de transcrição.

Nesta Fase 1 será implementado apenas o fluxo da **DF Holding**, deixando a arquitetura preparada para novos BOTs e novos serviços.

### Fluxo alvo

```text
WhatsApp
   ↓
WUZAPI
   ↓
ORQUESTRADOR
   ↓
identifica a instância receptora
   ↓
identifica organização + BOT
   ↓
BOT DF HOLDING
   ↓
SERVIÇO TRANSCRIÇÃO IA
   ↓
resultado estruturado
   ↓
FILA FIFO DA CONVERSA
   ↓
BOT DF HOLDING
   ├─ valida
   ├─ pergunta ao usuário quando necessário
   └─ solicita gravação
         ↓
SERVIÇO DE GRAVAÇÃO BD
         ↓
Banco DF Holding
         ↓
BOT DF HOLDING
         ↓
ORQUESTRADOR
         ↓
WUZAPI
         ↓
WhatsApp
```

---

## 2. Objetivos da Fase 1

1. Receber imagem ou PDF pelo WhatsApp.
2. Identificar a empresa pelo número/instância WhatsApp que recebeu a mensagem.
3. Garantir que cada instância esteja associada a exatamente um BOT.
4. Autorizar apenas remetentes cadastrados.
5. Permitir cadastro simples por `/cadastro SENHA`.
6. Transcrever todo arquivo recebido antes de colocá-lo na fila de negócio.
7. Descartar o arquivo original após a transcrição ou falha definitiva.
8. Manter apenas os dados extraídos/normalizados na fila.
9. Processar os lançamentos em FIFO por conversa.
10. Permitir apenas uma interação ativa por conversa.
11. Inferir `Despesa` ou `Entrada` usando CPF/CNPJ configurados da DF Holding.
12. Perguntar ao usuário somente quando faltar informação obrigatória ou houver incerteza relevante.
13. Gravar automaticamente quando o lançamento estiver completo.
14. Retornar confirmação final via WhatsApp.
15. Registrar auditoria completa, consumo de tokens, duração, erros e correlação entre serviços.
16. Isolar credenciais e regras de cada componente.
17. Deixar o desenho extensível para novos BOTs, instâncias e serviços.

---

## 3. Fora do escopo

- Consulta em banco pelo WhatsApp.
- Relatórios.
- Áudio.
- Texto livre como comando de negócio, exceto cadastro e respostas de confirmação.
- Deduplicação do conteúdo de documentos.
- Revisão humana em painel.
- Front administrativo da plataforma.
- Cadastro automatizado de novas instâncias WUZAPI.
- Secret manager multi-tenant.
- Múltiplos BOTs por instância.
- Armazenamento permanente de imagem/PDF.
- BOTs de outras empresas.
- Schema financeiro final da DF Holding.
- Cobrança/faturamento por cliente.

---

## 4. Entidades e conceitos

### Organização
Empresa atendida pela plataforma. Na Fase 1: `DF Holding`.

### Instância
Instância/número conectado ao WUZAPI. Uma organização pode ter uma ou mais instâncias.

### BOT
Componente responsável pelas regras de negócio da organização.

### Usuário
Número remetente autorizado.

### Conversa
Contexto operacional definido por:

```text
organization_id + instance_id + sender_phone
```

A fila sequencial e a interação ativa são controladas por conversa.

### Evento
Mensagem recebida do WUZAPI.

### Item de processamento
Documento acompanhado desde o recebimento até gravação, falha ou expiração.

---

# 5. Responsabilidade dos componentes

## 5.1 WUZAPI — transporte

Responsável por:

- manter conexão com WhatsApp;
- receber mensagens;
- disponibilizar a mídia ao pipeline;
- enviar evento ao Orquestrador;
- enviar respostas solicitadas pelo Orquestrador.

Não deve:

- decidir organização;
- aplicar regra DF;
- chamar Gemini diretamente;
- gravar no banco DF.

## 5.2 Orquestrador — roteamento e controle de entrada/saída

Responsável por:

- validar/autenticar webhook WUZAPI;
- normalizar payload;
- gerar/propagar `correlation_id`;
- aplicar idempotência;
- identificar instância;
- resolver organização;
- resolver BOT;
- identificar remetente;
- controlar fluxo de cadastro;
- encaminhar ao BOT correto;
- devolver respostas ao WUZAPI;
- registrar eventos/falhas de roteamento.

Não deve:

- interpretar documento;
- decidir `Despesa`/`Entrada`;
- conhecer o schema financeiro da DF;
- possuir credencial do banco DF.

## 5.3 BOT DF Holding — coordenador do negócio

Responsável por:

- iniciar extração;
- controlar fila FIFO;
- validar dados;
- aplicar regras DF;
- decidir `expense`/`income`;
- controlar perguntas ao usuário;
- controlar expiração;
- solicitar persistência;
- gerar mensagem final.

O BOT é o **dono do processo de negócio**.

## 5.4 Serviço Transcrição IA — execução especializada

Responsável por:

- validar arquivo;
- processar tipos permitidos;
- chamar Gemini;
- identificar tipo de documento;
- extrair e normalizar dados;
- registrar tokens, latência, modelo e provedor;
- retornar estrutura ao BOT;
- descartar arquivo após sucesso ou falha definitiva.

Não deve:

- decidir organização;
- decidir autorização;
- decidir banco de destino;
- carregar regra específica da DF quando depender de configuração do BOT.

## 5.5 Database Writer — persistência especializada

Responsável por:

- receber operação autorizada;
- resolver internamente credencial DF;
- validar contrato;
- acessar banco DF;
- executar transação;
- garantir idempotência da escrita;
- retornar `record_id`/status;
- nunca expor credenciais em response/log.

Na Fase 1 a credencial da DF fica como **secret/env do Database Writer**, não no request.

## 5.6 PostgreSQL da Plataforma

Banco separado do banco financeiro da DF.

Armazena:

- organizações;
- instâncias;
- BOTs;
- usuários;
- hash da senha de cadastro;
- eventos;
- itens/fila;
- interações;
- execuções;
- consumo/tokens;
- auditoria.

---

# 6. Regras de negócio

## RN-001 — Organização pelo número/instância que recebe

```text
instance_id → organization_id → bot_id
```

O remetente não determina a empresa.

## RN-002 — Uma organização pode ter múltiplas instâncias

Todas podem apontar ao mesmo BOT.

## RN-003 — Uma instância aponta para exatamente um BOT

`instances.bot_id` é obrigatório.

## RN-004 — Um telefone pertence a somente uma organização

O telefone deve ser normalizado antes da comparação.

Se já estiver cadastrado na organização A, não poderá ser cadastrado na B.

## RN-005 — Desconhecido não gera custo de IA

Se não cadastrado e enviar imagem/PDF:

```text
Este número ainda não está cadastrado.

Para habilitar o acesso, envie:
/cadastro SUA_SENHA
```

Nenhuma chamada ao Gemini deve ocorrer.

## RN-006 — Cadastro por senha fixa

```text
/cadastro MinhaSenha
```

Regras:

- senha específica da organização;
- armazenar somente hash/HMAC;
- nunca registrar senha em logs;
- aplicar limite de tentativas;
- cadastro vincula telefone à organização da instância receptora.

Resposta de sucesso:

```text
✅ Cadastro realizado com sucesso.

Você já pode enviar seus comprovantes.
```

Senha fixa é decisão temporária da Fase 1.

## RN-007 — Entradas aceitas

- imagem;
- PDF.

Texto somente para:

- `/cadastro`;
- resposta à pergunta ativa.

## RN-008 — Documentos suportados

- Nota fiscal;
- Comprovante PIX;
- Boleto;
- Cupom fiscal;
- Pedido;
- Orçamento.

Devem seguir as regras já existentes na Transcrição IA 1.0.

## RN-009 — Todo documento suportado representa pedido de lançamento

Na Fase 1, mesmo orçamento/pedido/boleto enviados são interpretados como pedido explícito de registro.

## RN-010 — Campos mínimos

```text
amount
transaction_date
direction
```

`direction ∈ {expense, income}`.

`amount > 0`.

## RN-011 — Data

Se existir data válida no documento:

```text
transaction_date = document_date
date_source = DOCUMENT
```

Se não existir:

```text
transaction_date = timestamp da mensagem
date_source = MESSAGE_TIMESTAMP
```

Na Fase 1 não se pergunta data ao usuário.

## RN-012 — Lista CPF/CNPJ DF

Inicialmente placeholders:

```text
CNPJ_1 = 00.000.000/0000-00
CNPJ_2 = 11.111.111/1111-11
CPF_1  = 000.000.000-00
CPF_2  = 111.111.111-11
```

Substituir antes de produção.

## RN-013 — Direction

```text
DF como PAGADOR e não como RECEBEDOR → expense
DF como RECEBEDOR e não como PAGADOR → income
DF nos dois lados → ambiguous → perguntar
DF em nenhum lado/dados insuficientes → unknown → perguntar
```

## RN-014 — Valor ausente/incerto

Se `amount` estiver ausente, inválido ou marcado como incerto:

```text
Qual é o valor deste lançamento?
```

Nunca inventar valor.

## RN-015 — Confiança/qualidade

O serviço de transcrição não deve fabricar probabilidade.

Contrato admite:

```text
needs_confirmation
quality_flags
confidence
```

O threshold final de baixa confiança será calibrado em testes.

## RN-016 — Sem confirmação final obrigatória

Com `amount`, `transaction_date` e `direction` válidos, grava imediatamente.

## RN-017 — Resposta final

Despesa:

```text
✅ Gravado com sucesso.

Despesa de R$ 1.200,00 realizada em 29/07/2026.
```

Entrada:

```text
✅ Gravado com sucesso.

Entrada de R$ 1.200,00 realizada em 29/07/2026.
```

---

# 7. Extração antecipada e descarte

## RN-018 — Extração antes da fila de negócio

Todos os arquivos são extraídos o quanto antes.

```text
Arquivo A → Transcrição → dados A ┐
Arquivo B → Transcrição → dados B ├→ fila de negócio
Arquivo C → Transcrição → dados C ┘
```

A fila de negócio contém dados, não arquivos.

## RN-019 — Arquivo não é persistido pela aplicação

Pode existir apenas em memória, `/tmp` ou referência transitória durante a extração/retry.

Depois de sucesso ou falha definitiva, deve ser removido.

Não persistir em Platform DB/object storage.

### Gate obrigatório

Verificar o comportamento do próprio WUZAPI. Se ele persistir mídia em `wuzapi/files`, configurar política de limpeza/retenção.

## RN-020 — Dados mantidos

Guardar:

- document_type;
- raw_extraction;
- normalized_data;
- quality_flags;
- campos finais;
- usage/tokens;
- metadados mínimos.

Metadados permitidos:

```text
mime_type
file_size
original_filename (quando disponível)
sha256
```

SHA-256 não bloqueia duplicidade na Fase 1.

---

# 8. Fila e concorrência

## RN-021 — FIFO por conversa

Chave:

```text
organization_id + instance_id + user_id
```

A sequência é atribuída no recebimento, não na conclusão da IA.

## RN-022 — Extrações podem ser paralelas

Com limite configurável.

## RN-023 — Ordem de negócio respeita recebimento

Mesmo que C termine IA antes de A/B:

```text
A → B → C
```

## RN-024 — Um item de negócio ativo por conversa

No máximo um item em:

```text
ACTIVE
VALIDATING
WAITING_USER_INPUT
PERSISTING
```

## RN-025 — Uma pergunta ativa por conversa

O usuário pode responder simplesmente:

```text
1
```

sem códigos de documento.

## RN-026 — Nova mídia durante pergunta ativa

A nova mídia:

1. é aceita;
2. recebe sequência;
3. é transcrita;
4. tem arquivo descartado;
5. fica READY aguardando.

Ela não substitui a pergunta atual.

## RN-027 — Expiração

TTL:

```text
1 hora
```

Após:

```text
WAITING_USER_INPUT → EXPIRED
```

O item não é gravado e a fila avança.

Mensagem:

```text
A confirmação do lançamento anterior expirou e ele não foi gravado.
Envie novamente o documento para tentar outra vez.
```

## RN-028 — Falha de extração não bloqueia fila

Após retries, `EXTRACTION_FAILED` e próximo item pode avançar.

## RN-029 — Limite de fila

Configuração obrigatória:

```text
MAX_QUEUE_ITEMS_PER_CONVERSATION
```

Ao atingir o limite, rejeitar novo arquivo antes de chamar Gemini.

---

# 9. Idempotência e duplicidade

## RN-030 — Replay técnico do mesmo webhook

Mesmo `external_message_id` deve resultar em um único evento/operação.

## RN-031 — Mesmo arquivo reenviado pelo usuário

Mensagens distintas podem gerar dois lançamentos.

Deduplicação por conteúdo fica fora da Fase 1.

## RN-032 — Escrita no banco precisa de idempotency key

Retry do Database Writer não pode duplicar lançamento.

---

# 10. Retries e erros

## Política base

Para falhas técnicas transitórias:

```text
tentativa inicial
retry 1
retry 2
```

com backoff.

Com retry:

- timeout Gemini;
- 429/5xx conforme política;
- conexão DB temporária;
- timeout interno.

Sem retry:

- valor ausente;
- direction ambígua;
- arquivo inválido;
- usuário não autorizado;
- contrato inválido;
- erro de regra de dados.

---

# 11. Estados

## Processing Item

```text
RECEIVED
EXTRACTING
EXTRACTED
READY
ACTIVE
VALIDATING
WAITING_USER_INPUT
PERSISTING
COMPLETED
EXTRACTION_FAILED
PERSISTENCE_FAILED
FAILED
EXPIRED
```

## Execution

```text
PENDING
RUNNING
SUCCESS
RETRYING
FAILED
```

Transições inválidas devem ser rejeitadas/auditadas.

---

# 12. Modelo conceitual — Platform DB

## organizations

```text
id
name
slug
status
registration_secret_hash
created_at
updated_at
```

## bots

```text
id
organization_id
name
service_key
status
created_at
updated_at
```

## instances

```text
id
organization_id
bot_id NOT NULL
provider
external_instance_id
phone_number
status
created_at
updated_at
```

## users

```text
id
organization_id
phone_number
name nullable
status
registered_at
created_at
updated_at
```

## events

```text
id
correlation_id
external_message_id
organization_id
instance_id
user_id nullable
channel
event_type
message_type
received_at
status
completed_at
```

## processing_items

```text
id
event_id
correlation_id
organization_id
instance_id
user_id
sequence
status
message_received_at
file_mime_type
file_size
file_sha256
original_filename nullable
document_type
raw_extraction
normalized_data
quality_flags
confidence_data nullable
amount nullable
document_date nullable
transaction_date nullable
date_source nullable
direction nullable
question_type nullable
waiting_since nullable
expires_at nullable
created_at
extracted_at nullable
activated_at nullable
completed_at nullable
```

## executions

```text
id
event_id
processing_item_id nullable
correlation_id
component
operation
status
attempt
started_at
completed_at
duration_ms
error_code nullable
error_message_sanitized nullable
```

## service_usage

```text
id
event_id
processing_item_id
execution_id
provider
model
input_tokens
output_tokens
total_tokens
estimated_cost nullable
duration_ms
created_at
```

O schema físico pode ser normalizado em mais tabelas.

---

# 13. Contratos mínimos

## Evento normalizado

```json
{
  "correlation_id": "uuid",
  "external_message_id": "string",
  "instance": {
    "external_id": "string",
    "receiver_phone": "string"
  },
  "sender_phone": "string",
  "message_type": "image|pdf|text",
  "message_timestamp": "ISO-8601",
  "text": null,
  "media": {
    "mime_type": "image/jpeg",
    "filename": "optional",
    "size": 12345,
    "transient_reference": "implementation-specific"
  }
}
```

## TranscriptionResult

```json
{
  "success": true,
  "document_type": "pix_receipt",
  "raw_extraction": {},
  "normalized_data": {},
  "quality_flags": [],
  "confidence": {},
  "usage": {
    "provider": "google",
    "model": "TBD",
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0
  }
}
```

## DatabaseWriteRequest

```json
{
  "correlation_id": "uuid",
  "organization_id": "uuid",
  "operation": "create_financial_entry",
  "idempotency_key": "uuid",
  "data": {
    "amount": 1200.00,
    "transaction_date": "2026-07-29",
    "direction": "expense"
  }
}
```

Contrato final depende do schema DF.

---

# 14. Segurança

## SEC-001 — Segredos fora do Git

Nunca versionar tokens, senhas, API keys ou DATABASE_URL.

## SEC-002 — Autenticação entre serviços

```text
WUZAPI → Orquestrador: webhook secret/HMAC
Orquestrador → BOT: service token
BOT → Transcrição: service token
BOT → Database Writer: service token
Orquestrador → WUZAPI: WUZAPI token
```

## SEC-003 — Webhook

Validar:

- assinatura/segredo;
- formato;
- tamanho;
- replay/idempotência.

## SEC-004 — Cadastro

- hash/HMAC da senha;
- não logar comando completo;
- rate limit;
- audit de tentativas.

## SEC-005 — Credencial DF isolada

Somente Database Writer possui credencial DF.

```text
Dokploy Secret/Environment
→ Database Writer
→ Supabase DF
```

Preferir TLS e usuário de menor privilégio.

## SEC-006 — Upload

- tipos permitidos;
- tamanho máximo;
- assinatura real;
- não confiar apenas em extensão/MIME declarado;
- cleanup em sucesso/falha;
- nunca logar binário/base64.

## SEC-007 — Logs

Proibido registrar:

- senha `/cadastro`;
- API keys;
- tokens;
- DATABASE_URL;
- arquivos;
- credenciais.

Mascarar CPF/CNPJ quando não necessário para diagnóstico.

## SEC-008 — Rede

Produção:

```text
Internet
   ↓ HTTPS / Traefik
```

Serviços internos em rede privada.

Não expor Platform DB/Database Writer.

Restringir painel WUZAPI e SSH.

## SEC-009 — Rate limit / abuso

Limitar:

- cadastro;
- webhook;
- tamanho de arquivo;
- fila;
- concorrência IA;
- requisições por organização.

Bloquear antes de gerar custo quando possível.

## SEC-010 — Hardening pré-release externo

- versões Docker pinadas;
- container non-root quando compatível;
- `no-new-privileges`;
- capabilities mínimas;
- dependências fixadas/auditadas;
- backup/restore;
- rotação de secrets.

---

# 15. Observabilidade

Todo fluxo deve preservar um único:

```text
correlation_id
```

Deve ser possível reconstruir:

1. mensagem;
2. instância;
3. organização;
4. usuário;
5. BOT;
6. transcrição;
7. tokens;
8. validação;
9. pergunta/resposta;
10. gravação;
11. resposta WUZAPI;
12. erro/retry.

Métricas mínimas:

- mensagens;
- arquivos;
- sucesso/falha;
- duração E2E;
- duração por serviço;
- tokens por documento;
- tokens por organização;
- retries;
- expirados;
- tamanho de filas;
- erros por código.

---

# 16. Requisitos não funcionais

- Estado persistente; não depender de RAM para fila/conversa.
- Restart não perde fila.
- Escrita idempotente.
- Health/readiness por serviço.
- Timeouts explícitos.
- Graceful shutdown.
- Migrations versionadas.
- Config por ambiente.
- Logs estruturados.
- Backups do Platform DB.

---

# 17. TBDs / dependências

### TBD-001 — Schema de destino DF Holding
Bloqueia Database Writer final.

### TBD-002 — CPF/CNPJ reais
Bloqueia release de produção.

### TBD-003 — Critério de baixa confiança
Calibrar antes de produção.

### TBD-004 — Payload real WUZAPI
Validar instance ID, receiver, sender, message ID, media e reply/quoted message.

### TBD-005 — Retenção de mídia WUZAPI
Verificar `wuzapi/files`.

### TBD-006 — Limites operacionais
Definir após testes: tamanho de arquivo, concorrência, fila, rate limits.

---

# 18. Critérios de aceite do produto

A Fase 1 estará funcionalmente aprovada quando:

1. Usuário não cadastrado não gera Gemini.
2. `/cadastro SENHA_CORRETA` cadastra.
3. Senha errada não cadastra e sofre rate limit.
4. Instância roteia para BOT correto.
5. Replay WUZAPI não duplica.
6. Imagem/PDF válido é transcrito.
7. Arquivo é eliminado após processamento.
8. Cinco arquivos podem ser recebidos rapidamente.
9. Todos podem ser extraídos antes da fila de negócio.
10. A fila respeita FIFO.
11. Apenas um item interage por conversa.
12. Conversas distintas processam em paralelo.
13. Data ausente usa timestamp.
14. Valor ausente é perguntado.
15. Direction automática quando possível.
16. Direction ambígua é perguntada.
17. Pendência expira em uma hora.
18. Expiração libera fila.
19. Item completo grava sem confirmação final.
20. Só Database Writer acessa DB DF.
21. Resposta final contém direção, valor e data.
22. Tokens/duração são auditados.
23. Correlation ID reconstrói E2E.
24. Restart não perde fila.
25. Secrets não aparecem em logs.

---

# 19. Plano de implementação faseado

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
