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
Invalid dates or blank names return 422. The start-year/end-year pair must be unique within each school when creating
or updating a session, including through school APIs. Duplicates return 409 even
when the month or day differs. Different schools may use the same years.
Existing duplicate rows are preserved; API validation prevents new conflicts.

Startup creates the `sessions` table with a school foreign key and date check.
Startup and school/session requests automatically remove the legacy
`uq_session_school_years` index from existing databases. The database role needs
permission to drop this index. Alternatively, apply
`migrations/004_allow_duplicate_session_dates.sql` manually before deployment.
The date-order check remains enforced.
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
| Create (POST) / List (GET) | `/schools/school/{school_id}/sessions/{session_id}/classes` | `/schools/school/{school_id}/sessions/{session_id}/sections` |
| Update (PUT) / Delete (DELETE) | `/schools/school/{school_id}/sessions/{session_id}/classes/{class_id}` | `/schools/school/{school_id}/sessions/{session_id}/sections/{section_id}` |

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

- `POST /schools/school/{school_id}/sessions/{session_id}/subjects`: create (201).
- `GET /schools/school/{school_id}/sessions/{session_id}/subjects`: list all; optionally filter with
  `?status=Active` or `?status=Inactive`.
- `PUT /schools/school/{school_id}/sessions/{session_id}/subjects/{subject_id}`: update name/code
  and optionally status. Omitted status keeps the current value; omitted code clears it.
- `PATCH /schools/school/{school_id}/sessions/{session_id}/subjects/{subject_id}/status`: change status
  using `{"status":"Inactive"}` or `{"status":"Active"}`.
- `DELETE /schools/school/{school_id}/sessions/{session_id}/subjects/{subject_id}`: permanently delete.
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
permissions, sessions, classes, sections, and subjects. PostgreSQL
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

- `POST /schools/school/{school_id}/sessions/{session_id}/streams`: create (201).
- `GET /schools/school/{school_id}/sessions/{session_id}/streams`: list by ID (200).
- `PUT /schools/school/{school_id}/sessions/{session_id}/streams/{stream_id}`: update name (200).
- `DELETE /schools/school/{school_id}/sessions/{session_id}/streams/{stream_id}`: delete (200).

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

- `POST /schools/school/{school_id}/sessions/{session_id}/fee_categories`: create (201).
- `GET /schools/school/{school_id}/sessions/{session_id}/fee_categories`: list by ID (200).
- `GET /schools/school/{school_id}/sessions/{session_id}/fee_categories/{fee_category_id}`: get one (200).
- `PUT /schools/school/{school_id}/sessions/{session_id}/fee_categories/{fee_category_id}`: update (200).
- `DELETE /schools/school/{school_id}/sessions/{session_id}/fee_categories/{fee_category_id}`: delete (200).

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

- `POST /schools/school/{school_id}/sessions/{session_id}/caste_categories`: create (201).
- `GET /schools/school/{school_id}/sessions/{session_id}/caste_categories`: list by ID (200).
- `GET /schools/school/{school_id}/sessions/{session_id}/caste_categories/{caste_category_id}`: get one (200).
- `PUT /schools/school/{school_id}/sessions/{session_id}/caste_categories/{caste_category_id}`: update (200).
- `DELETE /schools/school/{school_id}/sessions/{session_id}/caste_categories/{caste_category_id}`: delete (200).

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

- `POST /schools/school/{school_id}/sessions/{session_id}/houses`: create (201).
- `GET /schools/school/{school_id}/sessions/{session_id}/houses`: list by ID (200).
- `GET /schools/school/{school_id}/sessions/{session_id}/houses/{house_id}`: get one (200).
- `PUT /schools/school/{school_id}/sessions/{session_id}/houses/{house_id}`: update (200).
- `DELETE /schools/school/{school_id}/sessions/{session_id}/houses/{house_id}`: delete (200).

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

School-scoped APIs currently require Super Admin access. School permissions
are linked by `school_id`. User assignments are stored in `school_mapping`.
The legacy `school_user_assignments` table is separate from this new table;
legacy records are not automatically migrated.

### Super Admin school user assignments

