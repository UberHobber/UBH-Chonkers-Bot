# Native Stuff
import argparse,csv,os,tempfile,time
from collections import Counter

# Installed Stuff
import numpy as np
from psycopg2.extras import execute_values

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Database as DB
import modules.Archive as A
import modules.AudienceDeltas as AD

"""
Rebuilds the pre-generated audience rollups (sql/audience_rollups.sql) from scratch, or checks
them for drift (--verify).

The heavy work runs HERE, in DuckDB on this machine: Postgres only streams user_channel_activity
(and user_first_message) out once, sequentially. Grouping, ranking and the channel-pair counting
for Community happen in this machine's RAM (spilling to its local disk if needed), never as sorts
or temp files on the database host -- whose worn QLC NVMe overheated under heavy query load.

Bucketing comes from modules/AudienceDeltas.py (bucket_case_sql), the same definitions the bot
uses when it maintains these tables incrementally, so a rebuild and the incremental path agree.

Usage:
    build_audience_baseline.py                   rebuild everything (bot must be stopped)
    build_audience_baseline.py --verify          recompute and report drift, write nothing
    build_audience_baseline.py --verify --channel Kobo
                                                 cheap per-channel check (streams only that channel)

A full run reads all of user_channel_activity (~9 GB) on the DB host once: keep its fan on and
check the drive temperature before and after. The rebuild writes the result tables, resets the
staging tables and fixes video_message_stats.first_messages/first_channel_messages, all in one
transaction.
"""

def _lit(s:str) -> str:
    return "'" + str(s).replace("'","''") + "'"

def load(con,channel_id:str|None) -> None:
    where = f" WHERE channel_id = {_lit(channel_id)}" if channel_id else ""
    q = ('SELECT channel_id, user_id, "timestamp" AS first_ts, last_message_ts AS last_ts, total_messages, '
         f'max_member_status, ever_moderator, first_video_id, active_weeks::text AS weeks '
         f'FROM public.user_channel_activity{where}')
    t = time.time()
    con.execute(f"CREATE OR REPLACE TABLE uca AS SELECT * FROM postgres_query('pg', {_lit(q)})")
    n = con.execute("SELECT count(*) FROM uca").fetchone()[0]
    LOG.logger.info(f"Streamed {n:,} user_channel_activity rows in {time.time() - t:.0f}s.")

