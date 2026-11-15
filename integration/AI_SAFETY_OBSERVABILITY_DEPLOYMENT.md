# AI Safety & Observability Layer — Deployment Guide

This document covers deploying the five new Phase 2 components for the
Elementary Particles personal AI assistant stack. These components add
RAG/Vector search, LLM output safety guardrails, a versioned prompt
store, interaction metrics, and a 30-45 day automated retraining cycle.

---

## Architecture Overview

```
┌─────────────────── web-01 (10.0.30.20) ──────────────────────────┐
│                                                                    │
│  rag-ingestor  :5250  ──► Qdrant  :6333/:6334                     │
│  guardrails    :5210  ──► PostgreSQL guardrails schema             │
│  prompt-store  :5230  ──► PostgreSQL prompt_store schema           │
│  ai-monitor    :5220  ──► PostgreSQL ai_monitor schema             │
│                       ──► Prometheus /metrics  (Grafana scrapes)   │
│  alertmanager-forwarder :5241 ──► NextCloud Talk "Tech Talk"      │
│                                                                    │
│  n8n :5678  runs:                                                  │
│    rag_ingestor_workflow.json      (every 15 min)                  │
│    monthly_retraining_workflow.json (1st of month, 09:00 UTC)      │
└────────────────────────────────────────────────────────────────────┘
          │
          ▼
   gpu-01 :11434  (Ollama — nomic-embed-text, qwen2.5, llama3.2)
   pg-01  :5432   (PostgreSQL — elementary_particles DB)
```

---

## Prerequisites

All services run on **web-01** alongside the existing assistant stack.

- Python 3.11+ with `pip`
- PostgreSQL 15 on pg-01 (existing)
- Qdrant v1.9.2 container (new — see §2)
- Ollama with `nomic-embed-text` model pulled (new model)
- n8n 1.x (existing)
- Vault with existing secret path structure

---

## 1. Database Migrations

Run each schema file against the `elementary_particles` database on pg-01.
Order does not matter — schemas are independent.

```bash
PGPASSWORD="$DB_PASSWORD" psql -h 10.0.40.10 -U postgres -d elementary_particles \
  -f integration/postgres/rag_store_schema.sql

PGPASSWORD="$DB_PASSWORD" psql -h 10.0.40.10 -U postgres -d elementary_particles \
  -f integration/postgres/guardrails_schema.sql

PGPASSWORD="$DB_PASSWORD" psql -h 10.0.40.10 -U postgres -d elementary_particles \
  -f integration/postgres/prompt_store_schema.sql

PGPASSWORD="$DB_PASSWORD" psql -h 10.0.40.10 -U postgres -d elementary_particles \
  -f integration/postgres/ai_monitor_schema.sql
```

**Verify:**
```sql
SELECT schema_name FROM information_schema.schemata
WHERE schema_name IN ('rag_store','guardrails','prompt_store','ai_monitor');
-- Should return 4 rows
```

---

## 2. Qdrant Vector Database

Qdrant is the only new infrastructure component. Deploy as a Docker container
on web-01 alongside existing services.

> **Note:** If `research_rag_api.py` (Research Brain, port 5090) is already
> running, it uses the **same Qdrant instance** with collections
> `research_papers` and `research_chunks`. There is no conflict — our
> personal-assistant collections use distinct names (`global_health_reference`,
> `global_habit_knowledge`, `global_nutrition_guide`, plus per-profile ones).
> Both services can share the same Qdrant container safely.

```bash
docker run -d \
  --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -p 6334:6334 \
  -v /opt/qdrant/storage:/qdrant/storage \
  qdrant/qdrant:v1.9.2
```

**Storage directory:**
```bash
sudo mkdir -p /opt/qdrant/storage
sudo chown 1000:1000 /opt/qdrant/storage
```

**Verify Qdrant is running:**
```bash
curl http://10.0.30.20:6333/health
# → {"title":"qdrant - vector search engine","version":"1.9.2"}
```

---

## 3. Ollama — Pull Embedding Model

The RAG ingestor uses `nomic-embed-text` for 768-dimension embeddings.

```bash
# On gpu-01 (10.0.30.50)
ollama pull nomic-embed-text

# Verify
ollama list | grep nomic
```

---

## 4. Python Service Dependencies

Install into the existing assistant virtualenv on web-01:

```bash
pip install \
  fastapi \
  uvicorn[standard] \
  asyncpg \
  httpx \
  pydantic \
  apscheduler \
  pypdf \
  python-docx
```

---

## 5. Vault Secrets

Store all service credentials in Vault under `secret/elementary-particles/`:

