"""Upgrade existing layouts to login_user accounts and staff-specific profiles.

Run inside the initialization transaction, preserving linked users and staff IDs.
"""
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from utils.passwords import hash_password

ACCOUNT_COLUMNS = {
    'first_name': 'VARCHAR(255)', 'middle_name': 'VARCHAR(255)',
    'last_name': 'VARCHAR(255)', 'email': 'VARCHAR(255)', 'mobile': 'VARCHAR(20)',
    'password': 'VARCHAR(255)', 'role': 'VARCHAR(100)',
}
PROFILE_COLUMNS = {
    'date_of_birth': 'DATE', 'designation': 'VARCHAR(255)',
    'spouse_name': 'VARCHAR(255)', 'father_name': 'VARCHAR(255)',
    'mother_name': 'VARCHAR(255)', 'nationality': 'VARCHAR(100)',
    'aadhaar_number': 'VARCHAR(20)', 'religion': 'VARCHAR(100)',
    'caste_category_id': 'INTEGER REFERENCES caste_categories(id)',
    'qualification': 'VARCHAR(255)', 'joining_date': 'DATE',
    'biometric_code': 'VARCHAR(100)', 'experience': 'VARCHAR(255)',
    'mode_of_transport': 'VARCHAR(100)', 'salary': 'NUMERIC(12,2)',
    'blood_group': 'VARCHAR(10)', 'gender': 'VARCHAR(50)', 'feedback': 'TEXT',
}


def columns(connection, table):
    return {col['name']: col for col in inspect(connection).get_columns(table)}


def lock(connection):
    if connection.dialect.name != 'postgresql':
        raise SQLAlchemyError('Legacy staff storage migration requires PostgreSQL')
    connection.execute(text('SELECT pg_advisory_xact_lock(731904218)'))


def ensure_unique(connection, table, fields):
    inspector = inspect(connection)
    keys = inspector.get_unique_constraints(table) + inspector.get_indexes(table)
    if not any(key['column_names'] == fields and key.get('unique', True) for key in keys):
        connection.execute(text(f"ALTER TABLE {table} ADD UNIQUE ({', '.join(fields)})"))


