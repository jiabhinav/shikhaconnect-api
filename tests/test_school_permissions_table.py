import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from database.school_permissions_table import allow_legacy_service_name_null


class LegacyPermissionTests(unittest.TestCase):
    def test_repairs_required_legacy_column(self):
        connection = Mock(dialect=SimpleNamespace(name="postgresql"))
        with patch("database.school_permissions_table.inspect") as inspect:
            inspect.return_value.get_columns.return_value = [
                {"name": "module_id", "nullable": False},
                {"name": "service_name", "nullable": False},
            ]
            allow_legacy_service_name_null(connection)
            self.assertEqual(str(connection.execute.call_args.args[0]),
                             "ALTER TABLE school_permissions ALTER COLUMN service_name DROP NOT NULL")

    def test_current_and_already_repaired_schemas_need_no_changes(self):
        for columns in (
            [{"name": "module_id", "nullable": False}],
            [{"name": "module_id", "nullable": False}, {"name": "service_name", "nullable": True}],
            [{"name": "service_name", "nullable": False}],
        ):
            connection = Mock(dialect=SimpleNamespace(name="postgresql"))
            with patch("database.school_permissions_table.inspect") as inspect:
                inspect.return_value.get_columns.return_value = columns
                allow_legacy_service_name_null(connection)
                connection.execute.assert_not_called()
