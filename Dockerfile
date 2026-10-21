FROM python:3.12-slim
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY data/fixtures ./data/fixtures
RUN pip install --no-cache-dir -r requirements.txt
COPY data/etf_whitelist.json ./data/etf_whitelist.json
COPY config/*.example.toml ./config/
COPY config/*.example.json ./config/
COPY scripts/*.py ./scripts/
COPY tests ./tests
RUN useradd --uid 1000 --create-home trademind \
    && mkdir -p /app/data/runtime \
    && chown -R trademind:trademind /app
USER trademind
EXPOSE 8321
ENTRYPOINT ["python", "-m", "trademind.runner"]
CMD ["--config", "config/t0-container.local.toml", "serve"]
