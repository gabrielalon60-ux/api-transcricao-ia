# Technical Implementation Plan — Gate 3 — Transcrição

```text
Official source of truth:
.agents/IMPLEMENTATION_PLAN_GATE_3.md

The Antigravity implementation_plan.md artifact is a synchronized,
non-authoritative planning copy.
```

Este documento define a especificação técnica oficial e completa para o **Gate 3 — Transcrição**, versionado dentro do repositório no caminho `.agents/IMPLEMENTATION_PLAN_GATE_3.md`.

---

## 1. Goal Description

O Gate 3 implementa a extração inteligente e estruturada de comprovantes de Nota Fiscal, PIX, boletos, cupons fiscais, pedidos e orçamentos enviados por usuários ativos, através da integração do serviço de Transcrição com o Gemini e da invocação segura pelo Bot DF.

### Document-type contract decision

Explicit product decision: Gate 3 supports six business document categories represented by four technical runtime labels. This is an approved contract decision, not an inferred implementation shortcut.

Authoritative mapping:

| Business category | Runtime `document_type` label |
| --- | --- |
| Nota fiscal | `invoice` |
| Cupom fiscal | `invoice` |
| Comprovante PIX | `pix_receipt` |
| Boleto | `bank_receipt` |
| Pedido | `commercial_document` |
| Orçamento | `commercial_document` |

`unknown` remains a fallback classification only and is not a supported business document category. Six business acceptance fixtures remain required, but six separate runtime schemas are not required.

---

## 2. User Review Required

> [!IMPORTANT]
> **1. Assinatura de PDF Estrita (Primeiros 5 Bytes)**
> * O arquivo PDF deve começar **exatamente com os 5 bytes `b"%PDF-"` no byte zero**. Qualquer lixo ou byte de controle posicionado antes dessa assinatura resultará em rejeição imediata com erro `INVALID_PDF` (HTTP 422), sem chamar o Gemini.
>
> **2. Parseamento de Metadados Multipart JSON**
> * O endpoint interno `/internal/extract` receberá dois campos multipart form: `file` (o binário do arquivo) e `metadata` (uma string de texto contendo um objeto JSON).
> * O endpoint executará o parseamento e a validação do JSON de metadados explicitamente:
>   `parsed_metadata = InternalExtractionMetadata.model_validate_json(metadata)`
> * Qualquer malformação no JSON ou descumprimento do schema Pydantic retornará HTTP 422 com o contrato de erro `INVALID_METADATA` e os campos `request_id` e `event_id` definidos como `null` no response (sem expor Pydantic errors ou logs de metadados brutos).
>
> **3. Idempotência de Requisições via `request_id`**
> * O banco de dados do serviço de Transcrição usa `requests.id` como chave primária, que recebe diretamente o valor de `metadata.request_id`. Não existe coluna separada `request_id`. A constraint de unicidade da chave primária é a autoridade máxima para idempotência.
> * **Replays em andamento:** Se uma requisição com o mesmo `request_id` for recebida enquanto a primeira está sendo processada, retorna imediatamente `HTTP 409 Conflict` com o código de erro `REQUEST_ALREADY_PROCESSING` e `retryable=true`.
> * **Replays concluídos:**
>   * `SUCCEEDED` -> retorna o resultado final de negócio de sucesso persistido de forma estática, sem chamar a IA.
>   * `FAILED` -> retorna o resultado de erro persistido de forma estática, sem chamar a IA.
>   * `PERSISTENCE_FAILED` -> retorna HTTP 500 com `status="FAILED"`, `error_code="PERSISTENCE_ERROR"` e `retryable=false`, sem nova chamada à IA.
>
> **4. Isolamento Físico de Processo para Parsers**
> * Pillow e pypdf são bibliotecas puramente síncronas que podem bloquear o event loop ou serem expostas a vulnerabilidades. Executaremos as rotinas de validação estrutural do Pillow e pypdf em um subprocesso isolado usando o módulo `multiprocessing` nativo do Python com spawn-compatible arguments e timeout hard de `DOCUMENT_VALIDATION_TIMEOUT_SECONDS`.
>
> **5. Detecção de Conteúdo Ativo em PDFs**
> * Inspecionaremos a árvore estrutural do PDF em busca de objetos suspeitos como `/OpenAction`, `/AA`, `/JavaScript`, `/JS`, `/Launch` e `/EmbeddedFiles`. PDFs identificados com conteúdo ativo serão sumariamente rejeitados com `PDF_ACTIVE_CONTENT_UNSUPPORTED` (HTTP 422, não-retryable).

---

## 3. Open Questions & Integration TBDs

> [!NOTE]
> * **TBD-TRANSCRIPTION-SCHEMA-MAPPING — RESOLVED**
>   * Official mapping: `.agents/transcription_schema_mapping.md`.
>   * Schema mapping approved.
>   * ORM model implementation completed and locally validated.
>   * Migration source files not yet created.
>   * No physical database migration or adoption operation executed.
> * **TBD-WUZAPI-FILES-CLEANUP** — non-blocking locally; blocking for production deployment.

---

## 4. Early Change Review — extract.py

### Approved metadata contract clarification

Current user-approved Gate 3 internal metadata contract is:

- `request_id`;
- `bot_instance_id`;
- `correlation_id`;
- `received_at`;
- `source = WHATSAPP`.

