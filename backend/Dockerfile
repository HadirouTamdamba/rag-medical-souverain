# syntax=docker/dockerfile:1.7

# ----------------------------------------------------------------------------
# Optional context of pre-downloaded wheels.
#
# `backend/wheels/` (~500 MB) is excluded by .dockerignore: it must neither
# travel in the build context on every `docker build`, nor end up in the image.
# Compose injects it as a named context (`additional_contexts`), which replaces
# this `scratch` stage. Without that context /wheels is empty and pip simply
# falls back to PyPI.
# ----------------------------------------------------------------------------
FROM scratch AS wheels


# ----------------------------------------------------------------------------
# Stage 1 — build the Python environment
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv "$VIRTUAL_ENV"

COPY requirements.txt /tmp/requirements.txt

# torch first, from PyTorch's CPU index.
#
# The torch wheel published on PyPI declares the entire CUDA stack as
# dependencies (nvidia-cublas, cudnn, nccl, triton...), i.e. ~3.6 GB shipped for
# nothing in an image that will never see a GPU. The `+cpu` variant does not
# declare them. Installed first, it satisfies the constraint so the next step
# does not fall back to the PyPI wheel.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN --mount=from=wheels,target=/wheels \
    grep -E '^torch==' /tmp/requirements.txt > /tmp/torch.txt \
    && pip install --no-cache-dir --retries 10 --timeout 120 \
        --find-links=/wheels \
        --index-url "${TORCH_INDEX_URL}" \
        --extra-index-url https://pypi.org/simple \
        -r /tmp/torch.txt

# The bind mount exposes the wheels during installation without creating a
# layer: nothing from that directory ends up in the image.
RUN --mount=from=wheels,target=/wheels \
    pip install --no-cache-dir --retries 10 --timeout 120 \
        --find-links=/wheels -r /tmp/requirements.txt \
    && python -c "import torch; assert '+cpu' in torch.__version__, f'CUDA wheel installed: {torch.__version__}'; print('torch', torch.__version__)"

# The embedding model is baked into the image: an air-gapped hospital has no
# access to HuggingFace at startup, and without this the first `docker run`
# would fail or hang for several minutes. Set to 0 for a fast CI build.
ARG PREFETCH_EMBEDDING_MODEL=1
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV HF_HOME=/opt/hf-cache
RUN if [ "$PREFETCH_EMBEDDING_MODEL" = "1" ]; then \
        python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('$EMBEDDING_MODEL')"; \
    else \
        mkdir -p "$HF_HOME"; \
    fi


# ----------------------------------------------------------------------------
# Stage 2 — runtime image
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf-cache \
    # Model already present: forbid any network call to HuggingFace.
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN groupadd --system --gid 1001 appuser \
    && useradd --system --uid 1001 --gid appuser --create-home appuser

COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appuser /opt/hf-cache /opt/hf-cache

WORKDIR /app
COPY --chown=appuser:appuser *.py docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

USER appuser
EXPOSE 8000

# Liveness only: /ready depends on Qdrant and Ollama, and the orchestrator must
# use it separately to decide whether to route traffic.
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"

ENTRYPOINT ["./docker-entrypoint.sh"]
