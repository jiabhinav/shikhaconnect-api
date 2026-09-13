-- User-to-school assignments are no longer used by the application.
-- This permanently removes existing assignment records.
BEGIN;
DROP TABLE IF EXISTS school_user_assignments;
COMMIT;
