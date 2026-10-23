"""Local DuckDB store. Every agent writes its own table; readers query it."""
import subprocess, sys
try:
    import duckdb
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "duckdb"])
    import duckdb
from .config import DB_PATH


def connect(read_only=False):
    DB_PATH.parent.mkdir(exist_ok=True)
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def _exists(con, table):
    return con.execute("select count(*) from information_schema.tables where table_name = ?", [table]).fetchone()[0] > 0


def write(table, df, mode="append", key_cols=None):
    """mode='replace' rewrites the table; 'append' adds rows (first deleting rows with the same key values).
    New columns are added to an existing table automatically."""
    con = connect()
    try:
        con.register("_df", df)
        if mode == "replace" or not _exists(con, table):
            con.execute(f"create or replace table {table} as select * from _df")
            return
        have = set(con.execute(f"select * from {table} limit 0").df().columns)
        for name, typ in [(r[0], r[1]) for r in con.execute("describe select * from _df").fetchall()]:
            if name not in have:
                con.execute(f'alter table {table} add column "{name}" {typ}')
        if key_cols:
            cond = " and ".join(f"t.{c} = d.{c}" for c in key_cols)
            con.execute(f"delete from {table} t where exists (select 1 from _df d where {cond})")
        con.execute(f"insert into {table} by name select * from _df")
    finally:
        con.close()


def delete(table, where, params=None):
    con = connect()
    try:
        if _exists(con, table):
            con.execute(f"delete from {table} where {where}", params or [])
    finally:
        con.close()


def read(sql, params=None):
    con = connect()
    try:
        return con.execute(sql, params or []).df()
    except Exception:
        return None
    finally:
        con.close()


def execute(sql, params=None):
    con = connect()
    try:
        con.execute(sql, params or [])
    finally:
        con.close()
