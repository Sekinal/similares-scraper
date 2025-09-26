
**Similares Scraper** – a container‑based, async Python 3.12 scraper that queries VTEX’s `search‑graphql` endpoint to retrieve product listings. It uses HTTP/2‑capable **HTTPX** for high‑concurrency requests and **Supercronic** for cron‑style scheduling inside the container. The build and runtime workflow is managed with **Astral uv** for fast, reproducible environments and dependency handling.

---

### Features
- **Async HTTP client** with optional HTTP/2 support via HTTPX for efficient concurrent requests.  
- Targets VTEX `search‑graphql` endpoints that expose `product‑search` queries for VTEX Store Framework apps.  
- **Cron‑style scheduling** inside the container using Supercronic, a crontab‑compatible runner built for containers.  
- Reproducible Python environment and quick execution using uv installers and commands.  
- **Paginated outputs** plus a line‑delimited JSONL aggregate, well‑suited for streaming and large‑scale data processing.

---

### Architecture
| Component | Role |
|-----------|------|
| **HTTP client** | HTTPX with async support and optional HTTP/2, tuned via client limits and time‑outs. |
| **Scheduler** | Supercronic executes the scraper on a crontab‑defined cadence within the container. |
| **Packaging / runtime** | uv installer & commands for locking, syncing, and running the project efficiently. |
| **Orchestration** | Docker Compose mounts a persistent data directory and a read‑only secret file for proxies. |

---

## Quickstart (Docker)

**docker‑compose.yml** (example)

```yaml
services:
  similares-scraper:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: similares-scraper
    environment:
      - TZ=America/Mexico_City
    volumes:
      - ./data:/data
      - ./secrets/proxies.txt:/run/secrets/proxies.txt:ro
    restart: unless-stopped
```

1. Create an empty `data` directory and a `secrets/proxies.txt` file.  
2. Bring the service up in detached mode:

```bash
docker compose up -d
```

---

## Proxy configuration

HTTPX accepts a single proxy URL for all traffic on the client, using the standard URL form:

```
http://user:pass@host:port
```

A practical approach is to keep a text file where each line contains `host:port:user:pass`. The scraper reads this file, builds the appropriate HTTPX proxy URL for each request, and rotates proxies as needed.

**Example `proxies.txt** (mounted as a secret):

```
123.45.67.89:8000:alice:pass123
98.76.54.32:8080:bob:s3cr3t
```

---

## Running locally with uv

uv provides fast, standalone installers and a project runner. A typical local run uses `uv run` to execute the entry point with required flags:

```bash
# Ensure uv is installed (see docs), then from the project root:
uv run main.py \
  --proxies ./secrets/proxies.txt \
  --out ./data \
  --window 48 \
  --concurrency 8 \
  --order-by OrderByScoreDESC
```

The uv workflow emphasizes speed and reproducibility, simplifying Python version and dependency management for development and CI.

---

## Scheduling

Supercronic interprets a crontab file and runs the scraper inside the container at the configured schedule, keeping container environments intact and logs simple to surface. Adjust the schedule by editing the crontab file and rebuilding/redeploying the image.

---

## VTEX search notes

VTEX provides a `search‑graphql` contract used by the Store Framework; providers expose product‑search information through defined GraphQL queries and types in the `vtex.search‑graphql` schema. When building filters, VTEX’s schema and docs outline how external providers can supply search results and facet‑compatible behavior for storefront consumption.

---

## Output format

- **Per‑page JSON files** for each request.  
- **JSON Lines (JSONL)** file for de‑duplicated aggregation, ideal for streaming and line‑by‑line processing in data pipelines. JSONL stores one valid JSON value per line using UTF‑8 with newline terminators, enabling easy concatenation and incremental processing at scale.

---

## Performance tips

- Enable HTTP/2 with HTTPX to reduce overhead via multiplexing and header compression—beneficial when issuing many concurrent requests.  
- Use connection limits and structured time‑outs in HTTPX to balance throughput and resilience when scraping at scale.

---

## Notes

- Docker volumes are the recommended way to persist data and share it with other services or hosts in Compose‑driven deployments.  
- If adjusting proxies at runtime, remember that HTTPX supports per‑client or per‑request proxy configuration; keep credentials in the URL form to authenticate as needed.

---