This authority-resolution clarification supersedes older planning references to
`event_id`, `organization_id`, `instance_id`, and `user_id` for the current
internal route contract. The service persists `bot_instance_id` into the
nullable physical `requests.instance_id` column for Gate 3 internal requests.
The older fields remain nullable physical compatibility columns only and are
not required by `/internal/extract`.

Inspecionamos a alteração preliminar efetuada em `apps/transcription/src/transcription/api/extract.py`:
* **Modificação:** Inserção da função helper `validate_magic_bytes` e verificação básica de assinaturas sob `/extract`.
* **Classificação:** **Premature Gate 3 implementation**.
* **Decisão:** O código atual é insuficiente para atender às restrições estritas de bounded consumption of UploadFile in chunks, validação Pillow/pypdf estrutural, timeouts de subprocessos e rotas internas protegidas. Ele será **mantido** como ponto de partida mas passará por total refatoração durante o desenvolvimento das tarefas. Nenhuma tarefa do tracker será marcada como `DONE` com base nesse diff preliminar.

---

## 5. Dedicated Alembic Architecture

**Platform migrations**
- `packages/db/alembic.ini`
- `packages/db/alembic/`
- version table `alembic_version`

**Transcription migrations**
- `apps/transcription/alembic.ini`
- `apps/transcription/alembic/`
- version table `alembic_version_transcription`

The Transcription `env.py` must configure:
```python
context.configure(
    ...,
    version_table="alembic_version_transcription",
)
```
for both online and offline modes. This environment owns only the transcription tables (`applications`, `requests`, `extractions`, `usage_logs`) and related enums, indexes, and constraints. It never manages platform tables.

---

## 6. Migration Chain & Database Workflows

### Migration chain (frozen)

`transcription_1_0_baseline` → `gate3_schema`

The baseline derives from the verified historical commit `5fb2e485351dbd14962a44f9a4bbfd4da7ba6787`.

### Fresh database

Run the dedicated Transcription Alembic chain from baseline through Gate 3.

### Profile A — canonical unmanaged Version 1.0

1. Verify exact baseline schema equivalence.
2. `alembic stamp` the Transcription baseline revision.
3. Upgrade through the canonical Gate 3 migration.

### Profile B — current partial local drift

1. Verify the exact approved Profile B drift.
2. Execute a reviewed external reconciliation containing only the missing Gate 3 operations.
3. Verify exact Gate 3 schema equivalence.
4. Truthfully `alembic stamp` the Gate 3 head.

### Any other state

Abort without DDL, stamp, or partial reconciliation.

> Alembic `stamp` records migration history and does not reconcile schema.

---

## 7. Request Idempotency

`metadata.request_id` is stored directly as `requests.id`. The primary-key uniqueness constraint on `requests.id` is the authoritative idempotency mechanism. No separate `requests.request_id` column exists.

Transaction A inserts:
```text
requests.id = metadata.request_id
status = PROCESSING
processing_started_at = now()
```

The primary-key constraint on `requests.id` is the maximum authority. COMMIT occurs immediately before proceeding with validation or external calls.

---

## 8. Response Status vs Persisted State

- Every HTTP failure response exposes `status="FAILED"`.
- Persisted request state may be `FAILED` or `PERSISTENCE_FAILED`.
- A persisted `PERSISTENCE_FAILED` replay returns:
  - HTTP 500
  - `status="FAILED"`
  - `error_code="PERSISTENCE_ERROR"`
  - `retryable=false`

`PERSISTENCE_FAILED` is never exposed as the HTTP response status.

---

## 9. Internal vs Transitional Statuses

`/internal/extract` writes only the internal statuses:
- `PROCESSING`, `SUCCEEDED`, `FAILED`, `PERSISTENCE_FAILED`

The physical database enum also retains legacy values `PENDING` and `COMPLETED` for the legacy `/extract` route and historical rows. The four internal statuses are not the complete physical enum.

---

## 10. Database Defaults (Frozen)

- `usage_logs.attempt_number`: `INTEGER NOT NULL`, no server default; supplied explicitly by persistence logic (first attempt = 1).
- `requests.status`: `NOT NULL`, no server default; the internal workflow inserts `PROCESSING` explicitly; the legacy service supplies its own state.

No `PENDING` server default is added to the Gate 3 migration.

---

## 11. Provider-Attempt Usage Semantics

Each provider invocation constitutes a provider attempt. The system persists one `usage_logs` row per provider attempt **only when Transaction B succeeds**. Each row contains:
- `provider`, `model`, `attempt_number`, `status`, `started_at`, `completed_at`
- nullable `input_tokens`, `output_tokens`, `total_tokens`, `estimated_cost`
- `usage_status` (`AVAILABLE`, `PARTIAL`, `UNAVAILABLE`)
- `sanitized_error_code` recording the attempt outcome

Provider timeouts, 429 rate limits, temporary 5xx failures, auth errors and rejected requests all count as provider attempts and produce attempt rows (with potentially null token/cost fields).

Local failures before provider invocation do not create provider-attempt rows.

The error matrix columns `Attempt Log Expected` and `Usage Metrics May Be Available` replace the previous ambiguous `Usage Allowed` column.

---

## 12. Retry Counting (Frozen)

`PROVIDER_MAX_RETRIES = 2` means:
- initial attempt = `attempt_number` 1
- retry 1 = `attempt_number` 2
- retry 2 = `attempt_number` 3
- **maximum total provider attempts = 3**

Only timeout, 429 and approved transient 5xx responses are retryable by the Transcription provider layer. Auth errors, request rejections and local validation failures are never retried.

