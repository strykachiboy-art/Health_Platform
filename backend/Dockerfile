FROM rust:1.99.1-slim-bookworm AS clinical_print_builder
WORKDIR /src
COPY rust/clinical_print_renderer/Cargo.toml rust/clinical_print_renderer/Cargo.lock ./
COPY rust/clinical_print_renderer/src ./src
RUN cargo build --locked --release

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

COPY requirements.txt requirements.lock ./

RUN apt-get -o Acquire::Retries=10 update && \
    apt-get -o Acquire::Retries=10 install -y --no-install-recommends ca-certificates curl && \
    mkdir -p /usr/share/postgresql-common/pgdg && \
    curl --retry 10 --fail --silent --show-error \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
    printf '%s\n' \
        'Types: deb' \
        'URIs: https://apt.postgresql.org/pub/repos/apt' \
        'Suites: trixie-pgdg' \
        'Architectures: amd64' \
        'Components: main' \
        'Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc' \
        > /etc/apt/sources.list.d/pgdg.sources && \
    apt-get -o Acquire::Retries=10 update && \
    apt-get -o Acquire::Retries=10 install -y --no-install-recommends postgresql-client-18 fonts-dejavu-core fonts-liberation2 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.lock

COPY . .
COPY --from=clinical_print_builder /src/target/release/clinical_print_renderer /usr/local/bin/clinical_print_renderer

RUN useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", "--bind", "0.0.0.0:5000", "wsgi:app"]
