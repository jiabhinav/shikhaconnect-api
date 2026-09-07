"""Run with python -m database.check_connection inside the API container."""
from urllib.parse import quote, quote_plus

from sqlalchemy import create_engine, text

from database.database import engine


def main():
    url = engine.url
    diagnostic_engine = create_engine(url, connect_args={"connect_timeout": 5})
    print(f"Driver: {url.drivername}; host: {url.host}; port: {url.port}; database: {url.database}")
    try:
        # Use a separate connection with a timeout for this diagnostic.
        with diagnostic_engine.connect() as conn:
            row = conn.execute(text('SELECT current_database(), current_user')).one()
            print(f"Connected: database={row[0]}, user={row[1]}")
        return 0
    except Exception as exc:
        message = str(getattr(exc, 'orig', exc))
        if url.password:
            for secret in sorted({url.password, quote(url.password, safe=''), quote_plus(url.password)}, key=len, reverse=True):
                message = message.replace(secret, '[REDACTED]')
        print(f"Connection failed: {message}")
        return 1
    finally:
        diagnostic_engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
