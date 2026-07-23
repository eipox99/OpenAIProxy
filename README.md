# OpenAIProxy 🔀

A lightweight, self-hosted **LLM router/proxy** that exposes an **OpenAI-compatible API** at a single base URL. Incoming requests are routed to upstream providers in **priority order** with automatic failover on errors (rate limits, timeouts, server errors). A web UI lets you manage providers — add, edit, delete, toggle on/off, auto-detect models, enable/disable per model, and drag-and-drop priority ordering.

## Features

- **Single endpoint** — Point your OpenAI SDK at `http://localhost:8000` and forget about multiple API URLs
- **Priority-based routing** — Define a ranked list of providers; the proxy always tries the highest priority first
- **Automatic failover** — Detects rate limits (429), server errors (5xx), timeouts, network errors, and auth errors, seamlessly switching to the next provider
- **Per-model control** — Auto-detect models from each provider, then enable/disable individual models per provider
- **Web UI** — Manage everything from your browser: add/edit/delete providers, reorder by priority, toggle providers and models on/off
- **Circuit breaker** — After N consecutive failures, a provider is automatically skipped for a cooldown period
- **Usage logging** — Every request is logged with provider, model, latency, and error information
- **API key encryption** — Provider API keys are encrypted at rest

## Quick Start

### Using pip

```bash
# Clone and install
cd openproxy
pip install -e .

# Generate an encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set up .env
cp .env.example .env
# Edit .env and paste your encryption key

# Run
uvicorn openproxy.main:app --host 0.0.0.0 --port 8000
```

### Using Docker

```bash
# Generate an encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set up .env
cp .env.example .env
# Edit .env and paste your encryption key

# Start
docker compose up -d
```

## Usage

### Configuration

1. Open the web UI at `http://localhost:8000`
2. Go to **Providers** → **Add Provider**
3. Enter a name, base URL, and API key (e.g., `https://api.openai.com`, `sk-...`)
4. Click **Auto-detect** to fetch available models
5. Enable/disable specific models as needed
6. Add more providers and reorder them by priority using the ↑↓ buttons

### Using with OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="ignored-by-proxy",  # Proxy handles auth per-provider
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /v1/chat/completions` | Chat completions with streaming + failover |
| `POST /v1/embeddings` | Embeddings with failover |
| `GET /v1/models` | List all enabled models across providers |
| `GET /api/providers` | List providers |
| `POST /api/providers` | Add a provider |
| `PUT /api/providers/{id}` | Update a provider |
| `POST /api/providers/{id}/toggle` | Enable/disable a provider |
| `POST /api/providers/{id}/detect-models` | Auto-detect models from provider |
| `POST /api/providers/{id}/models` | Manually add a model |
| `PUT /api/providers/{id}/models/{mid}/toggle` | Enable/disable a model |
| `GET /api/stats` | Usage statistics |

## Configuration

Settings are managed via environment variables (`.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/openaiproxy.db` | Database connection |
| `ENCRYPTION_KEY` | *(required)* | Fernet key for API key encryption |
| `CIRCUIT_BREAKER_THRESHOLD` | `3` | Failures before circuit breaker activates |
| `CIRCUIT_BREAKER_COOLDOWN` | `30` | Seconds to skip a failing provider |
| `DEFAULT_TIMEOUT` | `60` | Default request timeout |

## Architecture

```
Client App (OpenAI SDK)
      │
      ▼
┌─────────────────┐
│   OpenAIProxy   │  ← Single base URL
│  localhost:8000 │
└────────┬────────┘
         │
    Priority routing
    with failover
         │
    ┌────┴────┐
    ▼         ▼
Provider A  Provider B
(priority 1) (priority 2)

On error → auto-failover to next provider
```

## Development

```bash
pip install -e ".[dev]"
pytest
```
