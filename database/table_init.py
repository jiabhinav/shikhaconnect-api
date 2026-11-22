"""Register application models and create missing tables in dependency order."""
from database.advisory_lock import acquire_transaction_lock
from importlib import import_module
from pkgutil import walk_packages
from pathlib import Path

from sqlalchemy import inspect, text, update
from sqlalchemy.exc import SQLAlchemyError

from database.database import Base
from database.fee_configuration_table import migrate_fee_configuration_month_rows
from database.generation_settings_table import ensure_late_fee_enabled_default, merge_generation_settings
from database.fee_structure_table import remove_fee_structure_carry_forward
from database.fee_assignment_table import (ensure_fee_assignment_section_column,
                                           rename_fee_assignment_structure_column,
                                           migrate_fee_assignment_configuration_reference,
                                           remove_fee_assignment_admission_amount,
                                           migrate_fee_assignment_class_configuration_uniqueness)
from database.student_carry_forward_table import migrate_student_carry_forwards
from database.student_table import migrate_optional_student_identity_fields, migrate_student_form_fields, migrate_student_dropdown_storage
from database.student_table import migrate_student_status_choices
from database.teacher_table import migrate_teacher_profile_image, migrate_optional_teacher_city, migrate_optional_teacher_identity, migrate_teacher_logins, migrate_teacher_employee_codes
from database.user_address_table import migrate_user_addresses
from database.staff_account_table import migrate_user_accounts, migrate_staff_profiles, migrate_account_status
from database.school_permissions_table import allow_legacy_service_name_null
from database.school_status_table import ensure_school_status
from database.session_table import remove_legacy_session_year_index
from database.staff_permission_table import rename_staff_module_id, add_staff_permission_actions
from database.school_mapping_table import migrate_school_mapping_accounts
from database.session_catalogs import migrate_session_catalogs
from database.student_table import migrate_student_session_admissions, migrate_shared_student_logins, migrate_student_admission_storage, migrate_student_roll_number, migrate_student_section, migrate_optional_father_fields, migrate_optional_student_pin_codes


def register_models():
    import models

    for module in walk_packages(models.__path__, prefix="models."):
        import_module(module.name)



def migrate_legacy_users(connection):
    """Split legacy accounts inside the caller's initialization transaction."""
    inspector = inspect(connection)
    if not inspector.has_table("users"):
        return
    if "login_user_id" in {col["name"] for col in inspector.get_columns("users")}:
        return
    if connection.dialect.name != "postgresql":
        raise SQLAlchemyError("Automatic legacy user migration requires PostgreSQL")

    # Serialize startup workers, then inspect again in case another migrated.
    acquire_transaction_lock(connection, 731904218)
    connection.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
    inspector = inspect(connection)
    if "login_user_id" in {col["name"] for col in inspector.get_columns("users")}:
        return
    if inspector.has_table("login_user"):
        raise SQLAlchemyError(
            "Legacy users and login_user both exist; reconcile the partial migration "
            "before restarting. No existing account data was overwritten."
        )

    migration = Path(__file__).resolve().parent.parent / "migrations" / "005_split_login_user.sql"
    # The SQL file can also run standalone; startup owns its transaction here.
    sql = "\n".join(
        line for line in migration.read_text().splitlines()
        if line.strip() not in {"BEGIN;", "COMMIT;"}
    )
    connection.exec_driver_sql(sql)


def remove_user_school_name(connection):
    """Schools are linked through school_mapping, not a name on users."""
    inspector = inspect(connection)
    if not inspector.has_table("users"):
        return
    if "school_name" not in {column["name"] for column in inspector.get_columns("users")}:
        return
    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904218)
        if "school_name" not in {column["name"] for column in inspect(connection).get_columns("users")}:
            return
    connection.execute(text("ALTER TABLE users DROP COLUMN school_name"))


