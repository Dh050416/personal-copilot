# Fresh-server storage

The default Compose file expects an existing PostgreSQL instance. If the server has no database,
the optional storage override starts PostgreSQL 16 with pgvector and Redis on the private Compose network.
This creates an empty database. Existing local or remote records require a separate backup/restore;
this configuration does not import them automatically. Do not use it to replace an existing database.

From the repository root on the server, keep the existing `.env` with model settings, then run:

```bash
git pull
grep -q '^POSTGRES_PASSWORD=' .env || printf '\nPOSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)" >> .env
docker compose -f docker-compose.yml -f docker-compose.storage.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.storage.yml ps
```

Use a generated hexadecimal password: the override inserts it into connection URLs without URL encoding.
Never regenerate/change POSTGRES_PASSWORD for an initialized volume without explicitly changing the database
role password too. PostgreSQL's initialization variable only takes effect when its data directory is empty.

The override supplies DATABASE_URL, CHECKPOINT_URL and REDIS_URL to both backend and bill-worker,
overriding old localhost values in `.env`. PostgreSQL/Redis have no published host ports.
The backend waits for storage health, runs Alembic, then starts the API. Frontend and bill-worker wait for API health.

Include both `-f` arguments on subsequent Compose commands, including updates and logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.storage.yml logs --tail=100 backend
```

Named volumes retain database/cache files through container replacement. `down -v` deletes these volumes;
it is not a repair command. Production database backups must be maintained separately from volumes.