---

## 13. Proposed Changes

### Component: Transcription Service (`apps/transcription`)

#### 13.1 Configuration & Variables (`config.py`)

Adiciona ao arquivo de configurações as seguintes variáveis:
* `BOT_TO_TRANSCRIPTION_TOKEN`: Segredo direcional interno.
* `TRANSCRIPTION_DATABASE_URL`: String de conexão isolada da Transcrição.
* `MAX_UPLOAD_SIZE_MB`: Tamanho máximo do upload.
* `UPLOAD_CHUNK_SIZE_BYTES = 65536`: Tamanho de leitura de chunk.
* `UPLOAD_SPOOL_MAX_MEMORY_BYTES`: Limite de spool em memória.
* `MAX_IMAGE_WIDTH` / `MAX_IMAGE_HEIGHT` / `MAX_IMAGE_PIXELS`: Limites geométricos e de proteção DoS de imagens.
* `MAX_PDF_PAGES`: Limite de páginas do PDF.
* `MAX_PDF_OBJECTS`: Limite máximo de objetos percorridos na validação estrutural do PDF (padrão `1000`).
* `MAX_PDF_TRAVERSAL_DEPTH`: Profundidade máxima de recursão estrutural de dicionários (padrão `10`).
* `UPLOAD_TOTAL_TIMEOUT_SECONDS`: Limite de tempo para a leitura total do arquivo de upload (HTTP 408 se excedido).
* `UPLOAD_CHUNK_READ_TIMEOUT_SECONDS`: Limite de tempo de leitura de um único chunk do fluxo.
* `DOCUMENT_VALIDATION_TIMEOUT_SECONDS`: Tempo de corte para a validação de imagem/PDF no subprocesso.
* `DOCUMENT_VALIDATION_TERMINATION_GRACE_SECONDS`: Tempo de tolerância após sinal de término (join).
* `MAX_CONCURRENT_VALIDATIONS`: Limite máximo de validações de arquivo simultâneas em execução no replica/processo (padrão `4`).
* `VALIDATION_ACQUISITION_TIMEOUT_SECONDS`: Tempo limite para conseguir uma vaga de validação (HTTP 503 se exceder).
* `PROVIDER_TIMEOUT_SECONDS`: Timeout limite por chamada ao Gemini.
* `PROVIDER_MAX_RETRIES`: Padrão 2 (máximo 3 tentativas totais ao provedor; ver §12).
* `SYSTEM_PROMPT_PATH`: Caminho opcional do prompt absoluto.
* `MAX_SYSTEM_PROMPT_SIZE_BYTES = 262144`: Tamanho máximo do arquivo de prompt em bytes brutos (256 KiB). A fronteira é inclusiva: exatamente `262144` bytes é aceito; `262145` bytes é rejeitado. A validação ocorre sobre bytes brutos antes do decode UTF-8 estrito. Prompts ausentes, ilegíveis, diretórios, oversized, UTF-8 inválido, vazios ou apenas whitespace são inválidos.

---

#### 13.2 Pydantic Contracts & Schemas

As classes Pydantic utilizarão a política `extra="forbid"` para validação estrita:

```python
from pydantic import BaseModel, Field, ConfigDict, field_validator
from uuid import UUID
from datetime import datetime, timezone
from typing import Literal

class InternalExtractionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    event_id: UUID
    correlation_id: str = Field(min_length=1, max_length=128)
    organization_id: UUID
    instance_id: UUID
    user_id: UUID
    received_at: datetime
    source: Literal["WHATSAPP"]

    @field_validator("received_at")
    @classmethod
    def validate_received_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value.astimezone(timezone.utc)
```

> Note: `InternalExtractionMetadata.request_id` maps directly to `requests.id` in the database (see §7). No separate database column `request_id` exists.

##### Success Response:
```json
{
  "request_id": "uuid",
  "event_id": "uuid",
  "status": "SUCCEEDED",
  "document_type": "PIX",
  "extraction": {},
  "normalization": {},
  "confidence": null,
  "quality_flags": [],
  "usage": {
    "provider": "google",
    "model": "configured-provider-model-id",
    "pricing_version": "2026-08-v1",
    "input_tokens": 100,
    "output_tokens": 50,
    "total_tokens": 150,
    "cached_tokens": null,
    "usage_status": "AVAILABLE",
    "estimated_cost": "0.00012345",
    "currency": "USD"
  },
  "file": {
    "sha256": "hex_string",
    "detected_mime": "application/pdf",
    "declared_mime": "application/pdf",
    "size_bytes": 12345
  },
  "timing": {
    "latency_ms": 1234
  }
}
```

##### Failure Response:
```python
class InternalExtractionFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID | None = None
    event_id: UUID | None = None
    status: Literal["FAILED"]
    error_code: str
    retryable: bool
    retry_after_seconds: int | None = None
```

> Note: `status` is always `"FAILED"` in HTTP failure responses, regardless of persisted state. `error_code` distinguishes the cause (e.g. `INTERNAL_ERROR`, `PERSISTENCE_ERROR`).

---

#### 13.3 Complete Error & HTTP Mapping Matrix

