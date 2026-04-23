FROM python:3.12-slim

WORKDIR /app

# Cria virtualenv isolado — sem ambiguidade de paths
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Instala dependências primeiro (layer de cache separada do código)
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Copia apenas o que o servidor precisa
COPY server/      ./server/
COPY shared/      ./shared/
COPY config.toml  ./config.toml
COPY run_server.py ./run_server.py

EXPOSE 8765

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1

CMD ["python", "run_server.py"]
