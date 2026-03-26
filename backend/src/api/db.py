import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pymysql


@dataclass(frozen=True)
class DBConfig:
    """Database connection configuration loaded from environment variables."""

    host: str
    port: int
    user: str
    password: str
    db: str


def _parse_mysql_url(mysql_url: str) -> Tuple[str, int, str]:
    """
    Parse a mysql://host:port/db style URL.

    Returns: (host, port, db_name)
    """
    # Very small parser to avoid extra deps.
    # Expected: mysql://localhost:5000/myapp
    if not mysql_url.startswith("mysql://"):
        raise ValueError("MYSQL_URL must start with mysql://")
    rest = mysql_url[len("mysql://") :]
    if "/" not in rest:
        raise ValueError("MYSQL_URL must contain /<db>")
    host_port, db_name = rest.split("/", 1)
    if ":" in host_port:
        host, port_s = host_port.split(":", 1)
        port = int(port_s)
    else:
        host = host_port
        port = int(os.getenv("MYSQL_PORT", "3306"))
    return host, port, db_name


# PUBLIC_INTERFACE
def load_db_config() -> DBConfig:
    """Load DB configuration from env vars (MYSQL_URL, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB, MYSQL_PORT)."""
    mysql_url = os.getenv("MYSQL_URL", "").strip().strip('"').strip("'")
    user = os.getenv("MYSQL_USER", "").strip().strip('"').strip("'")
    password = os.getenv("MYSQL_PASSWORD", "").strip().strip('"').strip("'")
    db = os.getenv("MYSQL_DB", "").strip().strip('"').strip("'")
    port_env = os.getenv("MYSQL_PORT", "").strip().strip('"').strip("'")

    host = "localhost"
    port = 3306
    db_from_url = ""
    if mysql_url:
        host, port, db_from_url = _parse_mysql_url(mysql_url)

    if port_env:
        port = int(port_env)

    db_name = db or db_from_url
    if not (user and password and db_name):
        raise RuntimeError(
            "Database env vars missing. Required: MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB (or MYSQL_URL containing db)."
        )

    return DBConfig(host=host, port=port, user=user, password=password, db=db_name)


def _connect(cfg: DBConfig) -> pymysql.connections.Connection:
    """Create a PyMySQL connection with dict cursor."""
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.db,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _rows_to_list(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


# PUBLIC_INTERFACE
def fetch_all(query: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """Fetch all rows for a query (sync), returning a list of dicts."""
    cfg = load_db_config()
    with _connect(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            rows = cur.fetchall()
            return _rows_to_list(rows)


# PUBLIC_INTERFACE
def fetch_one(query: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """Fetch a single row for a query (sync), returning a dict or None."""
    cfg = load_db_config()
    with _connect(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            row = cur.fetchone()
            return dict(row) if row else None


# PUBLIC_INTERFACE
def execute(query: str, params: Optional[Sequence[Any]] = None) -> int:
    """Execute a statement (sync) and return affected row count."""
    cfg = load_db_config()
    with _connect(cfg) as conn:
        with conn.cursor() as cur:
            affected = cur.execute(query, params or ())
            return int(affected)


# PUBLIC_INTERFACE
def execute_many(query: str, params_seq: Sequence[Sequence[Any]]) -> int:
    """Execute many (sync) and return total affected rows."""
    cfg = load_db_config()
    with _connect(cfg) as conn:
        with conn.cursor() as cur:
            affected = cur.executemany(query, params_seq)
            return int(affected)