def compute(con,full:bool,scopes:dict[str,list[str]]) -> dict[str,list[tuple]]:
    """Every rollup, recomputed from the uca table in DuckDB."""
    con.execute("SET TimeZone = 'UTC'")
    msgs = AD.bucket_case_sql("msgs","total_messages")
    stay = AD.bucket_case_sql("stay","(last_ts - first_ts)")
    out = {}
    t = time.time()
    out["channel_audience"] = con.execute(f"""
        SELECT channel_id, 'msgs', {msgs}, count(*) FROM uca GROUP BY 1, 3
        UNION ALL SELECT channel_id, 'badge', coalesce(CAST(max_member_status AS VARCHAR), 'none'), count(*) FROM uca GROUP BY 1, 3
        UNION ALL SELECT channel_id, 'stay', {stay}, count(*) FROM uca GROUP BY 1, 3
        UNION ALL SELECT channel_id, 'mod', 'yes', count(*) FROM uca WHERE ever_moderator GROUP BY 1""").fetchall()
    out["channel_audience_monthly"] = con.execute("""
        SELECT channel_id, m, sum(a), sum(l) FROM (
            SELECT channel_id, CAST(date_trunc('month', to_timestamp(first_ts)) AS DATE) m, 1 a, 0 l FROM uca
            UNION ALL SELECT channel_id, CAST(date_trunc('month', to_timestamp(last_ts)) AS DATE), 0, 1 FROM uca)
        GROUP BY 1, 2""").fetchall()
    out["channel_top_chatters"] = con.execute(f"""
        SELECT channel_id, user_id, total_messages FROM (
            SELECT *, row_number() OVER (PARTITION BY channel_id ORDER BY total_messages DESC, user_id) rn FROM uca)
        WHERE rn <= {DB.TOP_CHATTERS}""").fetchall()
    out["video_first_channel"] = con.execute(
        "SELECT first_video_id, count(*) FROM uca GROUP BY 1").fetchall()
    LOG.logger.info(f"Per-channel rollups computed in {time.time() - t:.0f}s.")
    regulars(con,out,full,scopes)

    if full:
        t = time.time()
        con.execute("CREATE OR REPLACE TABLE scopes (channel_id VARCHAR, scope VARCHAR)")
        con.executemany("INSERT INTO scopes VALUES (?, ?)",[(c,s) for c,ss in scopes.items() for s in ss])
        con.execute("""CREATE OR REPLACE TABLE user_scope AS
            SELECT u.user_id, s.scope, count(*) k, min(u.first_ts) mf FROM uca u JOIN scopes s USING (channel_id) GROUP BY 1, 2""")
        out["scope_audience"] = con.execute(
            "SELECT scope, 'k', CAST(k AS VARCHAR), count(*) FROM user_scope GROUP BY 1, 3").fetchall()
        arrivals = con.execute(
            "SELECT scope, CAST(date_trunc('month', to_timestamp(mf)) AS DATE), count(*) FROM user_scope GROUP BY 1, 2").fetchall()
        mix = out.pop("scope_mix")
        for s,m,n in arrivals:
            mix.setdefault((s,m),[0,0,0])[0] = n
        out["scope_audience_monthly"] = [(s,m,*v) for (s,m),v in mix.items()]
        con.execute("CREATE OR REPLACE TABLE uc AS SELECT user_id, channel_id FROM uca")
        out["channel_overlap"] = con.execute("""
            SELECT a.channel_id, b.channel_id, count(*) FROM uc a JOIN uc b ON a.user_id = b.user_id AND a.channel_id < b.channel_id
            GROUP BY 1, 2""").fetchall()
        out["regular_overlap"] = con.execute("""
            SELECT a.channel_id, b.channel_id, a.yr, count(*) FROM reg a JOIN reg b
              ON a.user_id = b.user_id AND a.yr = b.yr AND a.channel_id < b.channel_id GROUP BY 1, 2, 3
            UNION ALL
            SELECT a.channel_id, b.channel_id, 0, count(DISTINCT a.user_id) FROM reg a JOIN reg b
              ON a.user_id = b.user_id AND a.yr = b.yr AND a.channel_id < b.channel_id GROUP BY 1, 2""").fetchall()
        out["scope_regulars"] = con.execute("""
            SELECT s.scope, r.yr, count(DISTINCT r.user_id) FROM reg r JOIN scopes s USING (channel_id) GROUP BY 1, 2
            UNION ALL
            SELECT s.scope, 0, count(DISTINCT r.user_id) FROM reg r JOIN scopes s USING (channel_id) GROUP BY 1""").fetchall()
        LOG.logger.info(f"Community rollups computed in {time.time() - t:.0f}s.")
        t = time.time()
        q = 'SELECT video_id FROM public.user_first_message'
        out["video_first_global"] = con.execute(
            f"SELECT video_id, count(*) FROM postgres_query('pg', {_lit(q)}) GROUP BY 1").fetchall()
        LOG.logger.info(f"Global first messages counted in {time.time() - t:.0f}s.")
    return out

# rollup table -> (key columns, value columns) as stored in Postgres
TABLES = {
    "channel_audience": (["channel_id","dim","bucket"],["n"]),
    "channel_audience_monthly": (["channel_id","\"month\""],["arrivals","last_seen","regulars","active","\"returning\"","reg_returning"]),
    "channel_audience_yearly": (["channel_id","\"year\""],["regulars","active"]),
    "channel_top_chatters": (["channel_id","user_id"],["total_messages"]),
    "scope_audience": (["\"scope\"","dim","bucket"],["n"]),
    "scope_audience_monthly": (["\"scope\"","\"month\""],["arrivals","\"returning\"","reg_returning"]),
    "channel_overlap": (["channel_a","channel_b"],["shared"]),
    "regular_overlap": (["channel_a","channel_b","\"year\""],["shared"]),
    "scope_regulars": (["\"scope\"","\"year\""],["n"]),
    "channel_retention": (["channel_id","kind","cohort","age"],["n"]),
    "scope_retention": (["\"scope\"","kind","cohort","age"],["n"]),
}
STAGING = ["audience_delta","audience_month_delta","audience_year_delta","audience_video_delta","audience_new_link",
           "audience_top_candidate","audience_regular_change","audience_retention_delta","audience_activity_change"]
ZERO_ROWS_OK = ("channel_audience","scope_audience","channel_overlap","regular_overlap","scope_regulars",
                "channel_retention","scope_retention")

