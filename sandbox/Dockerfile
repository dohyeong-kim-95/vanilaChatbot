# The analysis sandbox (Loop 32): the base image plus columnar tools for big logs. No network at run time, so what
# the agent can use is what is here. Our own scripts are not baked in: sandbox/vtools is mounted read-only (/vtools).
# Tag: agent/sandbox.py IMAGE (the hash of this file and of stdf-rs); deploy/install.sh builds it.

# The Rust STDF reader (Loop 34): built here, only the binary goes into the image
FROM rust:1.94 AS stdf
WORKDIR /src
COPY stdf-rs/Cargo.toml stdf-rs/Cargo.lock ./
COPY stdf-rs/src ./src
# needs crates.io once (or CARGO_REGISTRIES_CRATES_IO_PROTOCOL/a mirror via build args); if it cannot build, the image
# is made without it and ingest falls back to the Python reader (same tables, ~25x slower)
ARG CARGO_HTTP_PROXY
RUN mkdir /out && (cargo build --release --locked && strip target/release/stdf2parquet \
    && cp target/release/stdf2parquet /out/ || echo "warning: stdf2parquet not built; the Python STDF reader is used")

FROM python:3.12-slim
# procps: pkill/ps, which Sandbox.exec uses to stop a command that timed out
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*
# a pip mirror, if the server uses one (the proxy variables are passed by Docker without being declared)
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
RUN pip install --no-cache-dir polars==1.35.2 duckdb==1.4.2 pyarrow==22.0.0 pandas==2.3.3 numpy==2.3.5 \
 && python -c "import polars, duckdb, pyarrow, pandas"
# SBDF (Spotfire's data file) for export_spotfire: spotfire-python, BSD-3, x86_64 Linux wheels (docs/research/R7 §4)
RUN pip install --no-cache-dir spotfire==2.4.2 && python -c "import spotfire.sbdf"
COPY --from=stdf /out/ /usr/local/bin/
