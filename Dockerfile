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

FROM builder AS verification
COPY requirements-remote-check.lock ./
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps --target /opt/check-deps -r requirements-remote-check.lock \
    && groupadd --gid 10001 canvas \
    && useradd --uid 10001 --gid canvas --no-create-home --shell /usr/sbin/nologin canvas
COPY tests/conftest.py tests/file_content_fixtures.py tests/unit/test_document_content.py tests/security/test_ephemeral_storage.py tests/integration/test_remote_file_content.py /verify/
USER 10001:10001
WORKDIR /verify
# The build must prove actual Linux file containment and killable parser isolation.
# Only generated synthetic documents are used; no Canvas/Auth0/network calls.
RUN PYTHONPATH=/opt/check-deps /opt/venv/bin/python -m pytest -q -p no:cacheprovider --tb=short /verify

FROM python:3.13-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=verification /opt/venv /opt/venv
RUN groupadd --gid 10001 canvas && useradd --uid 10001 --gid canvas --no-create-home --shell /usr/sbin/nologin canvas
USER 10001:10001
WORKDIR /opt/service
EXPOSE 8000
# HTTP defaults to loopback and deny-all auth. Container networking and development
# authentication require explicit run-time settings; no environment files copied.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request; p=int(os.environ.get('PORT',os.environ.get('MCP_HTTP_PORT','8000'))); r=urllib.request.urlopen('http://127.0.0.1:'+str(p)+'/health',timeout=2); assert r.status==200 and r.read()==b'{\"status\":\"ok\"}'"
CMD ["python", "-m", "canvas_mcp.mcp_http_server"]