| Error Code | HTTP Status | Retryable | Provider Called | Attempt Log Expected | Usage Metrics May Be Available | Logging Level | Sanitized Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `INVALID_METADATA` | 422 | false | false | false | false | WARNING | Metadados da requisição malformados ou incompletos. |
| `REQUEST_ALREADY_PROCESSING` | 409 | true | false | false | false | WARNING | Requisição idêntica já está em processamento concorrente. |
| `EMPTY_FILE` | 422 | false | false | false | false | WARNING | O arquivo enviado está em branco. |
| `FILE_TOO_LARGE` | 413 | false | false | false | false | WARNING | O arquivo excede o limite máximo permitido. |
| `UNSUPPORTED_FILE_TYPE` | 422 | false | false | false | false | WARNING | Assinatura de arquivo não suportada. |
| `MIME_MISMATCH` | 422 | false | false | false | false | WARNING | O MIME type do arquivo é incompatível com a assinatura. |
| `INVALID_IMAGE` | 422 | false | false | false | false | ERROR | A imagem enviada está corrompida ou é inválida. |
| `IMAGE_DIMENSIONS_EXCEEDED` | 422 | false | false | false | false | WARNING | As dimensões geométricas da imagem excedem os limites. |
| `IMAGE_PIXEL_LIMIT_EXCEEDED` | 422 | false | false | false | false | WARNING | Limite máximo de pixels excedido (proteção bomb). |
| `ANIMATED_IMAGE_UNSUPPORTED` | 422 | false | false | false | false | WARNING | Imagens com múltiplos frames ou animações não são aceitas. |
| `INVALID_PDF` | 422 | false | false | false | false | ERROR | O documento PDF está corrompido ou é ilegível. |
| `PDF_ENCRYPTED` | 422 | false | false | false | false | WARNING | O PDF está encriptado ou protegido por senha. |
| `PDF_PAGE_LIMIT_EXCEEDED` | 422 | false | false | false | false | WARNING | O número de páginas do PDF excede o limite máximo. |
| `PDF_ACTIVE_CONTENT_UNSUPPORTED` | 422 | false | false | false | false | WARNING | PDFs contendo scripts ou ações ativas são rejeitados. |
| `PDF_STRUCTURE_LIMIT_EXCEEDED` | 422 | false | false | false | false | WARNING | O PDF excedeu os limites de recursão ou contagem de objetos. |
| `DOCUMENT_VALIDATION_TIMEOUT` | 422 | false | false | false | false | ERROR | O tempo limite de validação estrutural em subprocesso expirou. |
| `VALIDATION_PROCESS_FAILED` | 500 | false | false | false | false | ERROR | Falha de execução interna ou crash no subprocesso de validação. |
| `VALIDATION_CAPACITY_EXCEEDED` | 503 | true | false | false | false | WARNING | Capacidade de validação desta réplica temporariamente ocupada. |
| `UPLOAD_READ_TIMEOUT` | 408 | true | false | false | false | WARNING | Tempo limite de recepção de upload excedido. |
| `INVALID_DOCUMENT` | 422 | false | true | true | true | WARNING | Não foi possível classificar o documento enviado. |
| `PROVIDER_TIMEOUT` | 504 | true | true | true | false | ERROR | Tempo limite excedido ao se conectar com o provedor de IA. |
| `PROVIDER_RATE_LIMITED` | 503 | true | true | true | false | WARNING | Limite de cota de requisições de IA atingido. |
| `PROVIDER_TEMPORARY_ERROR` | 503 | true | true | true | false | ERROR | O provedor de IA retornou uma falha temporária. |
| `PROVIDER_AUTH_ERROR` | 502 | false | true | true | false | CRITICAL | Falha de credenciamento ou autorização no provedor de IA. |
| `PROVIDER_REQUEST_REJECTED` | 502 | false | true | true | false | ERROR | A requisição de IA foi rejeitada pelo provedor. |
| `PERSISTENCE_ERROR` | 500 | false | depends on execution stage | if provider was called; durability not guaranteed | if provider was called; durability not guaranteed | CRITICAL | Erro na persistência interna dos logs da Transcrição. |
| `INTERNAL_ERROR` | 500 | false | — | false | false | ERROR | Erro interno inesperado com resposta sanitizada. |

---

#### 13.4 Bounded Upload Strategy

* A aplicação realiza o **bounded consumption of UploadFile in chunks** e nunca invoca um método `read()` ilimitado. O limite de tamanho imposto pela aplicação é verificado de forma independente do cabeçalho `Content-Length`. Starlette/FastAPI podem já ter persistido dados da requisição em arquivos temporários em disco antes da execução da rota.
* Tamanho máximo do chunk: `UPLOAD_CHUNK_SIZE_BYTES = 65536`.
* Limite total: `MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024`.
* Se ultrapassar o limite, aborta a leitura e lança `FILE_TOO_LARGE` (HTTP 413) com handles fechados no `finally`.

---

#### 13.5 Temporary-Storage Cleanup Strategy

> Upload and temporary-file handles are closed deterministically in `finally`. Application-created named temporary paths are unlinked by the parent process after validation, timeout or failure. Backing storage cleanup follows Python and operating-system semantics. The system does not promise secure erasure of process memory or physical storage.

* **Responsabilidades do Processo Pai:**
  * Fecha o `UploadFile` recebido.
  * Fecha o `SpooledTemporaryFile`.
  * Fecha os file handles temporários antes de criar o worker subprocesso (evitando bloqueios de locks em Windows).
  * Exclui (unlink) qualquer caminho temporário criado via `NamedTemporaryFile(delete=False)` no bloco `finally` superior após o término, timeout ou falha do subprocesso.
  * Envia o sinal de término (`terminate()`), aguarda (`join()`) e fecha endpoints IPC.
