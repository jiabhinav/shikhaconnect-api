# ShikshaConnect API

FastAPI application backed by an existing PostgreSQL database.

## School sessions

Authenticated endpoints are grouped under **Schools** in `/docs`:

- `POST /schools/school/{school_id}/sessions`: create a session (201).
- `PUT /schools/school/{school_id}/sessions/{session_id}`: replace its name and dates (200).
- `GET /schools/school/{school_id}/sessions`: list sessions, newest start date first (200).

Create and update accept:

```json
{
  "name": "2026-2027",
  "start_date": "2026-04-01",
  "end_date": "2027-03-31"
}
```

Responses use `{ "status": "success", "message": "...", "data": ... }`.
Each session includes `id`, `school_id`, `name`, dates, and a date-derived
`status` of `Past`, `Current`, or `Upcoming` (using the server's date).
Super Admins can access all schools; assigned Admins and Sub Admins can
access their schools. Missing or inaccessible schools/sessions return 404.
Invalid dates or blank names return 422. The same start-year/end-year pair
within a school returns 409, even if the days or months differ; different
schools may use the same years. Update excludes the session being edited.

Startup creates the `sessions` table with a school foreign key, date check,
and unique year-pair index. If the table already exists, apply
`migrations/001_session_constraints.sql` to PostgreSQL before using these
endpoints; automatic column synchronization does not add these constraints.
Existing session fields on the school record remain separate from this list.

Run the API and constraint tests against an isolated in-memory database:

```sh
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

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

## Classes and sections

Both are independent school-level lists under **Schools** in `/docs`.
Tables `classes` and `sections` are created automatically at startup and
checked again on requests. Each has an auto-incrementing `id` and a
`school_id` foreign key to `schools.id` with cascading school deletion.

| Action | Classes | Sections |
| --- | --- | --- |
| Create (POST) / List (GET) | `/schools/school/{school_id}/classes` | `/schools/school/{school_id}/sections` |
| Update (PUT) / Delete (DELETE) | `/schools/school/{school_id}/classes/{class_id}` | `/schools/school/{school_id}/sections/{section_id}` |

Class body: `{"name":"NURSERY","class_order":2}`. `class_order` is optional:
create appends after the highest order; update keeps the existing order.
Lists sort by class order then ID; equal orders are permitted.
Section body: `{"name":"A"}`. Section lists sort by ID.
Names are trimmed, required, limited to 100 characters, and unique per school
ignoring case. Duplicate names return 409, invalid payloads 422, and missing
or inaccessible records 404. Super Admins and the school's assigned Admins
and Sub Admins can manage these lists. Create returns 201; other operations
return 200, using the existing `status`, `message`, and `data` response format.

## Subjects

The school-scoped `subjects` table is created automatically at startup and on
subject requests if missing, with an auto-increment ID and a foreign key to
`schools.id`. APIs appear under **Schools**. Assigned Admins and Sub Admins
can manage their school's subjects; Super Admins can manage all schools.
Inactive user accounts remain blocked by the existing authentication dependency.

- `POST /schools/school/{school_id}/subjects`: create (201).
- `GET /schools/school/{school_id}/subjects`: list all; optionally filter with
  `?status=Active` or `?status=Inactive`.
- `PUT /schools/school/{school_id}/subjects/{subject_id}`: update name/code
  and optionally status. Omitted status keeps the current value; omitted code clears it.
- `PATCH /schools/school/{school_id}/subjects/{subject_id}/status`: change status
  using `{"status":"Inactive"}` or `{"status":"Active"}`.
- `DELETE /schools/school/{school_id}/subjects/{subject_id}`: permanently delete.
  Use the status endpoint to deactivate while keeping the record.

Create/update body:

```json
{"name":"ENGLISH","code":"ENG","status":"Active"}
```

Name is required (maximum 100 characters); code is optional (maximum 50).
Blank codes become null. Names and nonempty codes are unique per school,
ignoring case, including inactive subjects. Duplicates return 409; invalid
payloads return 422; missing or inaccessible schools/subjects return 404.
New subjects default to Active. Responses follow the existing
`status`, `message`, `data` envelope and include the subject's own status in data.

## Automatic table creation

All modules under `models` are discovered automatically. At startup and before
requests using the database dependency (including authentication), missing model
tables are created in foreign-key dependency order. This includes users, schools,
assignments, permissions, sessions, classes, sections, and subjects. PostgreSQL
initialization uses a transaction advisory lock to coordinate concurrent workers.
Existing tables and rows are preserved. The database must already exist and its
configured user must have schema creation permissions. If initialization cannot
connect or create tables, database requests return 503 and retry on the next request.
Creating missing tables does not migrate constraints on existing tables; continue
to apply any required migrations for those changes.

## Streams

School streams are listed under **Schools** in `/docs`. The `streams` table
contains an auto-incrementing `id`, a `school_id` foreign key to `schools.id`
(with cascading school deletion), and a required `name`. The centralized
initializer automatically creates it if missing at startup or before database
requests. The screenshot's Sr.No is the frontend row number, not a stored column.

- `POST /schools/school/{school_id}/streams`: create (201).
- `GET /schools/school/{school_id}/streams`: list by ID (200).
- `PUT /schools/school/{school_id}/streams/{stream_id}`: update name (200).
- `DELETE /schools/school/{school_id}/streams/{stream_id}`: delete (200).

Create/update body: `{"name":"COMMERCE"}`.
Names are trimmed, required, limited to 100 characters, and unique per school
ignoring case. Duplicate names return 409; invalid input returns 422; missing
or inaccessible schools/streams return 404. Assigned Admins and Sub Admins
can manage their school's streams; Super Admins can manage all schools.
Responses follow the `status`, `message`, `data` envelope, with `id`, `school_id`,
and `name` in each stream record. No default streams are inserted.

## Fee categories

Fee categories appear under **Schools** in `/docs`. The `fee_categories` table
has an auto-incrementing `id`, `school_id` foreign key to `schools.id` (with
cascading school deletion), and required `name`. Automatic initialization creates
it if missing. Sr.No in the screenshot is a frontend row number.

- `POST /schools/school/{school_id}/fee_categories`: create (201).
- `GET /schools/school/{school_id}/fee_categories`: list by ID (200).
- `GET /schools/school/{school_id}/fee_categories/{fee_category_id}`: get one (200).
- `PUT /schools/school/{school_id}/fee_categories/{fee_category_id}`: update (200).
- `DELETE /schools/school/{school_id}/fee_categories/{fee_category_id}`: delete (200).

Create/update body: `{"name":"GENERAL"}`. Names are trimmed, required, limited
to 100 characters, and unique per school ignoring case. Duplicates return 409,
invalid input 422, and missing/inaccessible records 404. Assigned Admins and
Sub Admins can manage their school's categories; Super Admins can manage all.
Responses use `status`, `message`, and `data`; each category contains `id`,
`school_id`, and `name`. No sample categories are inserted automatically.

## Caste categories

Caste categories appear under **Schools** in `/docs`. The `caste_categories`
table has an auto-incrementing `id`, a `school_id` foreign key to `schools.id`
(with cascading school deletion), and required `name`. The centralized
initializer creates it automatically if missing. Sr.No is a frontend row number.

- `POST /schools/school/{school_id}/caste_categories`: create (201).
- `GET /schools/school/{school_id}/caste_categories`: list by ID (200).
- `GET /schools/school/{school_id}/caste_categories/{caste_category_id}`: get one (200).
- `PUT /schools/school/{school_id}/caste_categories/{caste_category_id}`: update (200).
- `DELETE /schools/school/{school_id}/caste_categories/{caste_category_id}`: delete (200).

Create/update body: `{"name":"GENERAL"}`. Names are trimmed, required, limited
to 100 characters, and unique per school ignoring case. Duplicates return 409,
invalid input 422, and missing/inaccessible records 404. Assigned Admins and
Sub Admins manage their school's categories; Super Admins can manage all.
Responses use `status`, `message`, and `data`; each category includes `id`,
`school_id`, and `name`. No sample categories are inserted automatically.

## Houses

House APIs appear under **Schools** in `/docs`. The `houses` table contains
an auto-incrementing `id`, `school_id` foreign key to `schools.id` (with cascading
school deletion), and required `name`. It is automatically created if missing
by the centralized initializer. Sr.No is a frontend row number.

- `POST /schools/school/{school_id}/houses`: create (201).
- `GET /schools/school/{school_id}/houses`: list by ID (200).
- `GET /schools/school/{school_id}/houses/{house_id}`: get one (200).
- `PUT /schools/school/{school_id}/houses/{house_id}`: update (200).
- `DELETE /schools/school/{school_id}/houses/{house_id}`: delete (200).

Create/update body: `{"name":"Sapphire"}`. Names are trimmed, required, limited
to 100 characters, and unique per school ignoring case. Duplicates return 409,
invalid input 422, and missing/inaccessible records 404. Assigned Admins and
Sub Admins can manage their school's houses; Super Admins can manage all.
Responses use `status`, `message`, and `data`; each house includes `id`,
`school_id`, and `name`. No sample houses are inserted automatically.

## Fee and transport generation settings

Two auto-created tables, `fee_generation_settings` and
`transport_generation_settings`, each store one record per school session.
Each has an auto-increment ID and unique `session_id` foreign key to `sessions.id`
with cascading deletion. The school is derived from that session, avoiding
inconsistent school/session pairs. Assigned Admins/Sub Admins and Super Admins
can access settings under **Schools**.

Base path: `/schools/school/{school_id}/sessions/{session_id}`.

- `PUT /fee-generation-settings`: create or replace the fee panel (200).
- `GET /fee-generation-settings`: retrieve the saved fee panel.
- `PUT /transport-generation-settings`: create or replace the transport panel (200).
- `GET /transport-generation-settings`: retrieve the saved transport panel.

Fee payload:
```json
{"generation_day":1,"payment_due_days":10,"late_fee_enabled":false}
```
Transport payload:
```json
{"generation_day":1,"payment_due_day":15,"late_fee_enabled":false}
```

`generation_day` and transport `payment_due_day` are days of month (1–31).
Fee `payment_due_days` is a nonnegative number of days after generation.
These meanings follow the screenshot labels. The late-fee flag defaults to false.
Each save affects only its own panel; repeated saves update the existing record.
Missing settings or inaccessible sessions return 404, invalid input 422.
Responses use `status`, `message`, and `data`, including ID and session ID.
These endpoints only store configuration; they do not generate invoices, run
scheduled jobs, calculate late charges, or determine short-month billing dates.

## Timetable and attendance settings

The auto-created `timetable_settings` table stores one record per session with
an auto-increment ID and unique `session_id` foreign key to `sessions.id`
(cascading deletion). School ownership comes from the session. Assigned Admins
and Sub Admins, plus Super Admins, can access these **Schools** endpoints:

- `PUT /schools/school/{school_id}/sessions/{session_id}/timetable-settings`:
  create or replace all settings (200).
- `GET /schools/school/{school_id}/sessions/{session_id}/timetable-settings`:
  retrieve settings (200), or 404 if not saved or not accessible.

```json
{
  "summer_start_time": "08:00:00",
  "summer_end_time": "14:00:00",
  "winter_start_time": "09:00:00",
  "winter_end_time": "15:00:00",
  "minimum_attendance_percentage": 75,
  "term_attendance_enabled": true
}
```

All six fields are required. Times are local school clock times without timezone
offsets; each end must be after its corresponding start on the same day.
Attendance ranges from 0 to 100 with at most two decimal places; its response
value is serialized as a decimal string. Invalid input returns 422. Repeated
saves update the same record. The response envelope contains `status`, `message`,
and `data`, including settings ID and session ID. This stores configuration;
it does not generate class schedules or calculate attendance.

`POST /schools/school/{school_id}/sessions/{session_id}/timetable-settings`
creates timetable settings using the same six-field body as PUT and returns 201.
If settings already exist for that session, POST returns 409 without changing
any values; use PUT to update. The same school access and input validation apply.

## Students

The `students` table auto-creates through the centralized initializer. It has an
auto-increment ID, school foreign key, and foreign keys for session, class, fee
category, and caste category. References must belong to the URL's school.
Referenced records cannot be deleted while used by students (RESTRICT).
Assigned Admins/Sub Admins and Super Admins can use these Schools endpoints:

- `POST /schools/school/{school_id}/students`: create (201).
- `GET /schools/school/{school_id}/students`: list, with `offset=0&limit=50`
  (maximum limit 200).
- `GET /schools/school/{school_id}/students/{student_id}`: load all fields for editing.
- `PUT /schools/school/{school_id}/students/{student_id}`: replace the complete form.

Send a JSON object with three required sections: `student_info`, `parent_info`, and `address`. Only the screenshot's starred
fields are required. Minimum example (replace reference IDs with your school's):

```json
{
  "student_info": {
    "first_name": "Test",
    "last_name": "Student",
    "mobile_number": "9000000000",
    "date_of_birth": "2015-01-01",
    "gender": "Female",
    "email": "student@example.com",
    "nationality": "Indian",
    "caste_category_id": 1,
    "fee_category_id": 1,
    "session_id": 1,
    "class_id": 1
  },
  "parent_info": {
    "father_name": "Test Father",
    "father_contact_no": "9000000001",
    "father_aadhaar_no": "123456789012",
    "mother_name": "Test Mother"
  },
  "address": {
    "line_1": "Example Street",
    "city": "Example City",
    "country": "India",
    "state": "Example State",
    "pin_code": "123456"
  }
}
```

Optional fields: `middle_name`, `blood_group`, `religion`, `aadhaar_number`,
`permanent_education_no`, `father_secondary_number`, `father_qualification`,
`father_occupation`, `father_office_address`, `mother_contact_no`,
`mother_secondary_number`, `mother_qualification`, `mother_occupation`,
`mother_aadhaar_no`, `mother_office_address`, `guardian_name`,
`relation_with_student`, `guardian_primary_contact_no`,
`guardian_secondary_contact_no`, `guardian_email`, `guardian_address`,
`line_2`, and `address_type`. Optional blank strings become null. Phone,
Aadhaar, and postal values are strings. Emails are validated, but email/mobile
and parent identifiers are not unique so siblings may share contact details.
PUT requires all starred fields and clears omitted optional fields. Responses
include the same three sections plus top-level `id` and `school_id` in the existing response envelope. The database remains a single students table. Flat request bodies are no longer accepted.
Invalid input returns 422; missing/inaccessible students or reference IDs return
404; database reference conflicts return 409. No student sample data is inserted.
