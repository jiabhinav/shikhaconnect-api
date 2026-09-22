-- Run once against an existing PostgreSQL database before deploying this change.
-- Duplicate mobile numbers must be resolved first; the transaction fails safely
-- if any are present. Existing password hashes and user IDs are preserved.
BEGIN;
LOCK TABLE users IN ACCESS EXCLUSIVE MODE;

CREATE TABLE login_user (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(255) NOT NULL,
    middle_name VARCHAR(255),
    last_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    mobile VARCHAR(20) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    role user_role NOT NULL
);

INSERT INTO login_user (id, first_name, middle_name, last_name, email, mobile, password, role)
SELECT id, first_name, middle_name, last_name, email, mobile, password,
       (CASE role::text
           WHEN 'Super Admin' THEN 'SUPER_ADMIN'
           WHEN 'SuperAdmin' THEN 'SUPER_ADMIN'
           WHEN 'Admin' THEN 'ADMIN'
           WHEN 'Sub Admin' THEN 'SUB_ADMIN'
           WHEN 'SubAdmin' THEN 'SUB_ADMIN'
           ELSE role::text
        END)::user_role
FROM users;
SELECT setval(pg_get_serial_sequence('login_user', 'id'),
              COALESCE((SELECT MAX(id) FROM login_user), 1),
              EXISTS (SELECT 1 FROM login_user));

ALTER TABLE users ADD COLUMN login_user_id INTEGER;
UPDATE users SET login_user_id = id;
ALTER TABLE users ALTER COLUMN login_user_id SET NOT NULL;
ALTER TABLE users ADD CONSTRAINT users_login_user_id_key UNIQUE (login_user_id);
ALTER TABLE users ADD CONSTRAINT users_login_user_id_fkey
    FOREIGN KEY (login_user_id) REFERENCES login_user(id);

ALTER TABLE users
    DROP COLUMN first_name,
    DROP COLUMN middle_name,
    DROP COLUMN last_name,
    DROP COLUMN email,
    DROP COLUMN mobile,
    DROP COLUMN password,
    DROP COLUMN role;
COMMIT;
