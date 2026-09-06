FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 1000 agent \
    && useradd --uid 1000 --gid agent --create-home agent \
    && python -m pip install --no-cache-dir \
        pytest==8.4.1 \
        requests \
        httpx \
        pydantic \
        fastapi \
        flask \
        click \
        rich \
        numpy \
        pandas \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent
WORKDIR /workspace
CMD ["tail", "-f", "/dev/null"]
