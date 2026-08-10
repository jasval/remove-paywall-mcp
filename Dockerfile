FROM python:3.13-slim

WORKDIR /app

COPY README.md LICENSE pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir -e .

ENV MCP_TRANSPORT=stdio
ENV MCP_DB_DIR=/data

RUN mkdir -p /data

EXPOSE 8000

ENTRYPOINT ["remove-paywall-mcp"]
