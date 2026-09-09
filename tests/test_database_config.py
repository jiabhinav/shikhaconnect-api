import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Engine construction is lazy; these tests never connect to a database.
with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://test:test@example.test/test'}):
    from database.database import Settings, _resolve_env_file, build_database_url


class DatabaseConfigTests(unittest.TestCase):
    def config(self, **values):
        with patch.dict(os.environ, {}, clear=True):
            return Settings(_env_file=None, **values)

    def test_hosted_url_without_local_fields(self):
        for scheme in ('postgres', 'postgresql', 'postgresql+psycopg2'):
            with self.subTest(scheme=scheme):
                url = build_database_url(self.config(
                    DATABASE_URL=f'{scheme}://user:p%40ss%2Fword@db.example.test/app?sslmode=require'))
                self.assertEqual(url.drivername, 'postgresql+psycopg2')
                self.assertEqual(url.password, 'p@ss/word')
                self.assertEqual(url.query['sslmode'], 'require')

    def test_url_overrides_localhost(self):
        url = build_database_url(self.config(
            DATABASE_URL='postgresql://user:password@db.example.test/app',
            DATABASE_HOST='127.0.0.1', DATABASE_NAME='local', DATABASE_USER='local'))
        self.assertEqual(url.host, 'db.example.test')
        self.assertEqual(url.database, 'app')

    def test_local_fields(self):
        url = build_database_url(self.config(
            DATABASE_HOST='127.0.0.1', DATABASE_NAME='local',
            DATABASE_USER='local', DATABASE_PASSWORD='p@ss/#word'))
        self.assertEqual(url.host, '127.0.0.1')
        self.assertEqual(url.port, 5432)
        self.assertEqual(url.password, 'p@ss/#word')

    def test_missing_configuration(self):
        with self.assertRaisesRegex(ValueError, 'Set DATABASE_URL'):
            build_database_url(self.config())

    def test_dev_branch_uses_dev_env_file(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch('database.database.subprocess.run', return_value=SimpleNamespace(stdout='dev\n')):
                with patch('database.database.os.path.exists', side_effect=lambda path: path == '.env.dev'):
                    self.assertEqual(_resolve_env_file(), '.env.dev')

    def test_reject_non_postgresql_url(self):
        with self.assertRaisesRegex(ValueError, 'PostgreSQL'):
            build_database_url(self.config(DATABASE_URL='mysql://user:password@host/db'))


if __name__ == '__main__':
    unittest.main()