def migrate_user_accounts(connection):
    """Move account columns into login_user, preserving profile IDs and hashes."""
    if not inspect(connection).has_table('users'):
        return
    existing = columns(connection, 'users')
    account_columns = columns(connection, 'login_user') if inspect(connection).has_table('login_user') else {}
    if (not set(ACCOUNT_COLUMNS).intersection(existing)
            and existing.get('login_user_id', {}).get('nullable') is False
            and (not account_columns or (account_columns.get('last_name', {}).get('nullable') is True
                 and str(account_columns.get('role', {}).get('type')) == 'VARCHAR(100)'))):
        return
    lock(connection)
    connection.execute(text('LOCK TABLE users IN ACCESS EXCLUSIVE MODE'))
    from models.user import LoginUser
    LoginUser.__table__.create(connection, checkfirst=True)
    connection.execute(text('LOCK TABLE login_user IN ACCESS EXCLUSIVE MODE'))
    connection.execute(text('ALTER TABLE login_user ALTER COLUMN role TYPE VARCHAR(100) USING role::text'))
    connection.execute(text('ALTER TABLE login_user ALTER COLUMN last_name DROP NOT NULL'))
    existing = columns(connection, 'users')
    if 'login_user_id' not in existing:
        connection.execute(text('ALTER TABLE users ADD COLUMN login_user_id INTEGER'))
    rows = connection.execute(text('SELECT * FROM users WHERE login_user_id IS NULL ORDER BY id')).mappings().all()
    for row in rows:
        account = {field: row.get(field) for field in ACCOUNT_COLUMNS}
        if any(account[field] is None for field in ('first_name', 'email', 'mobile', 'password', 'role')):
            raise SQLAlchemyError(f"Cannot migrate user {row['id']}: missing account identity")
        account_id = connection.execute(text("""
            INSERT INTO login_user (first_name, middle_name, last_name, email, mobile, password, role)
            VALUES (:first_name, :middle_name, :last_name, :email, :mobile, :password, :role)
            RETURNING id
        """), account).scalar_one()
        connection.execute(text('UPDATE users SET login_user_id=:account WHERE id=:id'),
                           {'account': account_id, 'id': row['id']})
    if connection.execute(text("""
        SELECT 1 FROM users u LEFT JOIN login_user l ON l.id=u.login_user_id
        WHERE l.id IS NULL LIMIT 1
    """)).first():
        raise SQLAlchemyError('Cannot migrate users: missing linked login account')
    conflicting_columns = set()
    for name in sorted(set(ACCOUNT_COLUMNS).intersection(existing)):
        left, right = f'u.{name}::text', f'l.{name}::text'
        if name == 'role':
            def normalized(expression):
                return (f"CASE {expression} WHEN 'ADMIN' THEN 'Admin' WHEN 'SUPER_ADMIN' THEN 'Super Admin' "
                        f"WHEN 'SUB_ADMIN' THEN 'Sub Admin' ELSE {expression} END")
            left, right = normalized(left), normalized(right)
        if connection.execute(text(f"""
            SELECT 1 FROM users u JOIN login_user l ON l.id=u.login_user_id
            WHERE u.{name} IS NOT NULL AND ({left}) IS DISTINCT FROM ({right}) LIMIT 1
        """)).first():
            conflicting_columns.add(name)
    for name in sorted(set(ACCOUNT_COLUMNS).intersection(existing)):
        if name in conflicting_columns:
            # Linked login_user values are authoritative. Preserve the old copy
            # for review rather than overwriting credentials or blocking startup.
            archived = f'legacy_account_{name}'
            if archived in existing:
                raise SQLAlchemyError(f'Cannot preserve users.{name}: {archived} already exists')
            connection.execute(text(f'ALTER TABLE users RENAME COLUMN {name} TO {archived}'))
            connection.execute(text(f'ALTER TABLE users ALTER COLUMN {archived} DROP NOT NULL'))
            connection.execute(text(f'ALTER TABLE users ALTER COLUMN {archived} DROP DEFAULT'))
        else:
            connection.execute(text(f'ALTER TABLE users DROP COLUMN {name}'))
    connection.execute(text('ALTER TABLE users ALTER COLUMN login_user_id SET NOT NULL'))
    ensure_unique(connection, 'users', ['login_user_id'])
    if not any(fk['constrained_columns'] == ['login_user_id'] and fk['referred_table'] == 'login_user'
               for fk in inspect(connection).get_foreign_keys('users')):
        connection.execute(text('ALTER TABLE users ADD FOREIGN KEY (login_user_id) REFERENCES login_user(id)'))
    connection.execute(text("""
        UPDATE login_user SET role=CASE role
        WHEN 'ADMIN' THEN 'Admin' WHEN 'SUPER_ADMIN' THEN 'Super Admin'
        WHEN 'SUB_ADMIN' THEN 'Sub Admin' ELSE role END
    """))