def migrate_student_fee_adjustments(connection):
    from models.student_fee_adjustment import FEE_ADJUSTMENT_PAYMENT_STATUS_SQL

    table = "student_fee_adjustments"
    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904218)
    columns = {column["name"]: column for column in inspect(connection).get_columns(table)}
    legacy_flag = None
    if "application_status" in columns:
        legacy_flag = "application_status"
    elif "status" in columns and "computed" not in columns["status"]:
        legacy_flag = "status"

    if legacy_flag is not None:
        connection.exec_driver_sql(
            f"UPDATE {table} SET applied_at = CASE WHEN {legacy_flag} = 'Pending' THEN NULL "
            f"ELSE COALESCE(applied_at, CURRENT_TIMESTAMP) END WHERE type = 'Discount' "
            f"AND (applied_at IS NULL OR {legacy_flag} = 'Pending')"
        )
        connection.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {legacy_flag}")
        columns.pop(legacy_flag)
    elif "status" not in columns:
        connection.exec_driver_sql(
            f"UPDATE {table} SET applied_at = CURRENT_TIMESTAMP WHERE type = 'Discount' AND applied_at IS NULL"
        )

    current_status = columns.get("status")
    if current_status is not None and "applied_at" not in current_status["computed"]["sqltext"]:
        connection.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN status")
        columns.pop("status")

    storage = "STORED" if connection.dialect.name == "postgresql" else "VIRTUAL"
    for name, definition in (
        ("tag", "VARCHAR(50)"),
        ("fee_head_id", "INTEGER REFERENCES fee_heads(id) ON DELETE RESTRICT"),
        ("admission_number", "VARCHAR(150)"),
        ("paid_amount", "NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (paid_amount >= 0 AND paid_amount <= amount)"),
        ("status", f"VARCHAR(10) GENERATED ALWAYS AS ({FEE_ADJUSTMENT_PAYMENT_STATUS_SQL}) {storage} NOT NULL"),
    ):
        if name not in columns:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migrate_fee_head_adjustment_discounts(connection):
    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904218)
    columns = {column["name"] for column in inspect(connection).get_columns("student_fee_heads")}
    if "adjustment_discount_amount" in columns:
        return

    connection.exec_driver_sql(
        "ALTER TABLE student_fee_heads ADD COLUMN adjustment_discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0"
    )
    connection.exec_driver_sql("""
        WITH credits AS (
            SELECT head.id,
                   CASE WHEN SUM(adjustment.amount) > head.net_amount - head.paid_amount
                        THEN head.net_amount - head.paid_amount
                        ELSE SUM(adjustment.amount) END AS credit
            FROM student_fee_heads AS head
            JOIN student_fees AS fee ON fee.id = head.student_fee_id
            JOIN student_fee_adjustments AS adjustment
              ON adjustment.school_id = fee.school_id
             AND adjustment.session_id = fee.academic_session_id
             AND adjustment.student_id = fee.student_id
             AND adjustment.fee_head_id = head.fee_head_id
             AND substr(CAST(adjustment.date AS VARCHAR), 1, 7) = fee.fee_month
            WHERE adjustment.type = 'Discount' AND adjustment.applied_at IS NOT NULL
            GROUP BY head.id, head.net_amount, head.paid_amount
        )
        UPDATE student_fee_heads AS head
        SET adjustment_discount_amount = credits.credit,
            discount_amount = head.discount_amount + credits.credit,
            net_amount = head.net_amount - credits.credit
        FROM credits
        WHERE head.id = credits.id AND credits.credit > 0
    """)
    connection.exec_driver_sql("""
        WITH credits AS (
            SELECT student_fee_id, SUM(adjustment_discount_amount) AS credit
            FROM student_fee_heads
            WHERE adjustment_discount_amount > 0
            GROUP BY student_fee_id
        )
        UPDATE student_fees AS fee
        SET discount_amount = fee.discount_amount + credits.credit,
            net_amount = fee.net_amount - credits.credit,
            status = CASE WHEN fee.paid_amount >= fee.net_amount - credits.credit THEN 'PAID'
                          WHEN fee.paid_amount > 0 THEN 'PARTIAL'
                          ELSE 'UNPAID' END
        FROM credits
        WHERE fee.id = credits.student_fee_id
    """)


def migrate_student_fee_totals(connection):
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        acquire_transaction_lock(connection, 731904218)
    columns = {column["name"]: column for column in inspect(connection).get_columns("student_fees")}
    if "total_accepted_payment" in columns:
        connection.exec_driver_sql("ALTER TABLE student_fees DROP COLUMN total_accepted_payment")
    if postgres and columns["total_amount"]["type"].precision != 18:
        connection.exec_driver_sql("ALTER TABLE student_fees ALTER COLUMN total_amount TYPE NUMERIC(18, 2)")


def migrate_student_fee_paid_amount(connection):
    if connection.dialect.name != "postgresql":
        return
    acquire_transaction_lock(connection, 731904218)
    columns = {column["name"]: column for column in inspect(connection).get_columns("student_fees")}
    if columns["paid_amount"]["type"].precision == 18:
        return

    from models.student_fee_adjustment import StudentFee, fee_non_principal_paid_amount

    connection.exec_driver_sql("ALTER TABLE student_fees DROP CONSTRAINT IF EXISTS ck_student_fee_payment_range")
    connection.exec_driver_sql("ALTER TABLE student_fees ALTER COLUMN paid_amount TYPE NUMERIC(18, 2)")
    connection.exec_driver_sql(
        "ALTER TABLE student_fees ADD CONSTRAINT ck_student_fee_payment_range CHECK (net_amount >= 0)"
    )
    connection.execute(
        update(StudentFee.__table__).where(fee_non_principal_paid_amount > 0)
        .values(paid_amount=StudentFee.paid_amount + fee_non_principal_paid_amount)
    )


