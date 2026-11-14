# Native Stuff
from collections import Counter
from datetime import date,datetime,timezone
from itertools import combinations

"""
Pre-generated audience statistics: turns what record_message_stats() reports about each
user_channel_activity row it upserted (old and new values, via Postgres 18's RETURNING OLD/NEW)
into small deltas for the audience rollup tables (sql/audience_rollups.sql), so nothing ever has
to rescan user_channel_activity (~28M rows) to answer audience questions.

Pure functions, no database access -- the single source of truth for bucket definitions, shared
by the ingest path (modules/Classes.py Get_Messages -> Database.StageAudienceDeltas), the fold
(Database.FoldAudience) and the from-scratch rebuild (build_audience_baseline.py, which turns the
same definitions into SQL with bucket_case_sql()).

Rollup dimensions (channel_audience.dim):
    msgs   -- histogram of each chatter's message count on the channel (1..20 exact, then ranges);
              audience = sum, one-time = bucket "1", core (100+) = buckets from 100 up, and the
              median comes from the histogram.
    badge  -- highest membership badge seen (max_member_status: -1 never a member, 0 new member,
              then months), "none" when unknown.
    stay   -- time between a chatter's first and last message on the channel.
    mod    -- "yes": chatters who have ever been a moderator on the channel.
"""

DAY = 86400.0

# channel_id -> its current #100 total in channel_top_chatters (Database.GetTopThreshold), set by
# Main.py when a channel starts; read-only for the ingest workers. Missing = 0 = every chatter is
# a top-list candidate.
TOP_THRESHOLDS:dict[str,int] = {}

# (label, lo, hi) inclusive message-count ranges; hi None = open-ended.
MSG_BUCKETS:list[tuple[str,int,int|None]] = [(str(n),n,n) for n in range(1,21)] + [
    ("21-29",21,29),("30-49",30,49),("50-99",50,99),("100-199",100,199),("200-499",200,499),
    ("500-999",500,999),("1000-1999",1000,1999),("2000-4999",2000,4999),("5000-9999",5000,9999),
    ("10000+",10000,None),
]

# (label, lo, hi) in seconds, lo <= span < hi; hi None = open-ended.
STAY_BUCKETS:list[tuple[str,float,float|None]] = [
    ("Under a day",0,DAY),("Under a month",DAY,30 * DAY),("1-6 months",30 * DAY,182 * DAY),
    ("6-12 months",182 * DAY,365 * DAY),("1-3 years",365 * DAY,1095 * DAY),("3+ years",1095 * DAY,None),
]

def msg_bucket(total:int) -> str:
    for label,lo,hi in MSG_BUCKETS:
        if total >= lo and (hi is None or total <= hi):
            return label
    raise ValueError(f"message count {total} has no bucket")

def stay_bucket(span_seconds:float) -> str:
    span = max(0.0,span_seconds)
    for label,lo,hi in STAY_BUCKETS:
        if span >= lo and (hi is None or span < hi):
            return label
    raise ValueError(f"stay span {span_seconds} has no bucket")

def badge_bucket(status:int|None) -> str:
    return "none" if status is None else str(int(status))

def month_of(ts:float) -> date:
    """First day of the UTC month of an epoch-seconds timestamp."""
    d = datetime.fromtimestamp(ts,timezone.utc)
    return date(d.year,d.month,1)

# ---- Weekly activity and "regulars" (user_channel_activity.active_weeks)
#
# active_weeks is a fixed-length bitmap: bit i (0 = leftmost, like Postgres set_bit) = the chatter
# chatted on the channel in week i, counting Mondays from WEEK_BASE (UTC). A message's week is its
# STREAM's start week (videos.start_time, falling back to publishedat), not the message time, so
# the bot and the from-scratch rebuild (video_user_seen x videos) always agree.
#
# A REGULAR (user decision, 2026-11-08) is any chatter -- member or not -- who chatted in each of
# REGULAR_RUN consecutive weeks. They're a regular in every month touched by such a run (a week
# belongs to the month of its Monday) and in every year containing one of those months. Bits are
# only ever set, so regular months/years only grow: deltas stay exact whatever order videos are
# processed in (an older stream processed later can complete a run).