```bash
# RAG Ingestor
vault kv put secret/elementary-particles/rag-ingestor \
  db_dsn="postgresql://n8n:CHANGE@10.0.40.10:5432/elementary_particles" \
  qdrant_url="http://10.0.30.20:6333" \
  ollama_url="http://10.0.30.50:11434" \
  nextcloud_url="https://nextcloud.home.arpa" \
  nextcloud_user="admin" \
  nextcloud_password="CHANGE"

# Guardrails
vault kv put secret/elementary-particles/guardrails \
  db_dsn="postgresql://n8n:CHANGE@10.0.40.10:5432/elementary_particles" \
  ollama_url="http://10.0.30.50:11434" \
  ollama_model="llama3.2:3b-instruct-q4_K_M" \
  confidence_threshold="0.60" \
  toxicity_threshold="0.70"

# Prompt Store
vault kv put secret/elementary-particles/prompt-store \
  db_dsn="postgresql://n8n:CHANGE@10.0.40.10:5432/elementary_particles" \
  stale_days="30"

# AI Monitor
vault kv put secret/elementary-particles/ai-monitor \
  db_dsn="postgresql://n8n:CHANGE@10.0.40.10:5432/elementary_particles" \
  ollama_url="http://10.0.30.50:11434" \
  nextcloud_talk_url="https://nextcloud.home.arpa" \
  nextcloud_talk_token="YOUR_TALK_TOKEN" \
  nextcloud_bot_secret="YOUR_BOT_SECRET"
```

---

## 6. Systemd Service Units

Create a unit for each service. Example template (repeat for each):

