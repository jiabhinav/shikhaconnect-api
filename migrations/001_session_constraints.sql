-- For an existing sessions table. New tables receive these constraints at startup.
-- Duplicate year pairs or invalid dates must be corrected before this migration.
BEGIN;
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_school_years
ON sessions (school_id, EXTRACT(YEAR FROM start_date), EXTRACT(YEAR FROM end_date));
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_session_dates' AND conrelid = 'sessions'::regclass
    ) THEN
        ALTER TABLE sessions ADD CONSTRAINT ck_session_dates CHECK (end_date >= start_date);
    END IF;
END $$;
COMMIT;
