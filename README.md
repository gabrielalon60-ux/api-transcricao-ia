# Intelligent Document Extraction API

SaaS platform for intelligent document and image processing powered by AI (Google Gemini + provider-agnostic architecture).

## Quickstart

### 1. Clone & install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
```

### 2. Configure environment

```bash
copy .env.example .env
# Edit .env: set DATABASE_URL and GEMINI_API_KEY
```

### 3. Start the server

```bash
uvicorn app.main:app --reload
```

Swagger UI: http://localhost:8000/docs

---

## Usage

### Register an application

```http
POST /applications
Content-Type: application/json

{
  "name": "My App",
  "api_key": "APP_MYAPP_123456789"
}
```

### Extract data from an image

```http
POST /extract
Authorization: Bearer APP_MYAPP_123456789
Content-Type: multipart/form-data

file=<image file>
prompt=Extract full name, CPF, and date of birth
```

Response:

```json
{
  "success": true,
  "request_id": "uuid",
  "data": {
    "full_name": "John Doe",
    "cpf": "123.456.789-00",
    "date_of_birth": "1990-01-15"
  }
}
```

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/extract` | Extract structured data from image |
| `GET` | `/requests` | List request history (filterable) |
| `GET` | `/usage` | Token usage and cost statistics |
| `GET` | `/applications` | List registered applications |
| `POST` | `/applications` | Register new application |
| `PATCH` | `/applications/{id}/deactivate` | Deactivate application |
| `GET` | `/health` | Health check |

---

## Project Structure

```text
app/
├── main.py
├── api/
│   ├── extract.py
│   ├── requests.py
│   ├── usage.py
│   └── applications.py
├── services/
│   ├── ai/
│   │   ├── provider.py         ← Abstract AIProvider
│   │   └── gemini_provider.py  ← Gemini implementation
│   ├── extraction_service.py
│   └── usage_service.py
├── database/
│   ├── models.py
│   ├── session.py
│   └── repositories/
├── auth/
│   └── api_key_auth.py
├── schemas/
│   ├── requests.py
│   ├── applications.py
│   └── usage.py
├── core/
│   ├── config.py
│   └── logging.py
└── tests/
```

---

## Adding a New AI Provider

1. Create `app/services/ai/openai_provider.py`
2. Inherit from `AIProvider`
3. Implement `async def extract(self, image_bytes, prompt) -> ExtractionResult`
4. Inject into `ExtractionService` via dependency injection or config flag

No other changes required.