```ini
# /etc/systemd/system/rag-ingestor.service
[Unit]
Description=RAG Ingestor — Elementary Particles
After=network.target qdrant.service

[Service]
Type=simple
User=assistant
WorkingDirectory=/opt/elementary-particles
EnvironmentFile=/etc/elementary-particles/rag-ingestor.env
ExecStart=/opt/elementary-particles/venv/bin/uvicorn \
    integration.rag_ingestor:app \
    --host 0.0.0.0 \
    --port 5250 \
    --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Environment files** (`/etc/elementary-particles/*.env`) should pull from
Vault Agent or be written by Ansible. Never commit secrets to git.

| Service         | Port | Unit file name           | Entry point                    |
|-----------------|------|--------------------------|--------------------------------|
| rag-ingestor    | 5250 | rag-ingestor.service     | integration.rag_ingestor:app   |
| guardrails      | 5210 | guardrails.service       | integration.guardrails_service:app |
| prompt-store    | 5230 | prompt-store.service     | integration.prompt_store:app   |
| ai-monitor      | 5220 | ai-monitor.service       | integration.ai_monitor:app     |

```bash
# Enable and start all four
for svc in rag-ingestor guardrails prompt-store ai-monitor; do
  sudo systemctl daemon-reload
  sudo systemctl enable "$svc"
  sudo systemctl start "$svc"
done

# Verify health
for port in 5210 5220 5230 5250; do
  curl -s "http://10.0.30.20:$port/health" | python3 -m json.tool
done
```

---

## 7. NextCloud — AI Knowledge Base Folder

The RAG ingestor polls this WebDAV path every 15 minutes:

```
/remote.php/dav/files/{user}/AI-Knowledge-Base/{profile}/
```

Create the folder structure in NextCloud:

```bash
# Via curl (WebDAV MKCOL)
NC="https://nextcloud.home.arpa"
USER="admin"
PASS="$(vault kv get -field=nextcloud_password secret/elementary-particles/rag-ingestor)"

for profile in alice bob global; do
  curl -u "$USER:$PASS" -X MKCOL "$NC/remote.php/dav/files/$USER/AI-Knowledge-Base/$profile/"
done
```

Drop PDF, DOCX, TXT, or MD files into the profile subdirectory.
The ingestor will pick them up within 15 minutes.

---

## 8. n8n Workflows

Import both workflow files via the n8n UI or CLI:

```bash
# CLI import (n8n must be running)
n8n import:workflow --input=integration/n8n_workflows/rag_ingestor_workflow.json
n8n import:workflow --input=integration/n8n_workflows/monthly_retraining_workflow.json
```

**Required n8n credentials:**

| Credential name          | Type            | Used by                          |
|--------------------------|-----------------|----------------------------------|
| `nextcloud-webdav`       | HTTP Basic Auth | RAG Ingestor workflow (WebDAV)   |
| `vikunja-jwt`            | HTTP Header Auth| Monthly Retraining (task create) |

Set the `Authorization: Bearer <token>` header in the `vikunja-jwt` credential.
The token can be obtained from Vault: `vault kv get secret/task-manager/{profile}`.

**Activate both workflows** after import (toggle the Active switch in the UI).

---

## 9. Grafana Dashboard

The `ai-monitor` service exposes Prometheus metrics at `:5220/metrics`.

The project already has `integration/prometheus_scrape_config.yml`. Add the
`ai-monitor` job to the existing `scrape_configs` block in that file:

```yaml
# Add to the existing scrape_configs in integration/prometheus_scrape_config.yml
  - job_name: 'ai-monitor'
    static_configs:
      - targets: ['10.0.30.20:5220']
    metrics_path: /metrics
    scrape_interval: 30s
    labels:
      system: 'ai-assistant'
      service: 'ai-monitor'
```

After editing, reload Prometheus:
```bash
curl -X POST http://localhost:9090/-/reload
```

**Key metrics for dashboard panels:**

| Panel                    | Metric                                |
|--------------------------|---------------------------------------|
| Task Completion Rate     | `ai_assistant_tcr_rate`               |
| Interaction Volume       | `ai_assistant_interactions_total`     |
| Response Latency         | `ai_assistant_avg_latency_ms`         |
| Drift Status             | `ai_assistant_drift_flag`             |
| Feedback Ratio           | `ai_assistant_thumbs_up_total` / `ai_assistant_thumbs_down_total` |
| Input Sentiment Trend    | `ai_assistant_avg_sentiment`          |

---

## 10. Integration Wiring

Once all services are running, update existing assistant services to use
the new components:

### Use Prompt Store for system prompts

In each existing assistant service, replace hard-coded system prompt
strings with a startup call to prompt-store:

```python
import httpx

async def get_system_prompt(service: str, profile: str) -> str:
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"http://10.0.30.20:5230/prompt/{service}/{profile}")
        if r.status_code == 200:
            return r.json()["prompt"]
    return DEFAULT_PROMPT  # fallback
```

### Route LLM responses through Guardrails

```python
async def safe_ollama_response(prompt: str, response: str, context_chunks: list,
                                profile: str, service: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post("http://10.0.30.20:5210/validate", json={
            "prompt": prompt,
            "response": response,
            "context_chunks": context_chunks,
            "profile": profile,
            "service": service,
        })
        if r.status_code == 200:
            return r.json()["safe_response"]
    return response  # fail-open
```

### Log interactions to AI Monitor

```python
async def log_interaction(profile: str, service: str, latency_ms: int,
                           guardrails_action: str = "pass") -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await client.post("http://10.0.30.20:5220/event", json={
            "profile_name": profile,
            "service": service,
            "latency_ms": latency_ms,
            "guardrails_action": guardrails_action,
        })
```

---

## 11. Post-Deployment Verification Checklist

- [ ] `curl http://10.0.30.20:5210/health` → `{"status":"ok","service":"guardrails"}`
- [ ] `curl http://10.0.30.20:5220/health` → `{"status":"ok","service":"ai-monitor"}`
- [ ] `curl http://10.0.30.20:5230/health` → `{"status":"ok","service":"prompt-store"}`
- [ ] `curl http://10.0.30.20:5250/health` → `{"status":"ok","service":"rag-ingestor"}`
- [ ] `curl http://10.0.30.20:6333/health` → Qdrant health JSON
- [ ] `curl http://10.0.30.20:5230/prompts/stale` → returns array (may be empty)
- [ ] `curl http://10.0.30.20:5220/metrics` → Prometheus text output
- [ ] Qdrant collections: `curl http://10.0.30.20:6333/collections` → 3 collections seeded
- [ ] n8n RAG workflow activated — check execution log after 15 min
- [ ] n8n Monthly workflow: trigger manually and verify NextCloud Talk post
- [ ] Grafana: `ai_assistant_interactions_total` panel shows data after first test event
- [ ] Guardrails rules seeded: `curl http://10.0.30.20:5210/rules` → 6+ rules
- [ ] Prompt store seeded: `curl http://10.0.30.20:5230/prompt/proactive-intelligence` → CRISPE prompt

---

## Troubleshooting

### Qdrant collection not created on first ingest
The ingestor auto-creates missing collections. If it fails, check:
```bash
curl -s http://10.0.30.20:5250/collections
```
Collections are created with `distance: Cosine` and 768 dimensions
(nomic-embed-text output size).

### Guardrails blocking all responses
Check if block rules are too aggressive:
```bash
curl http://10.0.30.20:5210/rules | python3 -m json.tool
# Toggle a rule off:
curl -X PUT http://10.0.30.20:5210/rules/{id}/toggle
```

### Prompt Store returning 404
The seed prompts are inserted by `prompt_store_schema.sql` but not activated.
Activate the seed prompts:
```bash
# Get IDs
psql -h 10.0.40.10 -U n8n elementary_particles \
  -c "SELECT id, service, version FROM prompt_store.prompts ORDER BY id;"

# Activate each (replace IDs)
curl -X PUT "http://10.0.30.20:5230/prompt/1/activate?reason=initial+seed"
```

### ai-monitor drift cron not firing
The scheduler requires the service to be continuously running. Check:
```bash
sudo journalctl -u ai-monitor -n 50
```
Manually trigger: `curl -X POST http://10.0.30.20:5220/drift/compute`
