# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements-remote.lock pyproject.toml ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps -r requirements-remote.lock \
    && /opt/venv/bin/pip install --no-cache-dir setuptools==84.0.0 packaging==26.2
COPY src/ ./src/
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps --no-build-isolation . \
    && /opt/venv/bin/pip check

FROM python:3.13-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=builder /opt/venv /opt/venv
RUN groupadd --gid 10001 canvas && useradd --uid 10001 --gid canvas --no-create-home --shell /usr/sbin/nologin canvas
USER 10001:10001
WORKDIR /opt/service
EXPOSE 8000
# HTTP defaults to loopback and deny-all auth. Container networking and development
# authentication require explicit run-time settings; no environment files copied.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request; p=int(os.environ.get('PORT',os.environ.get('MCP_HTTP_PORT','8000'))); r=urllib.request.urlopen('http://127.0.0.1:'+str(p)+'/health',timeout=2); assert r.status==200 and r.read()==b'{\"status\":\"ok\"}'"
CMD ["python", "-m", "canvas_mcp.mcp_http_server"]
