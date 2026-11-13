FROM python:3.12-slim AS production

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser

COPY requirements.txt ./
# mcp is pinned: mcp-proxy 0.15.0 needs mcp>=1.30,<2, requirements.txt allows >=1.0
RUN pip install -r requirements.txt "mcp-proxy==0.15.0" "mcp==1.30.0"

COPY server.py tools_direct_extra.py tools_metrika.py tools_audience.py ./

USER appuser
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4)"

# PUBLIC_HOST = public hostname without scheme, set in Dokploy env.
CMD exec mcp-proxy \
      --host=0.0.0.0 --port=8765 \
      --pass-environment \
      --allowed-host="${PUBLIC_HOST:?PUBLIC_HOST must be set}" \
      --allowed-host="${PUBLIC_HOST:?}:*" \
      --allow-origin="https://${PUBLIC_HOST:?}" \
      -- python server.py