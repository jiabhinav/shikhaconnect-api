# ShikshaConnect API

FastAPI application backed by an existing PostgreSQL database.

## PostgreSQL configuration

Create a PostgreSQL database and a user with schema and data permissions, then set
`DATABASE_HOST`, `DATABASE_PORT` (default `5432`), `DATABASE_NAME`,
`DATABASE_USER`, and `DATABASE_PASSWORD` in `.env` using `.env.example`.
The app uses SQLAlchemy with `psycopg2`; passwords may contain URL special
characters without manual encoding. Existing `.env` credentials must be
updated to your PostgreSQL server before starting the app.

For local development:

```sh
pip install -r requirements.txt
uvicorn main:app --reload
curl http://127.0.0.1:8000/health
```

Startup creates tables in the configured PostgreSQL database.
Missing columns are added automatically, but changes to existing columns,
constraints, indexes, and enum labels require explicit migrations.

## Connect a local API to VPS PostgreSQL

If PostgreSQL is reachable at `127.0.0.1:5432` on your VPS, start an SSH
tunnel from your Mac and keep the terminal open:

```sh
sh scripts/connect_vps_db.sh USER@VPS_IP
# For a custom SSH port:
sh scripts/connect_vps_db.sh USER@VPS_IP 2222
```

Test the connection before starting the API:

```sh
psql -h 127.0.0.1 -p 5433 -U YOUR_VPS_DB_USER -d YOUR_VPS_DB_NAME -W
```

For an API running directly on your Mac, use `DATABASE_HOST=127.0.0.1`
and `DATABASE_PORT=5433` with your VPS database name, user, and password.
Remove `DATABASE_URL` from both `.env` and your shell environment if using
these separate fields, because it takes precedence. Restart the API after
changing settings. The API's existing startup logic may add missing tables
and columns to this VPS database.

These instructions assume PostgreSQL listens on the VPS loopback interface.
If PostgreSQL runs in Docker, its port must be reachable from the VPS host.
An API running inside Docker also requires a host reachable from that container;
its `127.0.0.1` does not refer to your Mac's tunnel.

## Deploy on Render

A Render web service does not contain your local PostgreSQL server.
`127.0.0.1:5432` points at the web service itself and will fail unless a
PostgreSQL server is actually running there.

1. Create or select a Render PostgreSQL database in the same region as the API.
2. Copy its **Internal Database URL** from the database's **Connect** menu.
3. In the web service's **Environment** settings, set `DATABASE_URL` to that
   URL, without surrounding quotes. This overrides the local `DATABASE_*`
   fields; they are not required when `DATABASE_URL` is set.
4. For the native Python runtime, use build command
   `pip install -r requirements.txt` and start command
   `uvicorn main:app --host 0.0.0.0 --port $PORT`.
5. Deploy the updated code and check `/health` for `"database_connected": true`.

The application supports `postgres://`, `postgresql://`, and
`postgresql+psycopg2://` URLs, preserving query options such as `sslmode`.
Keep credentials in Render environment settings, not committed source files.
Existing local data is not copied to Render automatically.

See [Render PostgreSQL connection instructions](https://render.com/docs/postgresql-creating-connecting).

## Deploy to a VPS with Docker Compose

These instructions assume a Linux VPS with SSH access and an existing PostgreSQL
server reachable from the VPS. No database container or database migration is
included. Install Docker Engine and the Compose plugin using the
[official instructions for your Linux distribution](https://docs.docker.com/engine/install/).

1. Clone or copy this project to the VPS and enter its directory.
2. Create the configuration (skip the copy if `.env` already exists):

   ```sh
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```

   Set all five database values. Single-quote passwords containing `$` or `#`.
   Use the remote database hostname/IP; `localhost` inside the container refers
   to the API container. Allow the VPS IP in your database provider's remote
   access settings. Keep `.env` on the server and out of Git.

3. Build and start:

   ```sh
   docker compose up -d --build
   docker compose ps
   docker compose logs --tail=100 api
   curl http://127.0.0.1:8000/health
   ```

   A successful health response contains `"database_connected": true`.
   Startup creates missing tables and adds missing columns using the existing
   application logic, so back up the database before deploying. The database
   user needs the corresponding schema permissions. One Uvicorn process runs
   per container to avoid concurrent startup schema changes.

4. Set up HTTPS: point your API domain's DNS A record to the VPS IP (and an
   AAAA record only if IPv6 works). Install Caddy on the VPS using its
   [official installation instructions](https://caddyserver.com/docs/install).
   Replace `api.example.com` in `deploy/Caddyfile` with your domain. On a
   dedicated VPS using Caddy's standard systemd installation:

   ```sh
   sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
   sudo caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl enable --now caddy
   sudo systemctl reload caddy
   ```

   If Caddy already serves other sites, add this site's block to its existing
   configuration instead of replacing that file. Allow inbound TCP ports 80
   and 443 in the VPS/provider firewall and retain SSH access. Caddy obtains
   and renews certificates automatically. The API port binds only to VPS
   loopback; access the API through `https://YOUR_DOMAIN` and its interactive
   documentation through `https://YOUR_DOMAIN/docs`.

## Operations

After copying updated code to the VPS:

```sh
docker compose up -d --build
docker compose logs --tail=100 api
```

Stop with `docker compose down`. The remote database is unaffected. Containers
restart after process failures and Docker restarts; a failed health check marks
the container unhealthy but does not itself trigger a restart.

## Existing security issues to address before public use

Database credentials were previously hardcoded in source; rotate that password
because older Git history may retain it. Adding `.gitignore` does not untrack
an already tracked `.env` file.

The current authentication code stores and compares plaintext passwords and
returns passwords in user payloads. Fix password hashing and remove passwords
from responses before handling real users on a public deployment.

Container setup follows the [Docker Python guide](https://docs.docker.com/guides/python/).
