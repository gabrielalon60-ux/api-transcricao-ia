# Intelligent Document Extraction API

AI-powered platform for intelligent document extraction with native WhatsApp integration.

The project uses **Google Gemini** for document understanding and **WUZAPI** for WhatsApp communication, running together through Docker Compose.

---

# Features

- AI document extraction
- WhatsApp integration
- REST API
- Swagger documentation
- Usage statistics
- Request history
- Docker-based deployment
- Single configuration file (.env)

---

# Architecture

```
                WhatsApp
                    │
                    ▼
              WUZAPI Container
                    │
                    ▼
      Intelligent Document API
                    │
                    ▼
         Google Gemini + Database
```

Both services run together using Docker Compose.

---

# Requirements

- Docker
- Docker Compose
- Git

---

# Installation

Clone the repository:

```bash
git clone https://github.com/gabrielalon60-ux/api-transcricao-ia.git
cd api-transcricao-ia
```

Run the installer:

```bash
chmod +x install.sh
./install.sh
```

On the first execution the installer will automatically create:

```
.env
```

Edit this file and configure your environment.

Run the installer again:

```bash
./install.sh
```

---

# Configuration

The project uses a **single `.env` file** shared by all services.

Create it manually if necessary:

```bash
cp .env.example .env
```

Main configuration variables:

```dotenv
DATABASE_URL=

GEMINI_API_KEY=

API_KEY_HASH_SECRET=

WUZAPI_BASE_URL=http://wuzapi:8080
WUZAPI_INSTANCE=
WUZAPI_TOKEN=
WUZAPI_APPLICATION_ID=

WUZAPI_ADMIN_TOKEN=
WUZAPI_PORT=8080
SESSION_DEVICE_NAME=API Transcrição IA

TZ=America/Sao_Paulo
```

---

# Updating

To update an existing installation:

```bash
./update.sh
```

The script automatically:

- pulls the latest code
- rebuilds changed containers
- starts the application
- removes unused images

---

# Running manually

Start:

```bash
docker compose up -d --build
```

Stop:

```bash
docker compose down
```

Restart:

```bash
docker compose restart
```

View logs:

```bash
docker compose logs -f
```

Container status:

```bash
docker compose ps
```

---

# Services

API

```
http://localhost:8000
```

Swagger

```
http://localhost:8000/docs
```

WUZAPI

```
http://localhost:8080
```

Health Check

```
http://localhost:8000/health
```

---

# Authentication

Applications are authenticated using API Keys.

Generate a new API Key:

```bash
python scripts/generate_api_key.py
```

Insert the generated SHA-256 hash into the database.

Deliver the original key to the client.

The raw key cannot be recovered.

---

# Example Request

```
POST /extract
Authorization: Bearer APP_MY_APP_KEY
Content-Type: multipart/form-data
```

Body:

```
file=<image>
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

# Endpoints

| Method | Endpoint | Description |
|---------|----------|-------------|
| POST | /extract | Extract structured data |
| GET | /requests | Request history |
| GET | /usage | Usage statistics |
| POST | /whatsapp/webhook | WUZAPI webhook |
| GET | /health | Health check |

---

# Project Structure

```
api-transcricao-ia/
│
├── app/
├── scripts/
├── wuzapi/
│   ├── dbdata/
│   └── files/
│
├── Dockerfile
├── docker-compose.yml
├── install.sh
├── update.sh
├── .env.example
└── README.md
```

---

# AI Providers

The AI layer is provider-agnostic.

To add a new provider:

1. Create a new provider inside:

```
app/services/ai/
```

2. Inherit from `AIProvider`.

3. Implement:

```python
extract(...)
```

4. Register the provider.

No other changes are required.

---

# Persistent Data

WUZAPI data is stored in:

```
wuzapi/dbdata
wuzapi/files
```

Do not delete these folders when updating.

---

# License

This project is licensed under the MIT License.