# dev-guide-qa server for AgentCore Runtime (MCP protocol: 0.0.0.0:8000/mcp). arm64 image (AgentCore requirement).
# pip instead of uv: uv hangs under qemu arm64 emulation on an amd64 build host (2026-11-03).
# Layout follows aiops-multi-agent-workshop agents/*/runtime/Dockerfile (non-root user, EXPOSE 8000).
# The same image runs on AgentCore Runtime (platform V2) or on any container host (on-premise, EKS).
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DOCKER_CONTAINER=1
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore
EXPOSE 8000
COPY server.py server.py
COPY data data
CMD ["python", "server.py"]