WEEK_BASE = date(2017,1,2)  # a Monday
WEEK_BITS = 728             # through late 2030 -- re-pad the column before then
REGULAR_RUN = 4

def week_index(start:datetime|date|None) -> int|None:
    """The week bit for a stream starting at `start` (naive UTC datetime or date); None if unknown
    or outside the bitmap."""
    if start is None:
        return None
    d = start.date() if isinstance(start,datetime) else start
    i = (d - WEEK_BASE).days // 7
    return i if 0 <= i < WEEK_BITS else None

def week_month(i:int) -> date:
    """First day of the month containing week i's Monday."""
    from datetime import timedelta
    d = WEEK_BASE + timedelta(days=7 * i)
    return date(d.year,d.month,1)

def active_months(bits:str|None) -> set[date]:
    return {week_month(i) for i,b in enumerate(bits or "") if b == "1"}

def regular_months(bits:str|None) -> set[date]:
    """Months touched by any run of REGULAR_RUN or more consecutive active weeks."""
    months,run = set(),[]
    for i,b in enumerate((bits or "") + "0"):
        if b == "1":
            run.append(i)
            continue
        if len(run) >= REGULAR_RUN:
            months |= {week_month(w) for w in run}
        run = []
    return months

def years_of(months:set[date]) -> set[int]:
    return {m.year for m in months}

# ---- Retention (user decisions, 2026-11-08)
#
# A chatter's COHORT on a channel (or a scope) is the first month they were active there; they're
# RETAINED at age N if active in the month N months later (classic cohort retention, so a curve can
# dip and recover). Kind 'A' uses active months, kind 'R' regular months (cohort = first month as a
# regular). The monthly mix adds RETURNING: active this month AND last month; new = age 0, and
# "back after a break" = active - new - returning. Month sets only grow, so every delta below is the
# exact difference of two full contribution sets (a late-processed older stream can move a cohort
# earlier -- that chatter's cells all shift, still exactly).

RETENTION_KINDS = ("A","R")
MIX_COLUMN = {"A":"returning","R":"reg_returning"}

def month_index(m:date) -> int:
    """Months since 2017-01 (the first month of the week bitmap)."""
    return (m.year - 2017) * 12 + m.month - 1

