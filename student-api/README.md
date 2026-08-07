# Student API

A Student CRUD REST API built with **FastAPI**, **SQLAlchemy 2.0**, **Alembic**, and **PostgreSQL**, targeting **Python 3.14**. It was built as a Twelve-Factor-App-style learning exercise: versioned REST endpoints, config via environment variables, DB migrations, structured logging, and unit tests.

## Table of Contents

- [Why UUIDv7 for the primary key](#why-uuidv7-for-the-primary-key)
- [Project structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Local setup](#local-setup)
- [The Makefile, explained](#the-makefile-explained)
- [Database migrations with Alembic](#database-migrations-with-alembic)
- [Configuration](#configuration)
- [API endpoints](#api-endpoints)
- [Postman collection](#postman-collection)
- [Running the tests](#running-the-tests)
- [Logging](#logging)
- [Docker image](#docker-image)

## Why UUIDv7 for the primary key

The `students.id` column is a `UUID`, generated with Python 3.14's native `uuid.uuid7()` (see `app/models/student.py`), instead of the more common `uuid4()`.

The reason is **index locality**. A `uuid4` is fully random, so every insert lands at a random point in the primary key's B-tree index — that causes constant page splits, poor buffer-cache hit rates, and index fragmentation as the table grows. `uuid7` embeds a **48-bit millisecond Unix timestamp** in the leading bits of the value, so IDs generated close together in time sort close together lexicographically. That makes inserts append-mostly (like an auto-increment integer), which keeps the primary key index compact and cache-friendly, while still giving every row a globally unique, non-guessable, non-sequential-looking identifier (unlike a plain auto-increment ID, it doesn't leak row counts or let you enumerate other students by incrementing a number). This is why `uuid7` was standardized in RFC 9562 and added to Python's stdlib `uuid` module in 3.14 — you get the operational benefits of a sequential key with the safety properties of a random one.

## Project structure

```
Test-main/
├── docker-compose.yaml          # Local Postgres 17 for development
├── README.md                    # You are here
└── student-api/
    ├── Dockerfile                # Multi-stage, non-root production image
    ├── .dockerignore             # Keeps build context/image lean
    ├── Makefile                 # build / run / migrate / test shortcuts
    ├── requirements.in          # unpinned source dependency list
    ├── requirements.txt         # pinned dependencies (installed by make install)
    ├── alembic.ini               # Alembic configuration
    ├── pytest.ini                 # pytest configuration
    ├── .env.example               # template for local .env
    ├── alembic/
    │   ├── env.py                 # wires Alembic to app.config.settings + app.db.Base
    │   └── versions/               # migration scripts
    ├── app/
    │   ├── main.py                 # FastAPI app, lifespan, router registration
    │   ├── config.py                # Settings (env-var driven, pydantic-settings)
    │   ├── db.py                     # engine, SessionLocal, Base, get_db dependency
    │   ├── logger.py                  # structlog JSON logging setup
    │   ├── exceptions.py               # global exception handlers
    │   ├── models/student.py            # SQLAlchemy ORM model (uuid7 PK)
    │   ├── schemas/student.py            # Pydantic request/response schemas
    │   └── api/v1/
    │       ├── health.py                  # /api/v1/health, /api/v1/ready
    │       └── students.py                 # /api/v1/students CRUD routes
    ├── tests/
    │   ├── conftest.py                      # SQLite-backed TestClient fixtures
    │   ├── test_health.py
    │   └── test_students.py
    └── postman/
        └── Student-API.postman_collection.json
```

## Prerequisites

- Python 3.14
- Docker (for the local Postgres instance) — or a Postgres 17 instance of your own
- `make`

## Local setup

```bash
# 1. Clone and enter the API directory
git clone <your-repo-url>
cd student-api

# 2. Create your local env file
cp .env.example .env
# edit .env if your DB credentials differ from the defaults

# 3. Start Postgres yourself, e.g. from the repo root:
docker compose up -d postgres

# 4. Create the virtualenv and install dependencies
make install

# 5. Apply DB migrations to create the students table
make migrate

# 6. Run the API with hot-reload
make dev
```

The API is now available at `http://localhost:8000`, with interactive docs at `http://localhost:8000/docs`.

## The Makefile, explained

All targets are run from inside `student-api/`. Run `make help` to list them with descriptions.

| Target | What it does |
|---|---|
| `make venv` | Creates a `.venv` virtual environment using `python3.14`. |
| `make install` | Runs `venv`, then `pip install -r requirements.txt` into it. This is the target the assignment's "build" requirement maps to — `make build` is provided as an alias. |
| `make migrate` | Runs `alembic upgrade head` — applies every migration that hasn't been applied yet, creating/updating the `students` table. |
| `make migrate-down` | Runs `alembic downgrade -1` — rolls back the single most recent migration. |
| `make revision m="message"` | Autogenerates a new Alembic migration by diffing the ORM models against the live DB schema, e.g. `make revision m="add grade column"`. |
| `make run` | Starts the API with plain `uvicorn`, no reload — closer to how it'd run in production. |
| `make dev` | Starts the API with `--reload`, for local development. |
| `make test` | Runs the pytest suite (`pytest -v`). |
| `make test-cov` | Runs the suite with a coverage report (`--cov=app --cov-report=term-missing`). |
| `make lint` | A cheap sanity check — byte-compiles every file under `app/` to catch syntax errors early. |
| `make clean` | Removes the venv, `__pycache__`, and pytest/coverage caches. |

All of the venv/pip/uvicorn/alembic/pytest binaries are referenced from `.venv/bin/`, so you never need to manually activate the virtual environment to use any target — `make dev`, `make migrate`, `make test`, etc. all just work.

## Database migrations with Alembic

Alembic manages the `students` table's schema as versioned, ordered Python scripts under `alembic/versions/`, instead of hand-run SQL.

**How it's wired up** (`alembic/env.py`):
- It imports `app.config.settings` and overrides `sqlalchemy.url` at runtime with `settings.DATABASE_URL`, so the DB connection is controlled by the exact same environment variable the app itself uses — no separate/duplicated DB config to keep in sync.
- It imports `app.db.Base` and `app.models.student.Student` so SQLAlchemy's metadata registry is populated. This is what makes `--autogenerate` possible: Alembic diffs `Base.metadata` (what your ORM models say the schema *should* be) against the actual database (what it currently *is*), and generates the migration script for the difference.

**Everyday workflow:**

```bash
# Apply all migrations up to the latest ("head")
make migrate

# You changed a model (e.g. added a field to Student) — generate a migration for it
make revision m="add phone number to students"
# review the generated file under alembic/versions/ before applying it,
# autogenerate is a helpful starting point, not infallible

# Apply the new migration
make migrate

# Made a mistake / need to roll back the last migration
make migrate-down
```

Under the hood these map directly to Alembic CLI commands (`alembic upgrade head`, `alembic revision --autogenerate -m "..."`, `alembic downgrade -1`) — the Makefile just saves you from remembering the venv path and flags.

The existing migration, `d76f130ed25a_create_students_table.py`, creates the `students` table with columns `id` (UUID PK), `first_name`, `last_name`, `email` (unique + indexed), `age`, `created_at`, `updated_at`.

## Configuration

Nothing is hardcoded — every setting is read from the environment (via `app/config.py`, backed by `pydantic-settings`, which also reads a local `.env` file if present):

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `Student API` | Used in the root endpoint and OpenAPI title. |
| `APP_VERSION` | `1.0.0` | Reported by the root endpoint. |
| `APP_ENV` | `development` | Environment label (`development`, `test`, `production`, ...). |
| `DEBUG` | `false` | FastAPI debug mode. |
| `LOG_LEVEL` | `INFO` | One of `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`. |
| `DB_DRIVER` | `postgresql+psycopg` | SQLAlchemy dialect+driver. |
| `DB_HOST` | `localhost` | Database host. |
| `DB_PORT` | `5432` | Database port. |
| `DB_NAME` | *required, no default* | Database name. |
| `DB_USER` | *required, no default* | Database username. |
| `DB_PASSWORD` | *required, no default* | Database password (typed as `SecretStr`, so it never appears in logs, `repr()`, or error messages). |

`DATABASE_URL` is **not** an environment variable anymore — it's a `@computed_field` on `Settings` that builds the SQLAlchemy connection string from the `DB_*` fields above via `sqlalchemy.engine.URL.create()`, rather than being read directly from the environment as a single pre-built string. This mirrors how secret managers like Vault + External Secrets Operator hand you credentials: separate fields, not one opaque connection string — and `URL.create()` correctly percent-encodes special characters (`@`, `:`, `/`) in the password, which naive string concatenation would break. `app/db.py` and `alembic/env.py` are unaffected by this change — they read `settings.DATABASE_URL` exactly as before, since a computed field is accessed the same way as a regular one.

Copy `.env.example` to `.env` and adjust as needed — `.env` is git-ignored.

## API endpoints

All resource endpoints are versioned under `/api/v1`, per the assignment's versioning requirement.

| Method | Path | Description | Success | Errors |
|---|---|---|---|---|
| `GET` | `/api/v1/health` | Liveness probe — is the process up. | `200` | – |
| `GET` | `/api/v1/ready` | Readiness probe — runs `SELECT 1` against the DB. | `200` | `503` if DB unreachable |
| `POST` | `/api/v1/students` | Create a student. | `201` | `409` duplicate email, `422` invalid body |
| `GET` | `/api/v1/students` | List all students. | `200` | – |
| `GET` | `/api/v1/students/{student_id}` | Get one student by UUID. | `200` | `404` not found, `422` malformed UUID |
| `PUT` | `/api/v1/students/{student_id}` | Partially update a student (any subset of fields). | `200` | `404` not found, `409` duplicate email, `422` invalid body |
| `DELETE` | `/api/v1/students/{student_id}` | Delete a student. | `204` | `404` not found |

Standard, semantically-correct HTTP verbs are used throughout: `POST` for creation, `GET` for reads, `PUT` for updates, `DELETE` for removal. Full interactive documentation (generated from the code) is available at `/docs` (Swagger UI) and `/redoc` while the app is running.

**Student payload shape** (`StudentCreate` / `StudentUpdate` / `StudentResponse` in `app/schemas/student.py`):

```json
{
  "first_name": "Ada",
  "last_name": "Lovelace",
  "email": "ada.lovelace@example.com",
  "age": 28
}
```

`PUT` accepts any subset of these fields — only the fields you send are updated (`model_dump(exclude_unset=True)`), the rest are left untouched.

## Postman collection

Import `student-api/postman/Student-API.postman_collection.json` into Postman. It includes:

- **Health** — Liveness and Readiness probes.
- **Students** — Create, Get All, Get By ID, Update, Delete, plus a Get-By-ID-Not-Found example.

It uses a `base_url` collection variable (defaults to `http://localhost:8000`) and a `student_id` variable that's **auto-populated** by a test script on the "Create Student" request — so you can run Create, then immediately run Get/Update/Delete without manually copying the ID. Change `base_url` if you're running the API somewhere other than `localhost:8000`.

## Running the tests

```bash
make test          # verbose pytest run
make test-cov       # with a coverage report
```

The suite (`tests/`) covers every endpoint: successful creates/reads/updates/deletes, the 404-not-found path, the 409-duplicate-email path, and 422 validation failures — including a check that generated IDs really are version-7 UUIDs.

Tests don't touch your dev/prod Postgres database at all. `tests/conftest.py` sets dummy `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` values just so `Settings()` doesn't fail its required-field validation on import — the app itself is then pointed at a separate in-memory SQLite engine (via `StaticPool`, so every session — including ones FastAPI opens in its threadpool — shares the same in-memory DB) through an override of the `get_db` dependency. The schema is created fresh and torn down around every single test function, so tests can never leak state (e.g. a "duplicate email" from one test bleeding into the next).

## Logging

`app/logger.py` configures `structlog` to emit structured JSON logs: `DEBUG`/`INFO`/`WARNING` go to `stdout`, `ERROR`/`CRITICAL` go to `stderr`, with the minimum level controlled by `LOG_LEVEL`. Every request-handling code path (student created, not found, duplicate email, DB errors, unhandled exceptions, etc.) logs a structured event, and `app/exceptions.py` adds global handlers so even framework-level validation failures and unhandled exceptions produce a proper structured log line and a clean JSON error response instead of an unstructured stack trace.

## Docker image

The `Dockerfile` builds a small, non-root production image using a **two-stage build**: a `builder` stage that compiles/installs dependencies, and a `runtime` stage that only contains what's needed to actually run the app. Build with:

```bash
docker build -t student-api .
docker run --env-file .env -p 8000:8000 student-api
```

### Stage 1 — `builder`

```dockerfile
FROM python:3.14-slim AS builder
```
`slim` (not the full `python:3.14` image, and not `alpine`) is the standard middle ground: it strips out compilers/docs/locales the full image ships with (much smaller, smaller attack surface), while still being a normal Debian base — unlike Alpine, which uses musl libc and regularly causes subtle breakage with C-extension Python packages (psycopg, cryptography, etc.) that expect glibc. `AS builder` names this stage so the runtime stage can selectively copy *only* its output later, instead of the whole stage.

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
```
- `PYTHONDONTWRITEBYTECODE=1` — skips writing `.pyc` files. In a container the filesystem is rebuilt from scratch every run, so cached bytecode from a previous run is never reused anyway; writing it just wastes image layers and I/O.
- `PYTHONUNBUFFERED=1` — makes `stdout`/`stderr` unbuffered, so log lines (your `structlog` JSON output) reach `docker logs`/`kubectl logs` immediately instead of sitting in a buffer — important for real-time log tailing and for logs not to appear to "hang" then dump all at once.
- `PIP_DISABLE_PIP_VERSION_CHECK=1` — stops pip from making a network call on every invocation just to check if a newer pip exists. Pure build-speed/noise reduction.

```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev && \
    rm -rf /var/lib/apt/lists/*
```
- `build-essential` (gcc, make, etc.) and `libpq-dev` (Postgres client headers) are needed **only if `pip install` has to compile a package from C source**. This matters a lot for this specific project: it targets **Python 3.14**, which is extremely new — some packages (including `psycopg`, even the `[binary]` extra) may not yet publish a prebuilt wheel for 3.14 on every platform, in which case pip silently falls back to building from source. Without these two packages, that fallback would fail with a cryptic compiler-not-found or `pg_config: not found` error. Including them makes the build robust across architectures/wheel availability instead of only working by luck.
- `--no-install-recommends` skips apt's "recommended but not required" extra packages — smaller layer, faster install.
- `apt-get update && apt-get install ... && rm -rf /var/lib/apt/lists/*` **in one `RUN` line** (not three separate `RUN`s) is a deliberate Docker layer-caching detail: each `RUN` becomes one image layer, and a layer's contents are fixed once written. If `apt-get update` were its own layer, its cached index would go stale over time while still being reused by cache, and `rm -rf /var/lib/apt/lists/*` in a *later* layer wouldn't actually shrink the image — deleted files in a later layer don't remove the bytes from earlier layers, they just hide them. Chaining update → install → cleanup into a single `RUN`/single layer means the apt index never ends up baked into the image at all.

```dockerfile
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
```
Installing into an isolated venv (rather than system site-packages) means the runtime stage can grab dependencies with one clean `COPY --from=builder /opt/venv /opt/venv` — a single, well-defined directory — instead of having to figure out which files under `/usr/lib/python3.14/site-packages` belong to your app versus the base image.

```dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```
This is the key **layer-caching** decision in the whole file: `requirements.txt` is copied and installed *before* any application code is copied. Docker caches each layer keyed on its instruction + the content it touches. As long as `requirements.txt` hasn't changed, Docker reuses this (often slow, since it may compile things) layer from cache on every rebuild — even if every line of `app/` changed. If `COPY app ./app` happened before this `pip install`, any code change would bust the cache and force a full dependency reinstall every single build. `--no-cache-dir` tells pip itself not to keep its own download cache on disk — irrelevant to Docker's layer cache, just keeps this layer's size down since the cache would otherwise sit in the image unused.

### Stage 2 — `runtime`

```dockerfile
FROM python:3.14-slim
```
Starts a **fresh** `slim` image — none of the builder stage's layers (compilers, `apt` cache, `libpq-dev` headers) carry over unless explicitly copied. This is the entire point of a multi-stage build: the final image only contains what's listed below, nothing used just to *get there*.

```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 && \
    rm -rf /var/lib/apt/lists/*
```
`libpq5` is the Postgres **client shared library** (as opposed to `libpq-dev`, which is the *development headers* used only at compile time). If `psycopg` ended up dynamically linked against `libpq` during the builder stage's install (the source-build fallback case described above), the runtime image needs this shared library present to actually load and run — without it you'd get an `ImportError`/`OSError` about a missing `.so` file the first time the app tries to open a DB connection. Note this is the *runtime* library only, not the dev headers — smaller and correct for a stage that doesn't compile anything itself.

```dockerfile
RUN groupadd --gid 1001 appgroup && \
    useradd \
        --uid 1001 \
        --gid appgroup \
        --create-home \
        --shell /usr/sbin/nologin \
        appuser
```
- Fixed, explicit `--gid 1001`/`--uid 1001` (rather than letting the OS auto-assign the next free ID) keeps the UID:GID stable and predictable across image rebuilds and across environments — this matters if you later pin `runAsUser: 1001` / `runAsGroup: 1001` in a Kubernetes `securityContext`, or if a mounted volume's file ownership needs to match the container's user consistently.
- `--create-home` gives the user a real `/home/appuser`, which some libraries assume exists as a writable location for their own cache files (pip, some Python libs default to `~/.cache` for things like font/plot caches) — a defensive default that avoids obscure "permission denied writing to /nonexistent" errors later, at the cost of a few extra KB.
- `--shell /usr/sbin/nologin` — this user can never get an interactive shell (e.g. via `docker exec -it ... bash` as this user, or if a vulnerability ever let something try to spawn a shell as it). Pure defense-in-depth; the container doesn't need this user to ever have shell access, so it's explicitly disabled.
- **Why a non-root user at all**: by default, a process in a container that never sets `USER` runs as `root` — and root inside a container that escapes its isolation (via a kernel exploit, misconfiguration, etc.) is root on the host's view of that container. Running as an unprivileged, fixed UID is standard container-hardening practice and is often an outright requirement in Kubernetes clusters with Pod Security Standards / OPA policies enforcing "no root containers."

```dockerfile
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
```
`COPY --from=builder` pulls **only** the `/opt/venv` directory out of the builder stage — the installed dependencies — and none of the compiler toolchain, apt cache, or intermediate files that produced them. This single line is what actually delivers the multi-stage build's size/security win.

```dockerfile
COPY --chown=appuser:appgroup alembic ./alembic
COPY --chown=appuser:appgroup alembic.ini .
COPY --chown=appuser:appgroup app ./app
```
Two things going on here:
- **`--chown=appuser:appgroup`** sets file ownership *during* the copy, in the same layer. Doing `COPY` then a separate `RUN chown -R ...` would create the files as root-owned in one layer and then rewrite ownership in a second layer — Docker's copy-on-write filesystem means the "original root-owned" bytes are still physically present in the image, just hidden, silently bloating the image. A single `--chown` copy avoids that entirely.
- **Ordering — `alembic` before `app`** — this is a second layer-caching decision, same principle as `requirements.txt` earlier but applied to source code: `alembic/` (schema migrations) changes far less often during day-to-day feature work than `app/` (route/business logic) does. Putting the less-frequently-changing directory first means routine `app/` edits only bust the cache for the `COPY app` layer and everything after it — the `alembic` copy layer stays cached across most rebuilds.

```dockerfile
USER 1001:1001
```
Switches the *active* user for every instruction from here on, and — critically — for the container process itself at `docker run` time. Everything before this line (installing `libpq5`, creating the user, copying files) still happens as root, because root privileges are needed to install packages and `chown` files; this line is placed as late as possible so the actual running application has the minimum possible privilege.

```dockerfile
EXPOSE 8000
```
Purely documentation/metadata — it doesn't actually publish the port (`-p 8000:8000` on `docker run` does that). It's there so `docker inspect`, orchestrators, and humans reading the file know which port the app listens on.

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
The **exec form** (a JSON array, not a bare string like `CMD uvicorn app.main:app ...`) matters: exec form runs `uvicorn` directly as PID 1, so it receives `SIGTERM` directly when Docker/Kubernetes stops the container, and can shut down its FastAPI `lifespan` cleanly. The shell form (`CMD uvicorn ...`) would instead run `/bin/sh -c "uvicorn ..."`, making `sh` PID 1 and `uvicorn` a child process — signals go to the shell, not uvicorn, which commonly causes containers to hang on shutdown until a hard `SIGKILL` timeout.

### Net effect

Two things drive the optimization here: **layer caching** (dependencies installed before code is copied, and less-frequently-changed code copied before more-frequently-changed code, so routine rebuilds are fast) and **image leanness/security** (multi-stage build discards the compiler toolchain and apt cache entirely, the final image only has the runtime-necessary `libpq5`, and the app runs as an unprivileged, fixed-UID user with no shell access).