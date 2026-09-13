-- Preserve historical service names while allowing new module_id permissions.
BEGIN;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'school_permissions' AND column_name = 'module_id'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'school_permissions' AND column_name = 'service_name'
          AND is_nullable = 'NO'
    ) THEN
        ALTER TABLE school_permissions ALTER COLUMN service_name DROP NOT NULL;
    END IF;
END $$;
COMMIT;