* **Responsabilidades do Subprocesso:**
  * Abre e lê o arquivo pelo caminho temporário fornecido (quando aplicável).
  * Fecha todos os handles de arquivos internos antes do encerramento.
  * Nunca deleta arquivos de propriedade do processo pai.

---

#### 13.6 Serializable Transport & Spawn Process Execution

Para garantir total compatibilidade com spawn de subprocessos em Windows, **nenhum** handle aberto, objeto SQLAlchemy, conexão de banco, Pydantic settings ou client HTTP será passado para o subprocesso.
* **Transporte por Bytes (Pequenos Arquivos):** Se o tamanho do arquivo for menor ou igual a `UPLOAD_SPOOL_MAX_MEMORY_BYTES`, os bytes lidos do spool serão serializados e passados diretamente para a função do subprocesso via `source_bytes`.
* **Transporte por Caminho (Arquivos Maiores):** Se o tamanho exceder o limite de spool na memória, materializa os dados em um arquivo temporário no OS (`NamedTemporaryFile(delete=False)`), fecha o handle no pai, e passa o caminho em formato string via `temporary_path`.

```python
@dataclass(frozen=True)
class ValidationLimits:
    max_image_width: int
    max_image_height: int
    max_image_pixels: int
    max_pdf_pages: int
    max_pdf_objects: int
    max_pdf_traversal_depth: int

def validate_document_worker(
    source_bytes: bytes | None,
    temporary_path: str | None,
    detected_format: str,
    limits: ValidationLimits,
) -> ValidationResult:
    # Abre o fluxo (bytes ou arquivo)
    # Valida estrutura em Pillow/pypdf
    # Retorna ValidationResult serializável
```

---

#### 13.7 Non-blocking Supervisor Thread Lifecycle

Toda a gerência de subprocesso rodará em uma thread separada via `asyncio.to_thread` para evitar travar o event loop do FastAPI:

```python
result = await asyncio.to_thread(
    run_validation_subprocess_sync,
    validation_input,
    limits,
    timeout_settings,
)
```

O supervisor síncrono `run_validation_subprocess_sync` executará as etapas:
1. Cria o subprocesso com `ctx = multiprocessing.get_context("spawn")`.
2. Cria o pipe com `parent_conn, child_conn = ctx.Pipe(duplex=False)`.
3. Inicia o subprocesso descartável.
4. Aguarda no Pipe com timeout:
   * Se o timeout expirar, chama `process.terminate()`.
   * Aguarda com `process.join(document_validation_termination_grace_seconds)`.
   * Se ainda ativo, chama `process.kill()` (se disponível na plataforma) e faz join final.
   * Fecha os Pipes e lança `DOCUMENT_VALIDATION_TIMEOUT`.
5. Retorna o struct `ValidationResult` contendo o status de sucesso ou erro traduzido (sem tracebacks do subprocesso).

---

#### 13.8 Concurrency Limit Scope

* O semáforo local `asyncio.Semaphore` atua apenas a nível do processo/réplica ativa da aplicação, não controlando o host de forma global.
* **Política de Implantação:**
  * O Transcription rodará com exatamente **1 worker de aplicação por container/réplica**.
  * A concorrência por réplica será controlada por `MAX_CONCURRENT_VALIDATIONS`.
  * A concorrência total da aplicação é dada por:
    `total_concurrency = replicas * MAX_CONCURRENT_VALIDATIONS`
  * Se o semáforo local estiver cheio, a requisição retorna `VALIDATION_CAPACITY_EXCEEDED` (HTTP 503, retryable).
  * O semáforo não coordena dados entre diferentes contêineres/réplicas.

---

#### 13.9 Image & WEBP Validation (Pillow)

Dentro do subprocesso de validação:
1. Configura warnings do Pillow como erros e valida pixels:
   ```python
   import warnings
   from PIL import Image
   Image.MAX_IMAGE_PIXELS = limits.max_image_pixels
   warnings.simplefilter("error", Image.DecompressionBombWarning)
   ```
2. Executa a validação manual de pixels: `width * height > limits.max_image_pixels`.
3. Executa validação de assinatura WEBP RIFF antes de instanciar o Pillow:
   * Verifica se os bytes 0-3 são `RIFF` e bytes 8-11 são `WEBP`.
   * Garante a igualdade estrita do tamanho do payload: `declared_riff_size == file_size - 8`. Se inconsistente, lança `INVALID_IMAGE`.
   * As dimensões da imagem WEBP devem vir da decodificação do Pillow, não de parses manuais da tabela RIFF.
4. Abre o arquivo temporário ou stream de bytes com contexto managed:
   ```python
   with Image.open(stream) as img:
       if img.format not in {"JPEG", "PNG", "WEBP"}:
           raise ValueError("Format mismatch")
       if img.width > limits.max_image_width or img.height > limits.max_image_height:
           raise ValueError("Dimensions exceeded")
       if getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1:
           raise ValueError("Animated unsupported")
       img.verify()
   ```

---

#### 13.10 PDF Active-Content Traversal Sequence (pypdf)

