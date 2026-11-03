# The analysis sandbox (Loop 32): the base image plus columnar tools for big logs. No network at run time, so what
# the agent can use is what is here. Our own scripts are not baked in: sandbox/vtools is mounted read-only (/vtools).
# Tag: agent/sandbox.py IMAGE (the hash of this file); deploy/install.sh builds it.
FROM python:3.12-slim
# a pip mirror, if the server uses one (the proxy variables are passed by Docker without being declared)
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
RUN pip install --no-cache-dir polars==1.35.2 duckdb==1.4.2 pyarrow==22.0.0 pandas==2.3.3 numpy==2.3.5 \
 && python -c "import polars, duckdb, pyarrow, pandas"
