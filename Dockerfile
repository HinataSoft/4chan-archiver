FROM python:3.13-slim

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
