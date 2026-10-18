# Native Stuff
from urllib.parse import urlparse

# Installed Stuff
import duckdb

# Other Project Files
import modules.Settings as CFG

"""
DuckDB access to chat archived out of Postgres by archive_chat.py.

Processed videos' chat lives in Parquet files on S3, at <channel bucket>/chat_archive/*.parquet,
listed in public.chat_archive (the manifest). Postgres's messages table only holds chat for
videos not yet processed/archived. connect() gives one DuckDB connection that sees both:

    import modules.Archive as A
    con = A.connect()                                   # every channel
    con = A.connect(channels=["Sakura Miko"])           # just these (lists fewer files)
    A.Messages(con, "video_id = 'abc123'").show()        # filter pushed to Postgres + Parquet
    con.sql("SELECT user_id, count(*) FROM archived_messages GROUP BY 1 ORDER BY 2 DESC LIMIT 10").show()
    df = con.sql("SELECT * FROM all_messages WHERE user_id = 'xyz'").df()  # needs pandas

Views:
    archived_messages -- the Parquet archive (same columns as messages)
    all_messages      -- archived_messages UNION ALL the live public.messages rows (streams every
                         live row -- use Messages(con, where) for selective lookups)
    pg.public.<table> -- the whole Postgres database, attached read-only (rollups, videos, ...)

The S3 store is a hard-drive NAS: threads defaults to 4 so parallel range requests don't make the
drives thrash between seeks; pass local_root= to read a local copy of the files instead (laid out
as <local_root>/<db_suffix>/chat_archive/*.parquet). Files are sorted by (video_id, timestamp), so
video filters skip most of each file; user/text filters scan the needed columns of every file.
"""

def _q(value) -> str:
    """SQL string literal."""
    return "'" + str(value).replace("'","''") + "'"

def S3_Secret_SQL() -> str:
    """CREATE SECRET statement for the bot's S3 store (CFG.ENDPOINT_*), path-style for self-hosted endpoints."""
    url = urlparse(CFG.ENDPOINT_URL)
    endpoint = url.netloc or url.path
    use_ssl = "true" if url.scheme == "https" else "false"
    return (f"CREATE OR REPLACE SECRET chonkers_s3 (TYPE s3, KEY_ID {_q(CFG.ENDPOINT_ID)}, SECRET {_q(CFG.ENDPOINT_KEY)}, "
            f"REGION {_q(CFG.ENDPOINT_REGION or 'us-east-1')}, ENDPOINT {_q(endpoint)}, URL_STYLE 'path', USE_SSL {use_ssl})")

def Postgres_DSN() -> str:
    """libpq connection string for the bot's database (CFG.DB_*), for DuckDB's postgres extension."""
    def q(value) -> str:
        return "'" + str(value).replace("\\","\\\\").replace("'","\\'") + "'"
    return f"host={q(CFG.DB_HOST)} port={q(CFG.DB_PORT)} dbname={q(CFG.DB_NAME)} user={q(CFG.DB_USR)} password={q(CFG.DB_PASS)}"