def verify(cur,expected:dict[str,list[tuple]],channel_id:str|None) -> int:
    cur.execute("SELECT " + " + ".join(f"(SELECT count(*) FROM {t})" for t in STAGING))
    staged = cur.fetchone()[0]
    if staged:
        LOG.logger.warning(f"{staged:,} staged delta rows aren't folded yet -- expect drift until the next fold (run with the bot stopped).")
    drift = 0
    for table,(keys,vals) in TABLES.items():
        if table not in expected:
            continue
        where = " WHERE channel_id = %s" if channel_id else ""
        cur.execute(f"SELECT {', '.join(keys + vals)} FROM {table}{where}",(channel_id,) if channel_id else None)
        have = {tuple(r[:len(keys)]): tuple(r[len(keys):]) for r in cur.fetchall()}
        want = {tuple(r[:len(keys)]): tuple(r[len(keys):]) for r in expected[table]}
        if table in ZERO_ROWS_OK:
            have = {k: v for k,v in have.items() if v != (0,)}
        if table.endswith("_monthly") or table.endswith("_yearly"):
            have = {k: v for k,v in have.items() if any(v)}
        bad = [k for k in set(have) | set(want) if have.get(k) != want.get(k)]
        drift += len(bad)
        LOG.logger.info(f"{table}: {len(want):,} expected rows, {len(bad):,} differ" +
                        ("" if not bad else f" -- e.g. {sorted(bad,key=str)[:3]}: have {[have.get(k) for k in sorted(bad,key=str)[:3]]}, want {[want.get(k) for k in sorted(bad,key=str)[:3]]}"))
    # per-video first counts
    fc = dict(expected["video_first_channel"])
    fg = dict(expected.get("video_first_global",[]))
    if channel_id:
        cur.execute("SELECT s.video_id, s.first_channel_messages FROM video_message_stats s JOIN videos v ON v.id = s.video_id WHERE v.channel_id = %s",(channel_id,))
        bad = [(v,c,fc.get(v,0)) for v,c in cur.fetchall() if c != fc.get(v,0)]
    else:
        cur.execute("SELECT video_id, first_channel_messages, first_messages FROM video_message_stats")
        bad = [(v,c,fc.get(v,0)) for v,c,g in cur.fetchall() if c != fc.get(v,0) or g != fg.get(v,0)]
    drift += len(bad)
    LOG.logger.info(f"video_message_stats first counts: {len(bad):,} videos differ" + (f" -- e.g. {bad[:3]}" if bad else ""))
    return drift

NM = len(AD.MONTHS)  # MONTHS[j] is month_index j (2017-01 onward, contiguous)