def month_at(i:int) -> date:
    return date(2017 + i // 12,i % 12 + 1,1)

def retention_contrib(months:set[date]) -> set[tuple[date,int]]:
    """(cohort, age) cells one chatter counts in."""
    if not months:
        return set()
    c = min(months)
    ci = month_index(c)
    return {(c,month_index(m) - ci) for m in months}

def mix_contrib(months:set[date]) -> set[date]:
    """Months the chatter was also active in the month before ("returning")."""
    idx = {month_index(m) for m in months}
    return {month_at(i) for i in idx if i - 1 in idx}

def retention_deltas(old:set[date],new:set[date],kind:str) -> tuple[Counter,Counter]:
    """One chatter's month set grew from `old` to `new` -> (retention[(kind, cohort, age)],
    mix[(month, MIX_COLUMN[kind])]) deltas."""
    ret,mix = Counter(),Counter()
    o,n = retention_contrib(old),retention_contrib(new)
    for c,a in n - o:
        ret[(kind,c,a)] += 1
    for c,a in o - n:
        ret[(kind,c,a)] -= 1
    om,nm = mix_contrib(old),mix_contrib(new)
    for m in nm - om:
        mix[(m,MIX_COLUMN[kind])] += 1
    for m in om - nm:
        mix[(m,MIX_COLUMN[kind])] -= 1
    return ret,mix

def scope_retention_deltas(before:dict[str,tuple[set,set]],after:dict[str,tuple[set,set]],
                           scopes:dict[str,list[str]]) -> tuple[Counter,Counter]:
    """
    One user's change in active/regular months per channel -> distinct-per-scope retention deltas.
    In a scope, a chatter is active (or a regular) in a month if they were on ANY of its channels.

    :param before / after: channel_id -> (active months, regular months); missing = none.
    :returns: (retention[(scope, kind, cohort, age)], mix[(scope, month, column)]).
    """
    ret,mix = Counter(),Counter()
    changed = {ch for ch in set(before) | set(after) if before.get(ch) != after.get(ch)}
    touched = {s for ch in changed for s in scopes.get(ch,[])}
    empty = (set(),set())
    for s in touched:
        chs = [ch for ch in set(before) | set(after) if s in scopes.get(ch,[])]
        for k,kind in enumerate(RETENTION_KINDS):
            b = set().union(*(before.get(ch,empty)[k] for ch in chs))
            a = set().union(*(after.get(ch,empty)[k] for ch in chs))
            if a == b:
                continue
            r,m = retention_deltas(b,a,kind)
            for (kd,c,age),v in r.items():
                ret[(s,kd,c,age)] += v
            for (mo,col),v in m.items():
                mix[(s,mo,col)] += v
    for c in (ret,mix):
        for k in [k for k,v in c.items() if v == 0]:
            del c[k]
    return ret,mix

# Vectorised regulars for set-based rebuilds (build_audience_baseline.py): week bit -> month -> year.
WEEK_MONTH = [week_month(i) for i in range(WEEK_BITS)]
MONTHS = sorted(set(WEEK_MONTH))
MONTH_STARTS = [next(i for i,m in enumerate(WEEK_MONTH) if m == mm) for mm in MONTHS]
YEARS = sorted({m.year for m in MONTHS})
YEAR_STARTS = [next(i for i,m in enumerate(MONTHS) if m.year == y) for y in YEARS]
_ZERO = "0" * WEEK_BITS

def regular_matrices(weeks:list[str|None]):
    """The same rules as AudienceDeltas.regular_months / active_months, vectorised with numpy:
    (regular months, active months), each rows x len(MONTHS) booleans. Needs numpy."""
    import numpy as np
    a = np.frombuffer("".join(w if w and len(w) == WEEK_BITS else _ZERO for w in weeks).encode("ascii"),
                      dtype=np.uint8).reshape(-1,WEEK_BITS) == ord("1")
    n = REGULAR_RUN
    starts = a[:,:WEEK_BITS - n + 1].copy()          # a run of n consecutive active weeks starts here
    for k in range(1,n):
        starts &= a[:,k:WEEK_BITS - n + 1 + k]
    in_run = np.zeros_like(a)
    for k in range(n):
        in_run[:,k:WEEK_BITS - n + 1 + k] |= starts
    return (np.maximum.reduceat(in_run,MONTH_STARTS,axis=1),np.maximum.reduceat(a,MONTH_STARTS,axis=1))

def bucket_defs() -> list[tuple[str,str,int,float|None,float|None]]:
    """(dim, bucket, ord, lo, hi) rows for audience_bucket_def -- dashboards order and label by these."""
    rows = [("msgs",label,i,lo,hi) for i,(label,lo,hi) in enumerate(MSG_BUCKETS)]
    rows += [("stay",label,i,lo,hi) for i,(label,lo,hi) in enumerate(STAY_BUCKETS)]
    return rows

def bucket_case_sql(dim:str,expr:str) -> str:
    """SQL CASE expression assigning `expr` to this dim's bucket label -- for set-based rebuilds
    (build_audience_baseline.py), so they bucket exactly like the Python functions above."""
    if dim == "msgs":
        parts = [f"WHEN {expr} >= {lo}" + (f" AND {expr} <= {hi}" if hi is not None else "") + f" THEN '{label}'"
                 for label,lo,hi in MSG_BUCKETS]
    elif dim == "stay":
        parts = [f"WHEN greatest({expr}, 0) >= {lo}" + (f" AND greatest({expr}, 0) < {hi}" if hi is not None else "") + f" THEN '{label}'"
                 for label,lo,hi in STAY_BUCKETS]
    else:
        raise ValueError(dim)
    return "CASE " + " ".join(parts) + " END"

# Columns record_message_stats_batch() returns per message, after its 1-based index. o_weeks/n_weeks
# are the active_weeks bitmaps as '0101...' text, only when this message changed them (else NULL).
RESULT_COLUMNS = ("inserted","o_total","n_total","o_status","n_status","o_first","n_first","o_last","n_last",
                  "o_mod","n_mod","o_fvid","n_fvid","g_changed","g_old_vid","g_new_vid","o_weeks","n_weeks")

class BatchDeltas:
    """Everything one record_message_stats_batch() call changed, aggregated for staging."""
    def __init__(self):
        self.channel:Counter = Counter()   # (channel_id, dim, bucket) -> delta
        self.month:Counter = Counter()     # (channel_id, month, kind) -> delta; kind 'arrivals' | 'last_seen'
        self.video:Counter = Counter()     # (video_id, kind) -> delta; kind 'first_global' | 'first_channel'
        self.links:dict[tuple[str,str],tuple[bool,float|None]] = {}  # (channel_id, user_id) -> (is_new, o_first)
        self.top:dict[tuple[str,str],int] = {}  # (channel_id, user_id) -> new total, only at/above the channel's top-100 threshold
        # Weekly activity / regulars: (channel_id, month, kind) and (channel_id, year, kind), kind 'regulars' | 'active'
        self.reg_month:Counter = Counter()
        self.reg_year:Counter = Counter()
        # (channel_id, user_id) -> (old regular years, new regular years) when the set grew -- feeds
        # the regular overlap / distinct regulars per scope in the fold.
        self.reg_changes:dict[tuple[str,str],tuple[frozenset,frozenset]] = {}
        # Retention: (channel_id, kind, cohort, age) -> delta; the monthly mix goes into reg_month
        # with kind 'returning' | 'reg_returning'.
        self.retention:Counter = Counter()
        # (channel_id, user_id) -> (months newly active, months newly regular) -- feeds the
        # distinct-per-scope retention in the fold.
        self.activity:dict[tuple[str,str],tuple[frozenset,frozenset]] = {}

    def prune(self) -> "BatchDeltas":
        for c in (self.channel,self.month,self.video,self.reg_month,self.reg_year,self.retention):
            for k in [k for k,v in c.items() if v == 0]:
                del c[k]
        return self

def _move(counter:Counter,old_key,new_key) -> None:
    if old_key != new_key:
        if old_key is not None:
            counter[old_key] -= 1
        if new_key is not None:
            counter[new_key] += 1

def batch_deltas(batch:list[tuple],results:list[tuple],top_thresholds:dict[str,int]|None=None) -> BatchDeltas:
    """
    :param batch: the (channel_id, video_id, user_id, ...) tuples sent to record_message_stats_batch(), in order.
    :param results: its rows: (idx, *RESULT_COLUMNS), idx 1-based into batch.
    :param top_thresholds: channel_id -> current #100 total in channel_top_chatters (0 or missing = take every user).

    A user can appear several times in one batch; each row's old/new values chain (the upsert
    sees the previous row's result), so summing each row's transition is exact.
    """
    d = BatchDeltas()
    thresholds = top_thresholds or {}
    for row in results:
        r = dict(zip(("idx",) + RESULT_COLUMNS,row))
        channel_id,user_id = batch[r["idx"] - 1][0],batch[r["idx"] - 1][2]
        new = r["inserted"]

        def move(dim,old_bucket,new_bucket):
            _move(d.channel,None if new else (channel_id,dim,old_bucket),(channel_id,dim,new_bucket))

        move("msgs",None if new else msg_bucket(r["o_total"]),msg_bucket(r["n_total"]))
        move("badge",None if new else badge_bucket(r["o_status"]),badge_bucket(r["n_status"]))
        move("stay",None if new else stay_bucket(r["o_last"] - r["o_first"]),stay_bucket(r["n_last"] - r["n_first"]))
        if r["n_mod"] and (new or not r["o_mod"]):
            d.channel[(channel_id,"mod","yes")] += 1

        _move(d.month,None if new else (channel_id,month_of(r["o_first"]),"arrivals"),(channel_id,month_of(r["n_first"]),"arrivals"))
        _move(d.month,None if new else (channel_id,month_of(r["o_last"]),"last_seen"),(channel_id,month_of(r["n_last"]),"last_seen"))

        _move(d.video,None if new else (r["o_fvid"],"first_channel"),(r["n_fvid"],"first_channel"))
        if r["g_changed"]:
            _move(d.video,None if r["g_old_vid"] is None else (r["g_old_vid"],"first_global"),(r["g_new_vid"],"first_global"))

        # Community needs to know when a user joins a channel, or when their first message on it
        # moves to an earlier month (that can move their arrival month in a group/branch).
        key = (channel_id,user_id)
        if new or month_of(r["o_first"]) != month_of(r["n_first"]):
            was_new,o_first = d.links.get(key,(False,None))
            if new or was_new:
                d.links[key] = (True,None)
            else:
                # first-message time only ever moves earlier, so the oldest value is the largest
                d.links[key] = (False,r["o_first"] if o_first is None else max(o_first,r["o_first"]))

        if r["n_total"] >= thresholds.get(channel_id,0):
            d.top[key] = max(d.top.get(key,0),r["n_total"])

        if r["n_weeks"] is not None:  # this message set a new week bit
            o_reg,n_reg = regular_months(r["o_weeks"]),regular_months(r["n_weeks"])
            o_act,n_act = active_months(r["o_weeks"]),active_months(r["n_weeks"])
            for m in n_reg - o_reg:
                d.reg_month[(channel_id,m,"regulars")] += 1
            for m in n_act - o_act:
                d.reg_month[(channel_id,m,"active")] += 1
            o_ry,n_ry = years_of(o_reg),years_of(n_reg)
            for y in n_ry - o_ry:
                d.reg_year[(channel_id,y,"regulars")] += 1
            for y in years_of(n_act) - years_of(o_act):
                d.reg_year[(channel_id,y,"active")] += 1
            if not o_reg and n_reg:
                d.channel[(channel_id,"regular","ever")] += 1
            if n_ry != o_ry:
                prev = d.reg_changes.get(key)
                # keep the oldest "before" and the newest "after" across this batch
                d.reg_changes[key] = (prev[0] if prev else frozenset(o_ry),frozenset(n_ry))
            for kind,o_m,n_m in (("A",o_act,n_act),("R",o_reg,n_reg)):
                if n_m != o_m:
                    ret,mix = retention_deltas(o_m,n_m,kind)
                    for (k,c,a),v in ret.items():
                        d.retention[(channel_id,k,c,a)] += v
                    for (m,col),v in mix.items():
                        d.reg_month[(channel_id,m,col)] += v
            if n_act != o_act or n_reg != o_reg:
                pa,pr = d.activity.get(key,(frozenset(),frozenset()))
                d.activity[key] = (pa | (n_act - o_act),pr | (n_reg - o_reg))
    return d.prune()

def regular_community_deltas(before:dict[str,set[int]],after:dict[str,set[int]],scopes:dict[str,list[str]]) -> tuple[Counter,Counter]:
    """
    One user's change in regular years per channel -> regulars overlap / scope deltas.

    :param before / after: channel_id -> years the user was a regular there (missing = none).
    :returns: (pairs, scopes):
        pairs[(a, b, year)] with a < b: change in chatters who were regulars of both a and b in
            that year; year 0 = regulars of both in at least one COMMON year.
        scopes[(scope, year)]: change in distinct regulars of the scope in that year; year 0 = ever.
    """
    pairs,scope_d = Counter(),Counter()
    def pair_years(m:dict[str,set[int]]) -> set[tuple[str,str,int]]:
        out = set()
        for a,b in combinations(sorted(c for c in m if m[c]),2):
            common = m[a] & m[b]
            out |= {(a,b,y) for y in common}
            if common:
                out.add((a,b,0))
        return out
    def scope_years(m:dict[str,set[int]]) -> set[tuple[str,int]]:
        out = set()
        for ch,years in m.items():
            for s in scopes.get(ch,[]):
                out |= {(s,y) for y in years}
                if years:
                    out.add((s,0))
        return out
    for k in pair_years(after) - pair_years(before):
        pairs[k] += 1
    for k in pair_years(before) - pair_years(after):
        pairs[k] -= 1
    for k in scope_years(after) - scope_years(before):
        scope_d[k] += 1
    for k in scope_years(before) - scope_years(after):
        scope_d[k] -= 1
    return pairs,scope_d

def community_deltas(before:dict[str,float],after:dict[str,float],scopes:dict[str,list[str]]) -> tuple[Counter,Counter,Counter]:
    """
    One user's change in channel membership -> Community rollup deltas.

    :param before: channel_id -> first-message timestamp, before this fold's changes.
    :param after: channel_id -> first-message timestamp, now (a superset of before's keys).
    :param scopes: channel_id -> the scopes it belongs to (e.g. ["group:EN/Myth", "branch:EN", "company"]).
    :returns: (pairs, scope_k, scope_month) Counters:
        pairs[(a, b)] with a < b: change in chatters shared by channels a and b;
        scope_k[(scope, k)]: change in chatters who chat on exactly k of the scope's channels
            (distinct audience of a scope = sum over k);
        scope_month[(scope, month)]: change in chatters whose first message in the scope fell in that month.
    """
    pairs,scope_k,scope_month = Counter(),Counter(),Counter()
    old_set,new_set = set(before),set(after)
    for a,b in combinations(sorted(new_set),2):
        if a not in old_set or b not in old_set:
            pairs[(a,b)] += 1

    touched = {s for ch in new_set for s in scopes.get(ch,[])}
    for s in touched:
        b_chs = [ch for ch in old_set if s in scopes.get(ch,[])]
        a_chs = [ch for ch in new_set if s in scopes.get(ch,[])]
        if len(a_chs) != len(b_chs):
            if b_chs:
                scope_k[(s,len(b_chs))] -= 1
            scope_k[(s,len(a_chs))] += 1
        b_month = month_of(min(before[ch] for ch in b_chs)) if b_chs else None
        a_month = month_of(min(after[ch] for ch in a_chs))
        _move(scope_month,None if b_month is None else (s,b_month),(s,a_month))
    for c in (pairs,scope_k,scope_month):
        for k in [k for k,v in c.items() if v == 0]:
            del c[k]
    return pairs,scope_k,scope_month

def channel_scopes(rows:list[tuple[str,str,str,str]]) -> dict[str,list[str]]:
    """
    channel_directory rows (user_id, channel_type, branch, group) -> each channel's scopes, per
    the dashboards' folder design: talent channels count toward their group, branch and the
    company; official channels (branch and unit channels) only toward "official".
    """
    out = {}
    for channel_id,channel_type,branch,group in rows:
        if channel_type == "official":
            out[channel_id] = ["official"]
        else:
            out[channel_id] = [f"group:{branch}/{group}",f"branch:{branch}","company"]
    return out