def migrate_staff_profiles(connection):
    """Link staff directly to login_user without creating user profiles."""
    existing = columns(connection, 'staff')
    identities = {('mobile_number' if f == 'mobile' else f) for f in ACCOUNT_COLUMNS}
    if (set(PROFILE_COLUMNS).issubset(existing) and 'login_user_id' in existing
            and 'user_id' not in existing and not identities.intersection(existing)
            and existing['login_user_id']['nullable'] is False):
        return
    lock(connection)
    connection.execute(text('LOCK TABLE staff, login_user IN ACCESS EXCLUSIVE MODE'))
    existing = columns(connection, 'staff')
    # Only old deployments with staff.user_id need the former profile table.
    user_columns = {}
    if 'user_id' in existing:
        connection.execute(text('LOCK TABLE users IN ACCESS EXCLUSIVE MODE'))
        user_columns = columns(connection, 'users')
    if 'login_user_id' not in existing:
        connection.execute(text('ALTER TABLE staff ADD COLUMN login_user_id INTEGER'))
    if 'user_id' in existing:
        if connection.execute(text("""
            SELECT 1 FROM staff s LEFT JOIN users u ON u.id=s.user_id
            WHERE s.user_id IS NOT NULL AND (u.id IS NULL OR
                (s.login_user_id IS NOT NULL AND s.login_user_id <> u.login_user_id)) LIMIT 1
        """)).first():
            raise SQLAlchemyError('Cannot migrate staff: conflicting or missing legacy user link')
        connection.execute(text("""
            UPDATE staff s SET login_user_id=u.login_user_id FROM users u
            WHERE u.id=s.user_id AND s.login_user_id IS NULL
        """))
    for name, kind in PROFILE_COLUMNS.items():
        if name not in existing:
            connection.execute(text(f'ALTER TABLE staff ADD COLUMN {name} {kind}'))
            if name in user_columns and 'user_id' in existing:
                cast = '::date' if name in ('date_of_birth', 'joining_date') else ''
                connection.execute(text(f"""
                    UPDATE staff s SET {name}=u.{name}{cast} FROM users u WHERE u.id=s.user_id
                """))
    rows = connection.execute(text('SELECT * FROM staff WHERE login_user_id IS NULL ORDER BY id')).mappings().all()
    for row in rows:
        account = {f: row.get('mobile_number' if f == 'mobile' else f)
                   for f in ACCOUNT_COLUMNS if f != 'password'}
        if any(account[f] is None for f in ('first_name', 'email', 'mobile', 'role')):
            raise SQLAlchemyError(f"Cannot migrate staff {row['id']}: missing account identity")
        account['password'] = row.get('password') or hash_password(account['mobile'])
        account_id = connection.execute(text("""
            INSERT INTO login_user (first_name, middle_name, last_name, email, mobile, password, role)
            VALUES (:first_name, :middle_name, :last_name, :email, :mobile, :password, :role)
            RETURNING id
        """), account).scalar_one()
        connection.execute(text('UPDATE staff SET login_user_id=:account WHERE id=:id'),
                           {'account': account_id, 'id': row['id']})
    if connection.execute(text("""
        SELECT 1 FROM staff s LEFT JOIN login_user l ON l.id=s.login_user_id WHERE l.id IS NULL LIMIT 1
    """)).first():
        raise SQLAlchemyError('Cannot migrate staff: missing linked login account')
    for name in sorted(identities.intersection(existing)):
        target = 'mobile' if name == 'mobile_number' else name
        left, right = f's.{name}::text', f'l.{target}::text'
        if name == 'role':
            def normalize(expr):
                return (f"CASE {expr} WHEN 'ADMIN' THEN 'Admin' WHEN 'SUPER_ADMIN' THEN 'Super Admin' "
                        f"WHEN 'SUB_ADMIN' THEN 'Sub Admin' ELSE {expr} END")
            left, right = normalize(left), normalize(right)
        if connection.execute(text(f"""
            SELECT 1 FROM staff s JOIN login_user l ON l.id=s.login_user_id
            WHERE s.{name} IS NOT NULL AND ({left}) IS DISTINCT FROM ({right}) LIMIT 1
        """)).first():
            raise SQLAlchemyError(f'Cannot remove staff.{name}: linked account values differ')
    for name in sorted(identities.intersection(existing)):
        connection.execute(text(f'ALTER TABLE staff DROP COLUMN {name}'))
    connection.execute(text('ALTER TABLE staff ALTER COLUMN login_user_id SET NOT NULL'))
    ensure_unique(connection, 'staff', ['login_user_id'])
    if not any(fk['constrained_columns'] == ['login_user_id'] and fk['referred_table'] == 'login_user'
               for fk in inspect(connection).get_foreign_keys('staff')):
        connection.execute(text('ALTER TABLE staff ADD FOREIGN KEY (login_user_id) REFERENCES login_user(id)'))
    if 'user_id' in existing:
        connection.execute(text('ALTER TABLE staff DROP COLUMN user_id'))


IDENTITY_FIELDS = tuple(field for field in ACCOUNT_COLUMNS if field != "password")
STAFF_PROFILE_FIELDS = tuple(PROFILE_COLUMNS)


