# The feeder as a container, for a host that is always on. Written for Railway; NOT YET RUN
# THERE. First used to test whether Whisker accepts a login from a data centre.
#
# Build from the repo root (the root .dockerignore keeps .env, the token and logs/ out):
#   docker build -f deploy/feeder/Dockerfile -t arthur-feeder .
#
# It runs in /data, so the Whisker token and logs/ land there. Mount a volume at /data to
# keep them across restarts. A volume also stops Railway running the old and the new copy at
# once during a deploy; Whisker allows one connection per account.
#
# Set in the host's own secret store, typed in by the owner, never in a file here:
#   WHISKER_USERNAME, WHISKER_PASSWORD, CLOCK_URL
# Run one copy per Whisker account, and stop any other feeder first.

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first, so a code change does not download them again.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# The code and config/display.toml, which arthur/config.py finds beside the package.
COPY arthur ./arthur
COPY config ./config

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data
WORKDIR /data

CMD ["python", "-m", "arthur.feeder"]
