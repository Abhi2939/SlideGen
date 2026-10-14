FROM python:3.12-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y gosu \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SLIDEGEN_DATA_DIR=/var/data

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin slidegen

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/slidegen-entrypoint
RUN chmod 0755 /usr/local/bin/slidegen-entrypoint \
    && mkdir -p /var/data \
    && chown slidegen:slidegen /var/data

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/slidegen-entrypoint"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