All endpoints require a Super Admin bearer token and appear under **Super Admin**
in the API docs. The application creates `school_mapping` automatically during
database initialization, including on existing databases where the table is missing.
Each row has `id`, `school_id`, `user_id`, and `status`. A user may belong to
multiple schools; each school/user pair is unique. Deleting a school or user
cascades to its mappings.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/super-admin/school-mappings` | Create assignment (201) |
| PUT | `/super-admin/school-mappings/{mapping_id}` | Replace school, user, and status |
| PATCH | `/super-admin/school-mappings/{mapping_id}/status` | Activate/deactivate assignment |
| DELETE | `/super-admin/school-mappings/{mapping_id}` | Delete assignment |
| GET | `/super-admin/school/{school_id}/users` | List assigned users |

The mapping API accepts and returns `login_user.id` in the `user_id` field.
Mappings reference `login_user` directly; a linked `users` profile is not required.

POST and PUT require all three fields:

```json
{"school_id": 1, "user_id": 2, "status": "active"}
```

PATCH accepts `{"status": "deactive"}` or `{"status": "active"}`.
Status strings are case insensitive; `inactive` is also accepted as `deactive`.
Assignment status applies only to this school mapping and does not change the
user's global account status or grant access to existing Super Admin APIs.

GET returns all assigned users regardless of assignment or account status;
the `status` query parameter is ignored. Each item contains mapping `id`, `school_id`, `user_id`, mapping
`status`, user name fields, `email`, `mobile`, `role`, and global `user_status`.
Passwords are excluded. Responses use the `status`, `message`, `data` envelope.
A school with no assignments returns an empty list. Missing schools, users, or
mappings return 404, duplicate assignments return 409, invalid input returns
422, and non-Super-Admin access returns 403.

School list and detail GET responses return `sessions` as a single session object
(or `null` when no session overlaps the current calendar year). When several
sessions overlap the year, a session active today is preferred, then the latest
start date and highest ID. The dedicated sessions list endpoint still returns
an array.

School updates require a positive top-level `session_id`. The URL's `school_id`
and this session ID must match the same row in `sessions`; otherwise the API
returns 404. Missing or null session IDs return 422. Updates never create a
replacement session or match by name/date. Session name/date changes are stored
in `sessions`, leaving the legacy school session columns unchanged.

### School staff

The application automatically creates `staff`, `staff_address`, and
`staff_permission` when missing, using the existing startup and database-request
initialization. Existing tables and rows are preserved. Staff belongs to a school;
each staff member has one address and a list of module permissions.

These endpoints require Super Admin authentication, matching the existing student
routes:

- `POST /schools/school/{school_id}/staff` — save all three form sections atomically.
- `GET /schools/school/{school_id}/staff?offset=0&limit=50` — list staff for the school.
- `GET /schools/school/{school_id}/staff/{staff_id}` — fetch all three sections.

Example create body:

```json
{
  "staff_info": {
    "first_name": "Anita",
    "date_of_birth": "1990-01-15",
    "designation": "Teacher",
    "mobile_number": "9876543210",
    "email": "anita@example.com",
    "father_name": "Father",
    "mother_name": "Mother",
    "nationality": "Indian",
    "aadhaar_number": "123456789012",
    "caste_category_id": 1,
    "role": "Teacher",
    "gender": "Female"
  },
  "address": {
    "line_1": "12 Main Road",
    "city": "Delhi",
    "country": "India",
    "state": "Delhi",
    "pin_code": "110001"
  },
  "permissions": [
    {"staff_module_id": 1, "is_enabled": true},
    {"staff_module_id": 2, "is_enabled": false}
  ]
}
```

`caste_category_id` must belong to the URL's school. Permission `staff_module_id` values
come from `GET /schools/staff-modules` and must reference active modules. Duplicate
module IDs are rejected; an empty permissions list grants no modules. The existing
`staff_modules` catalog is not created or seeded by these routes.

Optional personal fields are `middle_name`, `last_name`, `spouse_name`, `religion`,
`qualification`, `joining_date`, `biometric_code`, `experience`, `mode_of_transport`,
`salary`, `blood_group`, and `feedback`. Address `line_2` is optional. Dates use
`YYYY-MM-DD`; salary is a nonnegative decimal with at most two fractional digits.
Responses contain `status`, `message`, and `data`, including staff/school IDs,
`staff_info`, `address`, and `permissions`.

Staff creation writes all records in one transaction:

- `login_user`: first_name, middle_name, last_name, email, mobile, hashed password, role, and account status.
- `staff`: school_id, login_user_id, and all remaining staff_info fields, including dates, designation, family details, qualification, salary, and feedback.
- `staff_address`: address fields linked by login_user_id.
- `staff_permission`: module permissions linked by login_user_id.

The request field remains `mobile_number`; it maps to `login_user.mobile`.
Optional `staff_info.password` defaults to the mobile number on creation and is
always hashed. Omitting it during update preserves the current password.
Email and mobile must be unique across login accounts. Staff login uses staff.school_id directly; staff creation does not insert into
users or school_mapping. Passwords are excluded from responses.

### Existing database migration

Initialization upgrades PostgreSQL databases in one transaction. Account fields
stored in users are moved to login_user without changing user IDs or password
hashes. Existing login_user links and account values are preserved. Conflicting copies
in users are retained in nullable legacy_account_* columns for review. Duplicate
identities for unlinked accounts still abort the migration without discarding records.

Staff-specific profile fields remain in staff; earlier profile copies in users
are copied back when staff columns are missing. New staff API writes put those
fields only in staff. Addresses and permissions remain in their respective tables,
linked through login_user_id. Staff links directly to login_user. Legacy staff.user_id is removed after validating and migrating its account link.
Existing users rows are retained; staff creation does not write to users.

User address fields are stored in staff_address. Legacy address columns and
staff_addrers rows are migrated automatically. General user API responses keep
flat address fields; staff responses keep their three sections. Address and
permission responses expose login_user_id as their owner.

Account status is stored only in `login_user.status`. User and staff API status
fields read and update this shared value. Initialization moves legacy statuses
from users/staff and removes their status columns. If linked legacy statuses
differ, Inactive takes precedence over Pending, which takes precedence over Active.

### User account IDs

User registration, login, user detail, user list, and user update responses expose
`login_user.id` as `data.id` (or each list item's `id`). GET, PUT, DELETE
`/users/{user_id}` and PATCH `/users/{login_user_id}/status` accept that login
user ID. School mapping payloads and their `user_id` responses use the same ID.
User profile endpoints require a linked `users` profile. Staff resources continue
to expose their profile `id` and separate `login_user_id`.

School mapping PUT, PATCH, and DELETE URLs use the assignment's `mapping_id`,
not an account ID, because one account can have assignments to several schools.
Database initialization automatically migrates existing PostgreSQL school mappings
from `users.id` to `users.login_user_id` and changes the foreign key to `login_user.id`.

### Session IDs for school catalogs and settings

Pass `session_id` in the URL for all catalog operations:
`/schools/school/{school_id}/sessions/{session_id}/{resource}`. Resources are
`caste_categories`, `classes`, `sections`, `fee_categories`, `houses`, `streams`,
and `subjects`. Existing item IDs remain the final URL segment for item operations.
The request body keeps the existing fields (for example `{"name": "General"}`);
responses now include `session_id`. The old catalog URLs are replaced by these
session URLs. A session from another school returns 404. Names (and subject codes)
are unique within a school session, so they can be reused in another session.
Class ordering starts independently in each session. Student class, fee category,
and caste category references must belong to the student's selected session.

Timetable, fee generation, and transport generation settings already use the
same URL structure, with resources `timetable-settings`, `fee-generation-settings`,
and `transport-generation-settings`. Their tables and responses already store
`session_id`; settings request bodies do not need to repeat it.

At database initialization, existing catalog rows keep their IDs and are assigned
to their school's current session. If current sessions overlap, the latest start
date wins, followed by the highest session ID. If there is no current session,
the row is preserved with a null session ID and excluded from session lists until
assigned through a database update. Backfilling runs once when the column is added.
Deleting a session that still owns catalog records is restricted.

School responses (including login schools and users' assigned schools) expose
`current_session_id`. Pass that value as `{session_id}` in the catalog/settings
URLs. It is `null` when no session covers today's date. Overlapping current
sessions use the latest start date, then the highest session ID.