Dentro do subprocesso:
1. Garante que os primeiros 5 bytes são exatamente `b"%PDF-"`.
2. Inicializa o leitor estrutural: `reader = pypdf.PdfReader(stream)`.
3. Rejeita imediatamente PDF encriptado: `reader.is_encrypted == True` -> `PDF_ENCRYPTED`.
4. Valida contagem de páginas: `len(reader.pages) > limits.max_pdf_pages` -> `PDF_PAGE_LIMIT_EXCEEDED`.
5. Executa a inspeção estrutural de segurança para recursar dicionários e listas de objetos do PDF (catalog root, `/Pages`, `/Actions`, etc.):
   * Mantém um conjunto `visited_objects = set()` composto de tuplas `(idnum, generation)` para proteção contra referências circulares e recursão infinita.
   * Lança `PDF_STRUCTURE_LIMIT_EXCEEDED` se o número de objetos inspecionados ultrapassar `max_pdf_objects` ou se a profundidade da busca exceder `max_pdf_traversal_depth`.
   * PDFs contendo estruturalmente detectadas ações ativas, scripts, ações de lançamento (launch) ou arquivos embutidos são sumariamente rejeitados com `PDF_ACTIVE_CONTENT_UNSUPPORTED` (HTTP 422, não-retryable).
   * Ocorrências textuais comuns de caracteres `/JS` ou `/JavaScript` no conteúdo de páginas comuns e links normais de hipertexto (sem ações ativas associadas) **não** acionam rejeições.

---

### Component: Bot DF Service (`apps/bot_df`)

* Cria o módulo `TranscriptionClient` para se comunicar com o endpoint `/internal/extract`.
* Propaga os UUIDs (`request_id`, `event_id`, `correlation_id`) e a data do recebimento via Multipart form.
* **Isolamento de Concorrência e Retries:** O Bot DF **não** fará retries automáticos na chamada HTTP de extração. O Bot apenas lê a propriedade `retryable` retornada pelo endpoint e repassa a decisão ao módulo de gerenciamento de fila (que será desenvolvido no Gate 4).

---

## 14. Transaction Flow & Persistence Recovery

A integridade transacional de dados obedecerá estritamente ao seguinte modelo de estados persistidos:

```text
PROCESSING
SUCCEEDED
FAILED
PERSISTENCE_FAILED
```

* **PROCESSING:** Estado inicial criado na Transação A.
* **SUCCEEDED:** Extração bem-sucedida gravada na Transação B.
* **FAILED:** Erro de negócio/provedor terminal gravado na Transação B.
* **PERSISTENCE_FAILED:** Falha de escrita na Transação B com compensação bem-sucedida.

##### Transação A (Aquisição de Requisição)
* Tenta inserir o registro na tabela de requisições com `requests.id = metadata.request_id`, status `PROCESSING` e `processing_started_at = now()`. A constraint de chave primária em `requests.id` é a autoridade máxima para idempotência.
* Executa COMMIT imediato antes de prosseguir com validação ou chamada externa.

##### Transação B (Persistência Final)
* Atomicamente tenta persistir: atualização terminal do status da requisição, resultado de extração e registros de tentativas do provedor (`usage_logs`).
* Se tudo for concluído com sucesso, grava o resultado e atualiza status para `SUCCEEDED` (ou `FAILED` para erros de negócio).
* Se a persistência transacional B falhar:
  * Registros de extração e `usage_logs` pretendidos podem não ser duráveis.
  * Abre uma transação de compensação curta para atualizar status da requisição para `PERSISTENCE_FAILED`, preencher `last_persistence_error_at = now()` e gravar metadados parciais mínimos (evitando nova chamada ao provedor no replay).
  * Se a compensação também falhar, retorna `PERSISTENCE_ERROR` (HTTP 500, non-retryable) mantendo status em `PROCESSING` (não re-executável automaticamente). Neste caso `completed_at` permanece `null`.
  * Reconciliação administrativa é necessária para registros que permaneçam em `PROCESSING` além do timeout operacional.
* Replays nunca invocam novamente o provedor de IA.

---

## 15. Physical-Schema & Adoption State

- The existing platform Alembic chain does not own the Transcription tables.
- The local Transcription database currently lacks `alembic_version_transcription`.
- The local database matches the approved Profile B partial-drift definition.
- Migration files, reconciliation operations, stamps and upgrades have **not** been executed.
- The preserved database must remain untouched until source files are reviewed and separately authorized.

---

## 16. Verification Plan

### 16.1 Metadata and contracts
* **Test Case:** Envio de requisição multipart válida com exatamente um campo `file` e um campo `metadata` em string JSON.
* **Test Case:** Ausência do campo `metadata` retorna HTTP 422.
* **Test Case:** JSON de `metadata` malformado ou campos UUID malformados retornam `INVALID_METADATA` (HTTP 422).
* **Test Case:** Envio de metadados contendo campos extras (como campos não mapeados no schema Pydantic) retorna HTTP 422 devido à regra `extra="forbid"`.
* **Test Case:** Envio de `correlation_id` vazio ou que excede 128 caracteres é rejeitado.
* **Test Case:** Envio de `received_at` sem timezone é rejeitado. Timestamps válidos com fuso positivo ou negativo são aceitos e normalizados para UTC.
* **Test Case:** Falhas pré-validação de metadados retornam `request_id=null` e `event_id=null`. Não são utilizadas regex para recuperar IDs em payloads inválidos.
* **Test Case:** Valida o schema de sucesso e de falha contendo `confidence=null` e `quality_flags=[]` vazios.
* **Test Case:** Decimal do custo estimado do response é serializado como string.

