import psycopg
from pydantic import SecretStr


def connect(url: SecretStr, **kwargs) -> psycopg.Connection:
    """Open a psycopg 3 connection. The secret is unwrapped only here.

    psycopg errors are replaced, not chained: a malformed URL's message, or a failed
    connection's attached pgconn, can contain the password. Raising outside the
    except block leaves no __context__ link back to the original exception.
    """
    try:
        return psycopg.connect(url.get_secret_value(), connect_timeout=5, **kwargs)
    except psycopg.ProgrammingError:
        error = ValueError("Invalid PostgreSQL connection URL")
    except psycopg.OperationalError as exc:
        error = psycopg.OperationalError(str(exc))
    raise error