def Base_Connection(threads:int=4) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection with httpfs/postgres loaded, the S3 secret set and Postgres attached read-only as pg."""
    con = duckdb.connect()
    for ext in ("httpfs","postgres"):
        con.execute(f"INSTALL {ext}; LOAD {ext};")
    con.execute(f"SET threads = {int(threads)}")
    con.execute("SET enable_http_metadata_cache = true")
    con.execute(S3_Secret_SQL())
    con.execute(f"ATTACH {_q(Postgres_DSN())} AS pg (TYPE postgres, READ_ONLY)")
    return con

# Per-connection scope set by connect(): (Parquet file paths, channel ids or None for all).
# DuckDB connections can't carry Python attributes, so it's kept here, keyed by id(con).
_SCOPE:dict[int,tuple[list[str],list[str]|None]] = {}

def connect(channels:list[str]|None=None,local_root:str|None=None,threads:int=4) -> duckdb.DuckDBPyConnection:
    """
    Returns a DuckDB connection with the archived_messages / all_messages views (see module docstring).

    :param channels: channel_directory names to include. None = every channel.
    :param local_root: read Parquet from this local directory instead of S3.
    :param threads: DuckDB worker threads (parallel S3 requests). Keep low against the HDD NAS.
    """
    con = Base_Connection(threads)

    # The manifest, not an S3 listing, decides which files exist: a file is only in chat_archive
    # once its rows were verified and deleted from Postgres, so this can never double-count
    # against the live rows (or pick up an orphan from an interrupted archive run).
    names = None if channels is None else ", ".join(_q(n) for n in channels) or "NULL"
    channel_filter = "" if names is None else f"WHERE cd.name IN ({names})"
    rows = con.execute(f"""
        SELECT DISTINCT cd.db_suffix, ca.file_key
        FROM pg.public.chat_archive ca JOIN pg.public.channel_directory cd ON cd.user_id = ca.channel_id
        {channel_filter}
    """).fetchall()
    root = local_root.rstrip("/\\") if local_root else "s3:/"
    paths = [f"{root}/{db_suffix}/{file_key}" for db_suffix,file_key in rows]
    ids = None
    if channels is not None:
        ids = [r[0] for r in con.execute(f"SELECT user_id FROM pg.public.channel_directory cd {channel_filter}").fetchall()]
    _SCOPE[id(con)] = (paths,ids)

    con.execute(f"CREATE VIEW archived_messages AS {_archived_sql(con)}")
    con.execute(f"CREATE VIEW all_messages AS {_archived_sql(con)} UNION ALL BY NAME {_live_sql(con)}")
    return con

def _archived_sql(con:duckdb.DuckDBPyConnection,where:str|None=None) -> str:
    paths = _SCOPE[id(con)][0]
    if not paths:
        # Nothing archived yet -- an empty relation with the live table's columns keeps queries valid.
        return "SELECT * FROM postgres_query('pg', 'SELECT * FROM public.messages LIMIT 0')"
    file_list = "[" + ", ".join(_q(p) for p in paths) + "]"
    return f"SELECT * FROM read_parquet({file_list})" + (f" WHERE {where}" if where else "")

def _live_sql(con:duckdb.DuckDBPyConnection,where:str|None=None) -> str:
    """
    The live Postgres rows, read through postgres_query() so Postgres plans the WHERE (and uses
    its indexes). DuckDB's direct table scan (pg.public.messages) splits a table into page
    ranges and reads ALL of them even with a filter -- 45s for one video's 2,488 rows on
    messages_miko, measured -- so it's never used for messages.
    """
    ids = _SCOPE[id(con)][1]
    clauses = []
    if ids is not None:
        clauses.append("channel_id IN (" + (", ".join(_q(i) for i in ids) or "NULL") + ")")
    if where:
        clauses.append(f"({where})")
    sql = "SELECT * FROM public.messages" + (" WHERE " + " AND ".join(clauses) if clauses else "")
    return f"SELECT * FROM postgres_query('pg', {_q(sql)})"

def Messages(con:duckdb.DuckDBPyConnection,where:str|None=None) -> duckdb.DuckDBPyRelation:
    """
    Archived + live messages matching a SQL condition, with the condition sent to BOTH sides:
    Postgres plans it against its indexes, and DuckDB uses it to skip Parquet row groups. Prefer
    this over filtering all_messages for selective lookups -- all_messages has to stream every
    live row out of Postgres before DuckDB can filter them (cheap once most chat is archived,
    since then only unprocessed videos are live).

    The condition must be valid in both Postgres and DuckDB -- plain comparisons, IN, LIKE and
    boolean logic on messages' own columns are.

        A.Messages(con, "video_id = 'abc123'").df()
        A.Messages(con, "user_id = 'xyz' AND amount IS NOT NULL").count("*").show()
    """
    return con.sql(f"{_archived_sql(con,where)} UNION ALL BY NAME {_live_sql(con,where)}")