### 16.2 Authentication separation
* **Test Case:** Requisições para `/internal/extract` sem o cabeçalho token ou com segredo inválido retornam HTTP 401.
* **Test Case:** Chaves de API legadas são rejeitadas na rota interna `/internal/extract`, e tokens direcionais internos são rejeitados na rota legada `/extract`.
* **Test Case:** O token direcional e chaves de API nunca são exibidos nos logs ou gravados no banco. O cabeçalho Authorization nunca é logado.

### 16.3 Upload bounds and timeouts
* **Test Case:** Upload de arquivos com exatamente `MAX_UPLOAD_SIZE_BYTES` é aceito, e arquivos um byte acima são rejeitados com `FILE_TOO_LARGE` (HTTP 413).
* **Test Case:** Upload de arquivos sem cabeçalho `Content-Length` ou com `Content-Length` falsamente pequeno são consumidos em chunks limitados e rejeitados no overflow real de bytes.
* **Test Case:** Timeouts de upload total ou de chunks excedidos retornam `UPLOAD_READ_TIMEOUT` (HTTP 408, retryable), limpando os handles criados.
* **Test Case:** Cada chamada de leitura de chunk possui tamanho delimitado explicitamente (`UPLOAD_CHUNK_SIZE_BYTES`).
* **Test Case:** Handles do `UploadFile` e do `SpooledTemporaryFile` são fechados em blocos `finally` de nível superior no sucesso, erro ou timeout.

### 16.4 Serializable child-process transport
* **Test Case:** Arquivos pequenos (<= limit) utilizam `source_bytes` e não geram arquivo temporário nomeado.
* **Test Case:** Arquivos maiores (> limit) utilizam `temporary_path`.
* **Test Case:** Validação no worker falha caso ambos os campos estejam preenchidos ou ambos estejam nulos.
* **Test Case:** Apenas tipos primitivos e dataclasses são passados ao subprocesso. Nenhum handle de arquivo ou sessão SQLAlchemy é compartilhada.
* **Test Case:** O caminho do arquivo temporário permanece intacto enquanto o subprocesso executa e é removido somente após o término (sucesso, timeout ou crash) do child.

### 16.5 Valid format fixtures
* **Test Case:** Executa validação de assinaturas e MIME types compatíveis em fixtures válidas de JPEG, PNG, WEBP e PDF.
* **Test Case:** Valida que o processamento gera o SHA-256 apenas como metadado de auditoria e aceita arquivos duplicados (mesmo SHA-256) sob `requests.id` diferentes.

### 16.6 Invalid image fixtures
* **Test Case:** Envio de imagem vazia ou truncada (JPEG, PNG, WEBP) gera rejeição.
* **Test Case:** WEBP com tamanho de payload inconsistente na tabela RIFF (`file_size - 8`) ou cabeçalhos corrompidos geram rejeição.
* **Test Case:** GIFs são rejeitados com `UNSUPPORTED_FILE_TYPE`. GIFs não são suportados.
* **Test Case:** Imagens animadas (WEBP ou APNG contendo múltiplos frames) retornam `ANIMATED_IMAGE_UNSUPPORTED`.
* **Test Case:** Imagens com dimensões ou pixels excedidos, ou warnings de decompression bomb (que disparam erro) retornam `IMAGE_PIXEL_LIMIT_EXCEEDED` ou `IMAGE_DIMENSIONS_EXCEEDED` sem chamar o Gemini.

### 16.7 Invalid PDF fixtures
* **Test Case:** PDF com lixo posicionado antes do byte zero (`b"%PDF-"`) é rejeitado.
* **Test Case:** PDFs contendo referências circulares ou limites de recursão/objetos excedidos retornam `PDF_STRUCTURE_LIMIT_EXCEEDED`.
* **Test Case:** PDFs contendo estruturalmente ações ativas, scripts, ações de lançamento (launch) ou arquivos embutidos são rejeitados com `PDF_ACTIVE_CONTENT_UNSUPPORTED`.
* **Test Case:** PDFs contendo texto `/JS` ordinário ou hiperlinks normais de navegação são aceitos normalmente.

### 16.8 MIME and filename rules
* **Test Case:** Arquivos com MIME ausente mas assinatura válida são aceitos. MIME incompatível com assinatura gera `MIME_MISMATCH`. Extensão do arquivo de nome não influi na rejeição se a mágica for compatível.

### 16.9 Subprocess lifecycle and IPC
* **Test Case:** Retorno de resultados síncronos e erros estruturados via Pipe IPC.
* **Test Case:** Crash ou código de retorno não-zero do subprocesso dispara `VALIDATION_PROCESS_FAILED`.
* **Test Case:** Excesso de tempo de execução dispara `process.terminate()`, aguarda com grace join, escala para `kill()` se necessário, limpa pipes e retorna `DOCUMENT_VALIDATION_TIMEOUT`.
* **Test Case:** O event loop permanece ativo enquanto o supervisor aguarda o subprocesso através de `asyncio.to_thread`.
* **Test Case:** Limites de concorrência local e timeouts de aquisição retornam `VALIDATION_CAPACITY_EXCEEDED`.

### 16.10 Idempotency and persistence
* **Test Case:** Testes concorrentes reais contra o PostgreSQL garantem a inserção única via chave primária `requests.id` com estado `PROCESSING`.
* **Test Case:** Tentativa concorrente retorna `REQUEST_ALREADY_PROCESSING`.
* **Test Case:** Replays de requisições em status `SUCCEEDED` ou `FAILED` retornam a resposta cacheada.
* **Test Case:** Replay de status `PERSISTENCE_FAILED` retorna HTTP 500 com `status="FAILED"`, `error_code="PERSISTENCE_ERROR"` de forma direta sem nova chamada ao Gemini.
* **Test Case:** Registros de `processing_started_at`, `completed_at` e `last_persistence_error_at` são gravados e validados no banco.