def add_student_fee_payment_columns(connection, existing):
    postgres = connection.dialect.name == "postgresql"
    timestamp = "TIMESTAMP WITH TIME ZONE" if postgres else "DATETIME"
    if_not_exists = "IF NOT EXISTS " if postgres else ""
    payment_fields = [("receipt_number", "VARCHAR(125)"), ("tag", "VARCHAR(50)"), ("payment_date", timestamp)]
    for table, fields in (
        ("student_fees", payment_fields),
        ("student_fee_cheque_payments", payment_fields),
        ("student_fee_razorpay_orders", [("receipt_number", "VARCHAR(125)")]),
        ("student_fee_receipts", [("payment_at", timestamp), ("payment_details", "JSON")]),
    ):
        if table not in existing:
            continue
        columns = {column["name"] for column in inspect(connection).get_columns(table)}
        for name, definition in fields:
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {if_not_exists}{name} {definition}")


def ensure_all_tables(connection):
    register_models()
    ensure_school_status(connection)
    migrate_user_accounts(connection)
    remove_user_school_name(connection)
    rename_staff_module_id(connection)
    add_staff_permission_actions(connection)
    allow_legacy_service_name_null(connection)
    remove_legacy_session_year_index(connection)
    existing = set(inspect(connection).get_table_names())
    if "student_fee_adjustments" in existing:
        migrate_student_fee_adjustments(connection)
    if {"student_fees", "student_fee_heads", "student_fee_adjustments"} <= existing:
        migrate_fee_head_adjustment_discounts(connection)
    if "student_carry_forwards" in existing:
        migrate_student_carry_forwards(connection)
    if "student_fees" in existing:
        add_student_fee_late_fee_amount(connection, backfill="student_fee_late_payments" in existing)
        migrate_student_fee_totals(connection)
    if {"student_fees", "student_fee_adjustments", "student_carry_forwards"} <= existing:
        migrate_student_fee_paid_amount(connection)
    if "student_fee_late_payments" in existing and connection.dialect.name == "postgresql":
        old_constraint = "uq_student_fee_late_payment_session"
        table = "student_fee_late_payments"
        if any(item["name"] == old_constraint for item in inspect(connection).get_unique_constraints(table)):
            acquire_transaction_lock(connection, 731904218)
            connection.exec_driver_sql(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {old_constraint}")
            connection.exec_driver_sql(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_student_fee_late_payment_month "
                f"ON {table} (school_id, session_id, student_id, fee_month)"
            )
    for table in ("student_fee_cheque_payments", "student_fee_razorpay_orders"):
        if table in existing and "late_fee_payment_amount" not in {
            column["name"] for column in inspect(connection).get_columns(table)
        }:
            if_not_exists = "IF NOT EXISTS " if connection.dialect.name == "postgresql" else ""
            connection.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {if_not_exists}late_fee_payment_amount NUMERIC(16, 2) NOT NULL DEFAULT 0"
            )
    add_student_fee_payment_columns(connection, existing)
    merge_generation_settings(connection)
    ensure_late_fee_enabled_default(connection)
    if set(Base.metadata.tables).issubset(existing):
        _drop_legacy_session_month_date(connection)
        _normalize_session_month_format(connection)
        ensure_fee_assignment_section_column(connection)
        rename_fee_assignment_structure_column(connection)
        migrate_fee_configuration_month_rows(connection)
        migrate_fee_assignment_configuration_reference(connection)
        remove_fee_structure_carry_forward(connection)
        remove_fee_assignment_admission_amount(connection)
        migrate_fee_assignment_class_configuration_uniqueness(connection)
        migrate_optional_teacher_city(connection)
        migrate_optional_teacher_identity(connection)
        migrate_staff_profiles(connection)
        migrate_user_addresses(connection)
        migrate_account_status(connection)
        migrate_school_mapping_accounts(connection)
        migrate_session_catalogs(connection)
        migrate_student_admission_storage(connection)
        migrate_student_status_choices(connection)
        migrate_student_session_admissions(connection)
        migrate_shared_student_logins(connection)
        migrate_student_section(connection)
        migrate_student_form_fields(connection)
        migrate_student_dropdown_storage(connection)
        migrate_student_roll_number(connection)
        migrate_optional_father_fields(connection)
        migrate_optional_student_pin_codes(connection)
        migrate_optional_student_identity_fields(connection)
        migrate_teacher_logins(connection)
        migrate_teacher_employee_codes(connection)
        migrate_teacher_profile_image(connection)
        return
    if connection.dialect.name == "postgresql":
        # Recheck within create_all after serializing concurrent initialization.
        acquire_transaction_lock(connection, 731904218)
    Base.metadata.create_all(bind=connection, checkfirst=True)
    migrate_student_fee_paid_amount(connection)
    _drop_legacy_session_month_date(connection)
    _normalize_session_month_format(connection)
    ensure_fee_assignment_section_column(connection)
    rename_fee_assignment_structure_column(connection)
    migrate_fee_configuration_month_rows(connection)
    migrate_fee_assignment_configuration_reference(connection)
    remove_fee_structure_carry_forward(connection)
    remove_fee_assignment_admission_amount(connection)
    migrate_fee_assignment_class_configuration_uniqueness(connection)
    migrate_optional_teacher_city(connection)
    migrate_optional_teacher_identity(connection)
    migrate_staff_profiles(connection)
    migrate_user_addresses(connection)
    migrate_account_status(connection)
    migrate_school_mapping_accounts(connection)
    migrate_session_catalogs(connection)
    migrate_student_admission_storage(connection)
    migrate_student_status_choices(connection)
    migrate_student_session_admissions(connection)
    migrate_shared_student_logins(connection)
    migrate_student_section(connection)
    migrate_optional_father_fields(connection)
    migrate_optional_student_pin_codes(connection)
    migrate_optional_student_identity_fields(connection)
    migrate_student_roll_number(connection)
    migrate_student_form_fields(connection)
    migrate_student_dropdown_storage(connection)
    migrate_teacher_logins(connection)
    migrate_teacher_employee_codes(connection)
    migrate_teacher_profile_image(connection)


