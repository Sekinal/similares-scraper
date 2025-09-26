# Dockerfile
FROM python:3.12-slim-bookworm

# System deps: curl for installers, CA certs for HTTPS, tzdata for local time
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates tzdata && \
    rm -rf /var/lib/apt/lists/*

# Install uv (Astral) as recommended in docs
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin:${PATH}"

# Workdir and project files
WORKDIR /app
COPY pyproject.toml README.md ./

# Lock and sync at build for reproducible, cached installs
# If a uv.lock is not present, create it; then sync without dev deps
RUN uv lock || true && uv sync --no-dev

# Copy the rest of the source
COPY main.py ./main.py

# Install Supercronic (use latest release URL pattern; adjust version as needed)
# Latest releases are listed on aptible/supercronic releases
ENV SUPERCRONIC_VERSION="v0.2.36"
ENV SUPERCRONIC_URL="https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64"
RUN curl -fsSLo /usr/local/bin/supercronic "${SUPERCRONIC_URL}" && \
    chmod +x /usr/local/bin/supercronic

# Output directory inside container; a host volume will be mounted here
RUN mkdir -p /data && mkdir -p /var/log

# Copy crontab into place
COPY crontab /etc/crontab

# Timezone for the scheduler (can be overridden in compose)
ENV TZ=America/Mexico_City

# Run Supercronic by full path (required per noted bug)
CMD ["/usr/local/bin/supercronic", "-quiet", "/etc/crontab"]