### 16.11 Provider retry and cost
* **Test Case:** Disparo de até 2 retries automáticos (máximo 3 tentativas totais, `attempt_number` 1, 2, 3) sob falhas timeout, 429 ou 5xx transitórias do Gemini.
* **Test Case:** Erros locais de validação ou de credenciais de acesso não disparam retentativas.
* **Test Case:** Persistência de registros individuais de tentativas contendo consumo e custo via `Decimal` (`Numeric(18, 8)`) com arredondamento `ROUND_HALF_UP` e precificação versão imutável.
* **Test Case:** O response agrega a soma de tokens e custos de tentativas (usage status `AVAILABLE`, `PARTIAL` ou `UNAVAILABLE`).

### 16.12 Prompt packaging and startup
* **Test Case:** startup falha caso o prompt interno esteja vazio, contenha apenas whitespace, seja ilegível, aponte para diretório, contenha UTF-8 inválido, não seja encontrado ou exceda `MAX_SYSTEM_PROMPT_SIZE_BYTES`. O request de extração não pode sobregravar o prompt.
* **Test Case:** o carregamento do prompt usa a mesma implementação validada no startup e em runtime, cacheada uma vez por processo. Alterações no arquivo, `SYSTEM_PROMPT_PATH` ou `MAX_SYSTEM_PROMPT_SIZE_BYTES` exigem restart; não há hot reload.
* **Test Case:** falha defensiva de prompt em runtime retorna `SYSTEM_PROMPT_INVALID`, HTTP 503, `retryable=false`, sem chamar o Gemini. Em `/internal/extract`, a Transação A permanece antes do carregamento runtime; se a falha ocorrer após a Transação A, o estado terminal persistido é `FAILED` com `error_code=SYSTEM_PROMPT_INVALID`.

### 16.13 Six-document regression and backward compatibility
* **Test Case:** Processamento síncrono com mocks do Gemini para as seis categorias de negócio: Nota Fiscal, PIX, Boleto, Cupom Fiscal, Pedido e Orçamento. Os fixtures validam a matriz aprovada de seis categorias de negócio para quatro labels técnicos (`invoice`, `pix_receipt`, `bank_receipt`, `commercial_document`), sem exigir seis schemas técnicos separados.
* **Test Case:** The external contract and behavior of `/extract` remain backward compatible. O Bot DF não realiza retries automáticos e não inicia filas concorrentes no Gate 3.

### 16.14 Dedicated migration ownership
* **Test Case:** Platform (`alembic_version`) and Transcription (`alembic_version_transcription`) version tables are independent.
* **Test Case:** Transcription migrations never create, modify, or drop platform tables.

### 16.15 Fresh database migration
* **Test Case:** `transcription_1_0_baseline` followed by `gate3_schema` creates a physical schema equivalent to the approved ORM models.

### 16.16 Profile A adoption
* **Test Case:** Exact Version 1.0 audit passes.
* **Test Case:** Baseline stamp is truthful and recorded in `alembic_version_transcription`.
* **Test Case:** Gate 3 upgrade reaches the approved target schema.

### 16.17 Profile B adoption
* **Test Case:** Exact partial-drift audit passes against the approved Profile B definition.
* **Test Case:** External reconciliation applies only the missing Gate 3 changes.
* **Test Case:** Post-reconciliation schema equals the approved Gate 3 target.
* **Test Case:** Gate 3 stamp is recorded in `alembic_version_transcription` only after equivalence is verified.

### 16.18 Unsupported drift
* **Test Case:** Audit of an unrecognized schema state aborts.
* **Test Case:** No DDL, stamp, or partial reconciliation is applied.

### 16.19 Migration runtime safety
* **Test Case:** No Alembic `upgrade` is automatically executed during application startup.
* **Test Case:** `Base.metadata.create_all` is not used as a production migration mechanism.
* **Test Case:** Migration/reconciliation source generation and review does not touch the preserved database.

---

## 17. Synchronization Status

- **ALEMBIC ARCHITECTURE — APPROVED**
- **MIGRATION SOURCE IMPLEMENTATION — COMPLETE**
- **APPLICATION IMPLEMENTATION — APPROVED**
- **GATE 3 — COMPLETE**
- **PRODUCTION DEPLOYMENT — NOT PERFORMED**
- **PRODUCTION DATABASE ADOPTION — NOT PERFORMED**
- **WUZAPI PRODUCTION MEDIA-RETENTION VERIFICATION — PENDING OPERATIONAL FOLLOW-UP**
- **GATE 4 — NOT STARTED**

Gate 3 was formally approved by explicit user instruction on 2026-08-04 at 00:20:08 -03:00 (America/Sao_Paulo), based on formal review result `REVIEW PASSED WITH FOLLOW-UPS`. The approval covers the Gate 3 application implementation and Gate 3 completion only; it does not authorize production deployment, production database adoption, Gemini calls, Supabase/database mutation, commits, pushes, or Gate 4 implementation.

The authoritative `.agents/IMPLEMENTATION_PLAN_GATE_3.md` has been consolidated. The non-authoritative `implementation_plan.md` artifact has been synchronized.