def add_student_fee_late_fee_amount(connection, *, backfill):
    if "late_fee_amount" in {column["name"] for column in inspect(connection).get_columns("student_fees")}:
        return
    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904218)
        if "late_fee_amount" in {column["name"] for column in inspect(connection).get_columns("student_fees")}:
            return
    connection.exec_driver_sql(
        "ALTER TABLE student_fees ADD COLUMN late_fee_amount NUMERIC(16, 2) "
        "NOT NULL DEFAULT 0 CHECK (late_fee_amount >= 0)"
    )
    if backfill:
        connection.exec_driver_sql("""
            UPDATE student_fees
            SET late_fee_amount = COALESCE((
                SELECT late.paid_amount FROM student_fee_late_payments AS late
                WHERE late.school_id = student_fees.school_id
                  AND late.session_id = student_fees.academic_session_id
                  AND late.student_id = student_fees.student_id
                  AND late.fee_month = student_fees.fee_month
            ), 0)
        """)
        connection.exec_driver_sql("""
            INSERT INTO student_fees (
                school_id, academic_session_id, student_id, fee_month,
                total_amount, discount_amount, fine_amount, net_amount, paid_amount, status, late_fee_amount
            )
            SELECT late.school_id, late.session_id, late.student_id, late.fee_month,
                   0, 0, 0, 0, 0, 'PAID', late.paid_amount
            FROM student_fee_late_payments AS late
            WHERE late.paid_amount > 0 AND NOT EXISTS (
                SELECT 1 FROM student_fees AS fee
                WHERE fee.school_id = late.school_id
                  AND fee.academic_session_id = late.session_id
                  AND fee.student_id = late.student_id
                  AND fee.fee_month = late.fee_month
            )
        """)


def _drop_legacy_session_month_date(connection):
    """Keep the session-month table's period value in its month column only."""
    inspector = inspect(connection)
    if not inspector.has_table("session_months"):
        return
    columns = {column["name"] for column in inspector.get_columns("session_months")}
    if "month_date" in columns:
        connection.execute(text("ALTER TABLE session_months DROP COLUMN month_date"))


def _normalize_session_month_format(connection):
    """Convert legacy MM-YYYY labels to the canonical YYYY-MM format."""
    if not inspect(connection).has_table("session_months"):
        return
    rows = connection.execute(text("SELECT id, month FROM session_months")).all()
    for row_id, value in rows:
        if (len(value) == 7 and value[2] == "-" and value[:2].isdigit()
                and value[3:].isdigit()):
            normalized = f"{value[3:]}-{value[:2]}"
            connection.execute(text(
                "UPDATE session_months SET month = :month WHERE id = :id"
            ), {"month": normalized, "id": row_id})