def remove_migrated_staff_columns(connection, staff_columns):
    """Remove duplicated login fields only; never remove staff profile fields."""
    mappings = {
        ("mobile_number" if field == "mobile" else field): ("l", field)
        for field in IDENTITY_FIELDS
    }
    legacy = set(mappings).intersection(staff_columns)
    if not legacy:
        return
    missing_link = connection.execute(text("""
        SELECT 1 FROM staff s
        LEFT JOIN login_user l ON l.id=s.login_user_id
        WHERE l.id IS NULL LIMIT 1
    """)).first()
    if missing_link:
        raise SQLAlchemyError("Cannot remove legacy staff columns: some staff accounts are not linked")
    for column in sorted(legacy):
        alias, target = mappings[column]
        left, right = f"s.{column}::text", f"{alias}.{target}::text"
        if column == "role":
            def normalized(expression):
                return (
                    f"(CASE {expression} WHEN 'SUPER_ADMIN' THEN 'Super Admin' "
                    "WHEN 'SuperAdmin' THEN 'Super Admin' WHEN 'ADMIN' THEN 'Admin' "
                    "WHEN 'SUB_ADMIN' THEN 'Sub Admin' WHEN 'SubAdmin' THEN 'Sub Admin' "
                    f"ELSE {expression} END)"
                )
            left, right = normalized(left), normalized(right)
        conflict = connection.execute(text(f"""
            SELECT 1 FROM staff s JOIN login_user l ON l.id=s.login_user_id
            WHERE s.{column} IS NOT NULL AND {left} IS DISTINCT FROM {right} LIMIT 1
        """)).first()
        if conflict:
            raise SQLAlchemyError(
                f"Cannot remove staff.{column}: legacy and linked values differ; reconcile before retrying"
            )
    for column in sorted(legacy):
        connection.execute(text(f"ALTER TABLE staff DROP COLUMN {column}"))


def migrate_staff_accounts(connection):
    """Compatibility entry point for the direct staff-to-login_user migration."""
    migrate_user_accounts(connection)
    migrate_staff_profiles(connection)


def migrate_account_status(connection):
    """Consolidate profile status on login_user; retain the most restrictive state."""
    inspector = inspect(connection)
    account_columns = columns(connection, 'login_user')
    sources = [table for table in ('users', 'staff')
               if inspector.has_table(table) and 'status' in columns(connection, table)]
    if 'status' in account_columns and not sources:
        return
    lock(connection)
    connection.execute(text('LOCK TABLE login_user IN ACCESS EXCLUSIVE MODE'))
    for table in sources:
        connection.execute(text(f'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE'))
    # Refresh after concurrent startup workers finish.
    sources = [table for table in ('users', 'staff')
               if inspect(connection).has_table(table) and 'status' in columns(connection, table)]
    if 'status' not in columns(connection, 'login_user'):
        connection.execute(text('ALTER TABLE login_user ADD COLUMN status VARCHAR(8)'))
    parts = ['SELECT id AS account_id, UPPER(status::text) AS status FROM login_user']
    for table in sources:
        parts.append(f'SELECT login_user_id AS account_id, UPPER(status::text) AS status FROM {table}')
    states = ' UNION ALL '.join(parts)
    if connection.execute(text(f"""
        SELECT 1 FROM ({states}) states WHERE status IS NOT NULL
        AND status NOT IN ('ACTIVE', 'INACTIVE', 'PENDING') LIMIT 1
    """)).first():
        raise SQLAlchemyError('Cannot migrate account status: unsupported legacy status')
    connection.execute(text(f"""
        UPDATE login_user l SET status=CASE states.severity
            WHEN 2 THEN 'INACTIVE' WHEN 1 THEN 'PENDING' ELSE 'ACTIVE' END
        FROM (SELECT account_id, MAX(CASE status WHEN 'INACTIVE' THEN 2
                    WHEN 'PENDING' THEN 1 ELSE 0 END) AS severity
              FROM ({states}) all_states GROUP BY account_id) states
        WHERE states.account_id=l.id
    """))
    connection.execute(text("ALTER TABLE login_user ALTER COLUMN status SET DEFAULT 'ACTIVE'"))
    connection.execute(text('ALTER TABLE login_user ALTER COLUMN status SET NOT NULL'))
    for table in sources:
        connection.execute(text(f'ALTER TABLE {table} DROP COLUMN status'))
