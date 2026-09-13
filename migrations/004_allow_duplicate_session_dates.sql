-- Allow repeated session names, dates, and year pairs within each school.
-- School primary keys, session foreign keys, and date ordering remain enforced.
BEGIN;
DROP INDEX IF EXISTS uq_session_school_years;
COMMIT;
