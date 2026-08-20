############################
# Builder
############################

FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build


RUN python -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    # runtime never needs pip/setuptools/wheel — strip them from the venv
    pip uninstall -y pip setuptools wheel


############################
# Runtime
############################

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Update Debian packages to patched versions,
# then create the non-root application user.
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 1001 appgroup && \
    useradd \
        --uid 1001 \
        --gid appgroup \
        --create-home \
        --shell /usr/sbin/nologin \
        appuser && \
    # strip pip/setuptools shipped by the base image's system Python too
    rm -rf /usr/local/lib/python3.14/site-packages/pip* \
           /usr/local/lib/python3.14/site-packages/setuptools* \
           /usr/local/bin/pip*

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Less frequently changing files first
COPY --chown=appuser:appgroup alembic ./alembic
COPY --chown=appuser:appgroup alembic.ini .

# Most frequently changing code last
COPY --chown=appuser:appgroup app ./app

USER 1001:1001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]