class Retention:
    """Accumulates cohort cells (cohort index, age) and the 'returning' mix from boolean
    rows x NM month matrices -- the same definitions as AD.retention_contrib / AD.mix_contrib."""
    def __init__(self):
        self.cells = {k: np.zeros(NM * NM,dtype=np.int64) for k in AD.RETENTION_KINDS}
        self.returning = {k: np.zeros(NM,dtype=np.int64) for k in AD.RETENTION_KINDS}

    def add(self,kind:str,m) -> None:
        m = m[m.any(axis=1)]
        if not len(m):
            return
        cohort = m.argmax(axis=1)
        r,j = np.nonzero(m)
        self.cells[kind] += np.bincount(cohort[r] * NM + (j - cohort[r]),minlength=NM * NM)
        self.returning[kind][1:] += (m[:,1:] & m[:,:-1]).sum(axis=0)

    def rows(self,key:str) -> list[tuple]:
        out = []
        for kind,cells in self.cells.items():
            for i in np.nonzero(cells)[0]:
                out.append((key,kind,AD.MONTHS[i // NM],int(i % NM),int(cells[i])))
        return out

def regulars(con,out:dict,full:bool,scopes:dict[str,list[str]],chunk:int=250_000) -> None:
    """From the weekly bitmaps: regulars/active per channel and month/year, 'ever regular',
    retention cohorts and the returning mix per channel; and (full) each user's regular years per
    channel into DuckDB table reg (regular overlap / scope regulars) plus distinct-per-scope
    retention. Scope unions OR packed month masks per user into one array per scope; channels are
    visited company -> branch -> group, so only about three scope arrays are alive at a time."""
    t = time.time()
    monthly = {(c,m): list(v[:2]) + [0,0,0,0] for c,m,*v in out["channel_audience_monthly"]}
    yearly,ever = {},Counter()
    out["channel_retention"] = []
    tmp = tempfile.NamedTemporaryFile("w",suffix=".csv",delete=False,newline="")
    w = csv.writer(tmp)
    channels = [c for (c,) in con.execute("SELECT DISTINCT channel_id FROM uca").fetchall()]
    n_users = 0
    if full:
        con.execute("CREATE OR REPLACE TABLE uid AS SELECT user_id, CAST(row_number() OVER () - 1 AS BIGINT) id "
                    "FROM (SELECT DISTINCT user_id FROM uca)")
        n_users = con.execute("SELECT count(*) FROM uid").fetchone()[0]
        channels.sort(key=lambda c: tuple(reversed(scopes.get(c,[]))))
    remaining = Counter(s for c in channels for s in scopes.get(c,[]))
    live:dict[str,np.ndarray] = {}   # scope -> n_users x 2 x packed months (uint8)
    if full:
        out["scope_retention"],out["scope_mix"] = [],{}

    def finish_scope(s:str) -> None:
        arr = live.pop(s)
        acc = Retention()
        for i in range(0,n_users,500_000):
            part = arr[i:i + 500_000]
            for k,kind in enumerate(AD.RETENTION_KINDS):
                acc.add(kind,np.unpackbits(part[:,k,:],axis=1,count=NM).astype(bool))
        out["scope_retention"] += acc.rows(s)
        for j in range(NM):
            ra,rr = int(acc.returning["A"][j]),int(acc.returning["R"][j])
            if ra or rr:
                out["scope_mix"][(s,AD.MONTHS[j])] = [0,ra,rr]

    for ch in channels:
        if full:
            rows = con.execute("SELECT u.user_id, u.weeks, i.id FROM uca u JOIN uid i USING (user_id) WHERE u.channel_id = ?",[ch]).fetchall()
        else:
            rows = con.execute("SELECT user_id, weeks FROM uca WHERE channel_id = ?",[ch]).fetchall()
        ch_scopes = scopes.get(ch,[]) if full else []
        for s in ch_scopes:
            if s not in live:
                live[s] = np.zeros((n_users,2,(NM + 7) // 8),dtype=np.uint8)
        ret = Retention()
        for i in range(0,len(rows),chunk):
            part = rows[i:i + chunk]
            reg_m,act_m = AD.regular_matrices([r[1] for r in part])
            ret.add("A",act_m); ret.add("R",reg_m)
            if ch_scopes:
                ids = np.fromiter((r[2] for r in part),dtype=np.int64,count=len(part))
                packed = np.stack([np.packbits(act_m,axis=1),np.packbits(reg_m,axis=1)],axis=1)
                for s in ch_scopes:
                    live[s][ids] |= packed  # ids are unique within one channel
            for j,m in enumerate(AD.MONTHS):
                r,a_ = int(reg_m[:,j].sum()),int(act_m[:,j].sum())
                if r or a_:
                    v = monthly.setdefault((ch,m),[0,0,0,0,0,0])
                    v[2] += r; v[3] += a_
            reg_y = np.maximum.reduceat(reg_m,AD.YEAR_STARTS,axis=1)
            act_y = np.maximum.reduceat(act_m,AD.YEAR_STARTS,axis=1)
            for j,y in enumerate(AD.YEARS):
                r,a_ = int(reg_y[:,j].sum()),int(act_y[:,j].sum())
                if r or a_:
                    v = yearly.setdefault((ch,y),[0,0])
                    v[0] += r; v[1] += a_
            ever[ch] += int(reg_m.any(axis=1).sum())
            if full:
                for ri,yi in zip(*np.nonzero(reg_y)):
                    w.writerow((part[ri][0],ch,AD.YEARS[yi]))
        out["channel_retention"] += ret.rows(ch)
        for j in range(NM):
            ra,rr = int(ret.returning["A"][j]),int(ret.returning["R"][j])
            if ra or rr:
                v = monthly.setdefault((ch,AD.MONTHS[j]),[0,0,0,0,0,0])
                v[4] += ra; v[5] += rr
        for s in ch_scopes:
            remaining[s] -= 1
            if remaining[s] == 0:
                finish_scope(s)
    assert not live,f"unfinished scopes {list(live)}"
    tmp.close()
    out["channel_audience_monthly"] = [(c,m,*v) for (c,m),v in monthly.items()]
    out["channel_audience_yearly"] = [(c,y,*v) for (c,y),v in yearly.items()]
    out["channel_audience"] += [(c,"regular","ever",n) for c,n in ever.items() if n]
    if full:
        con.execute(f"CREATE OR REPLACE TABLE reg AS SELECT * FROM read_csv({_lit(tmp.name)}, header=false, "
                    "columns={'user_id': 'VARCHAR', 'channel_id': 'VARCHAR', 'yr': 'INTEGER'})")
    os.unlink(tmp.name)
    LOG.logger.info(f"Regulars computed in {time.time() - t:.0f}s.")

def write(db:DB.PostgresClass,expected:dict[str,list[tuple]]) -> None:
    cur = db.cursor
    t = time.time()
    cur.execute("TRUNCATE " + ", ".join(STAGING + list(TABLES) + ["audience_bucket_def"]))
    for table,(keys,vals) in TABLES.items():
        rows = [r for r in expected[table] if any(v != 0 for v in r[len(keys):])]
        execute_values(cur,f"INSERT INTO {table} ({', '.join(keys + vals)}) VALUES %s",rows,page_size=5000)
        LOG.logger.info(f"{table}: {len(rows):,} rows")
    execute_values(cur,"INSERT INTO audience_bucket_def (dim,bucket,ord,lo,hi) VALUES %s",AD.bucket_defs())

    # First-message counts per video: only rewrite the rows that are actually wrong.
    cur.execute("CREATE TEMP TABLE vf (video_id text PRIMARY KEY, g int8, c int8) ON COMMIT DROP")
    g = Counter(dict(expected["video_first_global"]))
    c = Counter(dict(expected["video_first_channel"]))
    execute_values(cur,"INSERT INTO vf VALUES %s",[(v,g.get(v,0),c.get(v,0)) for v in set(g) | set(c) if v is not None],page_size=5000)
    cur.execute("""
        UPDATE video_message_stats s SET first_messages = coalesce(vf.g, 0), first_channel_messages = coalesce(vf.c, 0)
        FROM video_message_stats s2 LEFT JOIN vf ON vf.video_id = s2.video_id
        WHERE s.video_id = s2.video_id
          AND (s2.first_messages, s2.first_channel_messages) IS DISTINCT FROM (coalesce(vf.g, 0), coalesce(vf.c, 0))""")
    LOG.logger.info(f"video_message_stats: {cur.rowcount:,} first-count rows corrected")
    db.database.commit()
    LOG.logger.info(f"Baseline written in {time.time() - t:.0f}s.")

def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild or verify the pre-generated audience rollups.")
    ap.add_argument("--verify",action="store_true",help="recompute and report drift; write nothing")
    ap.add_argument("--channel",help="verify one channel only (by name); cheap for small channels")
    ap.add_argument("--threads",type=int,default=4)
    ap.add_argument("--yes",action="store_true",help="skip the 'is the bot stopped?' prompt")
    args = ap.parse_args()
    if args.channel and not args.verify:
        ap.error("--channel only works with --verify (a rebuild is all-or-nothing)")

    db = DB.PostgresClass()
    cur = db.cursor
    channel_id = None
    if args.channel:
        cur.execute("SELECT user_id FROM channel_directory WHERE lower(name) = lower(%s)",(args.channel,))
        row = cur.fetchone()
        if row is None:
            raise SystemExit(f"unknown channel {args.channel}")
        channel_id = row[0]
    if not args.verify and not args.yes:
        if input("Rebuild replaces every audience rollup. Is the bot stopped? [y/N] ").strip().lower() != "y":
            raise SystemExit("aborted")

    cur.execute('SELECT user_id, channel_type, branch, "group" FROM channel_directory')
    scopes = AD.channel_scopes(cur.fetchall())
    db.database.commit()

    con = A.Base_Connection(args.threads)
    load(con,channel_id)
    expected = compute(con,full=channel_id is None,scopes=scopes)
    if args.verify:
        drift = verify(cur,expected,channel_id)
        db.database.rollback()
        LOG.logger.info("No drift." if drift == 0 else f"{drift:,} rows drifted.")
        raise SystemExit(0 if drift == 0 else 1)
    write(db,expected)

if __name__ == "__main__":
    main()
