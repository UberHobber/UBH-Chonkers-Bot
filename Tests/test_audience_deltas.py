"""Unit tests for modules/AudienceDeltas.py. Run: .venv\\Scripts\\python.exe -m unittest discover -s Tests"""
import os,sys,unittest
from collections import Counter
from datetime import date,datetime,timezone

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import modules.AudienceDeltas as AD

def ts(y,m,d=1) -> float:
    return datetime(y,m,d,12,tzinfo=timezone.utc).timestamp()

CH = "chan"
def msg(user,video="v1") -> tuple:
    # (channel_id, video_id, user_id, ...rest unused by batch_deltas)
    return (CH,video,user) + (None,) * 10

def res(idx,*,inserted=False,o_total=None,n_total=1,o_status=None,n_status=-1,o_first=None,n_first=ts(2024,1),
        o_last=None,n_last=ts(2024,1),o_mod=None,n_mod=False,o_fvid=None,n_fvid="v1",
        g_changed=False,g_old_vid=None,g_new_vid=None,o_weeks=None,n_weeks=None) -> tuple:
    return (idx,inserted,o_total,n_total,o_status,n_status,o_first,n_first,o_last,n_last,o_mod,n_mod,o_fvid,n_fvid,
            g_changed,g_old_vid,g_new_vid,o_weeks,n_weeks)

def bits(*weeks) -> str:
    out = ["0"] * AD.WEEK_BITS
    for w in weeks:
        out[w] = "1"
    return "".join(out)

def wk(y,m,d) -> int:
    return AD.week_index(date(y,m,d))

class Buckets(unittest.TestCase):
    def test_msg_buckets(self):
        self.assertEqual(AD.msg_bucket(1),"1")
        self.assertEqual(AD.msg_bucket(20),"20")
        self.assertEqual(AD.msg_bucket(21),"21-29")
        self.assertEqual(AD.msg_bucket(99),"50-99")
        self.assertEqual(AD.msg_bucket(100),"100-199")
        self.assertEqual(AD.msg_bucket(123456),"10000+")

    def test_stay_buckets(self):
        self.assertEqual(AD.stay_bucket(0),"Under a day")
        self.assertEqual(AD.stay_bucket(AD.DAY),"Under a month")
        self.assertEqual(AD.stay_bucket(-5),"Under a day")
        self.assertEqual(AD.stay_bucket(4 * 365 * AD.DAY),"3+ years")

    def test_badge_and_month(self):
        self.assertEqual(AD.badge_bucket(None),"none")
        self.assertEqual(AD.badge_bucket(-1),"-1")
        self.assertEqual(AD.month_of(ts(2024,3,31)),date(2024,3,1))

    def test_sql_case_matches_python(self):
        # Evaluate the generated CASE with a tiny interpreter: every boundary must agree.
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.create_function("greatest",2,max)
        for n in [1,2,19,20,21,29,30,99,100,101,999,1000,9999,10000,50000]:
            got = con.execute(f"SELECT {AD.bucket_case_sql('msgs',str(n))}").fetchone()[0]
            self.assertEqual(got,AD.msg_bucket(n),n)
        for s in [-1,0,AD.DAY - 1,AD.DAY,30 * AD.DAY,182 * AD.DAY,365 * AD.DAY,1095 * AD.DAY,5000 * AD.DAY]:
            got = con.execute(f"SELECT {AD.bucket_case_sql('stay',repr(float(s)))}").fetchone()[0]
            self.assertEqual(got,AD.stay_bucket(s),s)

class BatchDeltas(unittest.TestCase):
    def test_insert(self):
        d = AD.batch_deltas([msg("u1")],[res(1,inserted=True)])
        self.assertEqual(d.channel,Counter({(CH,"msgs","1"):1,(CH,"badge","-1"):1,(CH,"stay","Under a day"):1}))
        self.assertEqual(d.month,Counter({(CH,date(2024,1,1),"arrivals"):1,(CH,date(2024,1,1),"last_seen"):1}))
        self.assertEqual(d.video,Counter({("v1","first_channel"):1}))
        self.assertEqual(d.links,{(CH,"u1"):(True,None)})

    def test_second_message_leaves_one_time(self):
        d = AD.batch_deltas([msg("u1")],[res(1,o_total=1,n_total=2,o_status=-1,n_status=-1,o_first=ts(2024,1),
                                             o_last=ts(2024,1),o_mod=False,o_fvid="v1")])
        self.assertEqual(d.channel,Counter({(CH,"msgs","1"):-1,(CH,"msgs","2"):1}))
        self.assertEqual(d.month,Counter())
        self.assertEqual(d.links,{})

    def test_becomes_core(self):
        d = AD.batch_deltas([msg("u1")],[res(1,o_total=99,n_total=100,o_status=-1,o_first=ts(2024,1),o_last=ts(2024,1),
                                             o_mod=False,o_fvid="v1")])
        self.assertEqual(d.channel,Counter({(CH,"msgs","50-99"):-1,(CH,"msgs","100-199"):1}))

    def test_badge_moves(self):
        d = AD.batch_deltas([msg("u1"),msg("u1")],[
            res(1,o_total=5,n_total=6,o_status=-1,n_status=0,o_first=ts(2024,1),o_last=ts(2024,1),o_mod=False,o_fvid="v1"),
            res(2,o_total=6,n_total=7,o_status=0,n_status=12,o_first=ts(2024,1),o_last=ts(2024,1),o_mod=False,o_fvid="v1"),
        ])
        self.assertEqual(d.channel[(CH,"badge","-1")],-1)
        self.assertEqual(d.channel[(CH,"badge","0")],0)
        self.assertEqual(d.channel[(CH,"badge","12")],1)
        self.assertNotIn((CH,"badge","0"),d.channel)  # pruned

    def test_first_moves_earlier(self):
        d = AD.batch_deltas([msg("u1","v0")],[res(1,o_total=3,n_total=4,o_status=-1,o_first=ts(2024,5),n_first=ts(2023,2),
                                                  o_last=ts(2024,6),n_last=ts(2024,6),o_mod=False,o_fvid="v5",n_fvid="v0",
                                                  g_changed=True,g_old_vid="v5",g_new_vid="v0")])
        self.assertEqual(d.month,Counter({(CH,date(2024,5,1),"arrivals"):-1,(CH,date(2023,2,1),"arrivals"):1}))
        self.assertEqual(d.video,Counter({("v5","first_channel"):-1,("v0","first_channel"):1,
                                          ("v5","first_global"):-1,("v0","first_global"):1}))
        self.assertEqual(d.links,{(CH,"u1"):(False,ts(2024,5))})
        self.assertEqual(d.channel,Counter({(CH,"msgs","3"):-1,(CH,"msgs","4"):1,
                                            (CH,"stay","1-6 months"):-1,(CH,"stay","1-3 years"):1}))

    def test_last_seen_month_moves(self):
        d = AD.batch_deltas([msg("u1")],[res(1,o_total=3,n_total=4,o_status=-1,o_first=ts(2024,1),n_first=ts(2024,1),
                                             o_last=ts(2024,1),n_last=ts(2024,2),o_mod=False,o_fvid="v1")])
        self.assertEqual(d.month,Counter({(CH,date(2024,1,1),"last_seen"):-1,(CH,date(2024,2,1),"last_seen"):1}))

    def test_moderator_once(self):
        d = AD.batch_deltas([msg("u1"),msg("u1")],[
            res(1,o_total=1,n_total=2,o_status=-1,o_first=ts(2024,1),o_last=ts(2024,1),o_mod=False,n_mod=True,o_fvid="v1"),
            res(2,o_total=2,n_total=3,o_status=-1,o_first=ts(2024,1),o_last=ts(2024,1),o_mod=True,n_mod=True,o_fvid="v1"),
        ])
        self.assertEqual(d.channel[(CH,"mod","yes")],1)

    def test_same_user_twice_new_then_update(self):
        d = AD.batch_deltas([msg("u1"),msg("u1")],[
            res(1,inserted=True,n_total=1),
            res(2,o_total=1,n_total=2,o_status=-1,o_first=ts(2024,1),o_last=ts(2024,1),o_mod=False,o_fvid="v1"),
        ])
        self.assertEqual(d.channel[(CH,"msgs","2")],1)
        self.assertNotIn((CH,"msgs","1"),d.channel)
        self.assertEqual(d.links,{(CH,"u1"):(True,None)})

    def test_top_threshold(self):
        d = AD.batch_deltas([msg("a"),msg("b")],[
            res(1,o_total=499,n_total=500,o_status=-1,o_first=ts(2024,1),o_last=ts(2024,1),o_mod=False,o_fvid="v1"),
            res(2,o_total=9,n_total=10,o_status=-1,o_first=ts(2024,1),o_last=ts(2024,1),o_mod=False,o_fvid="v1"),
        ],{CH:400})
        self.assertEqual(d.top,{(CH,"a"):500})

class Community(unittest.TestCase):
    SCOPES = {"A":["group:EN/X","branch:EN","company"],"B":["group:EN/X","branch:EN","company"],
              "C":["group:EN/Y","branch:EN","company"],"O":["official"]}

    def test_first_channel(self):
        pairs,k,m = AD.community_deltas({},{"A":ts(2024,1)},self.SCOPES)
        self.assertEqual(pairs,Counter())
        self.assertEqual(k,Counter({("group:EN/X",1):1,("branch:EN",1):1,("company",1):1}))
        self.assertEqual(m[("company",date(2024,1,1))],1)

    def test_second_channel_same_group(self):
        pairs,k,m = AD.community_deltas({"A":ts(2024,1)},{"A":ts(2024,1),"B":ts(2024,3)},self.SCOPES)
        self.assertEqual(pairs,Counter({("A","B"):1}))
        self.assertEqual(k,Counter({("group:EN/X",1):-1,("group:EN/X",2):1,("branch:EN",1):-1,("branch:EN",2):1,
                                    ("company",1):-1,("company",2):1}))
        self.assertEqual(m,Counter())  # arrival month unchanged

    def test_new_channel_earlier_moves_scope_month(self):
        pairs,k,m = AD.community_deltas({"A":ts(2024,5)},{"A":ts(2024,5),"C":ts(2023,1)},self.SCOPES)
        self.assertEqual(m[("branch:EN",date(2024,5,1))],-1)
        self.assertEqual(m[("branch:EN",date(2023,1,1))],1)
        self.assertEqual(m[("group:EN/Y",date(2023,1,1))],1)
        self.assertNotIn(("group:EN/X",date(2024,5,1)),m)

    def test_first_moved_earlier_without_new_channel(self):
        pairs,k,m = AD.community_deltas({"A":ts(2024,5)},{"A":ts(2023,1)},self.SCOPES)
        self.assertEqual(pairs,Counter())
        self.assertEqual(k,Counter())
        self.assertEqual(m[("company",date(2024,5,1))],-1)
        self.assertEqual(m[("company",date(2023,1,1))],1)

    def test_two_new_channels_at_once(self):
        pairs,k,m = AD.community_deltas({"A":ts(2024,1)},{"A":ts(2024,1),"B":ts(2024,2),"O":ts(2024,2)},self.SCOPES)
        self.assertEqual(pairs,Counter({("A","B"):1,("A","O"):1,("B","O"):1}))
        self.assertEqual(k[("official",1)],1)

class Regulars(unittest.TestCase):
    def test_week_index(self):
        self.assertEqual(AD.week_index(date(2017,1,2)),0)
        self.assertEqual(AD.week_index(date(2017,1,8)),0)
        self.assertEqual(AD.week_index(date(2017,1,9)),1)
        self.assertIsNone(AD.week_index(None))
        self.assertIsNone(AD.week_index(date(2016,12,31)))
        self.assertEqual(AD.week_month(AD.week_index(date(2024,3,4))),date(2024,3,1))

    def test_four_weeks_is_regular_three_is_not(self):
        w = wk(2024,3,4)
        self.assertEqual(AD.regular_months(bits(w,w+1,w+2)),set())
        self.assertEqual(AD.regular_months(bits(w,w+1,w+2,w+3)),{date(2024,3,1)})

    def test_run_across_month_and_year(self):
        w = wk(2023,12,18)  # Mondays Dec 18, 25, Jan 1, 8
        self.assertEqual(AD.regular_months(bits(w,w+1,w+2,w+3)),{date(2023,12,1),date(2024,1,1)})
        self.assertEqual(AD.years_of(AD.regular_months(bits(w,w+1,w+2,w+3))),{2023,2024})

    def test_gap_breaks_run(self):
        w = wk(2024,3,4)
        self.assertEqual(AD.regular_months(bits(w,w+1,w+3,w+4)),set())

    def test_out_of_order_bit_completes_run(self):
        w = wk(2024,3,4)
        before = bits(w,w+1,w+3)
        after = bits(w,w+1,w+2,w+3)
        self.assertEqual(AD.regular_months(before),set())
        self.assertEqual(AD.regular_months(after),{date(2024,3,1)})

    def test_batch_regular_deltas(self):
        w = wk(2024,3,4)
        d = AD.batch_deltas([msg("u1")],[res(1,o_total=3,n_total=4,o_status=-1,o_first=ts(2024,3),o_last=ts(2024,3),
                                             n_first=ts(2024,3),n_last=ts(2024,3),o_mod=False,o_fvid="v1",
                                             o_weeks=bits(w,w+1,w+2),n_weeks=bits(w,w+1,w+2,w+3))])
        self.assertEqual(d.reg_month,Counter({(CH,date(2024,3,1),"regulars"):1}))   # active month already counted
        self.assertEqual(d.reg_year,Counter({(CH,2024,"regulars"):1}))
        self.assertEqual(d.channel[(CH,"regular","ever")],1)
        self.assertEqual(d.reg_changes,{(CH,"u1"):(frozenset(),frozenset({2024}))})

    def test_batch_new_week_new_month_active(self):
        d = AD.batch_deltas([msg("u1")],[res(1,inserted=True,o_weeks=None,n_weeks=bits(wk(2024,5,6)))])
        self.assertEqual(d.reg_month,Counter({(CH,date(2024,5,1),"active"):1}))
        self.assertEqual(d.reg_year,Counter({(CH,2024,"active"):1}))
        self.assertEqual(d.reg_changes,{})

    def test_unchanged_weeks_no_regular_deltas(self):
        d = AD.batch_deltas([msg("u1")],[res(1,o_total=3,n_total=4,o_status=-1,o_first=ts(2024,3),o_last=ts(2024,3),
                                             o_mod=False,o_fvid="v1")])
        self.assertEqual(d.reg_month,Counter())

    def test_regular_community(self):
        scopes = {"A":["group:EN/X","branch:EN","company"],"B":["group:EN/X","branch:EN","company"]}
        pairs,sc = AD.regular_community_deltas({"A":{2023}},{"A":{2023},"B":{2023,2024}},scopes)
        self.assertEqual(pairs,Counter({("A","B",2023):1,("A","B",0):1}))
        # 2023 was already a regular year for the group/branch/company; B's 2024 is new for all three
        self.assertEqual(sc,Counter({("group:EN/X",2024):1,("branch:EN",2024):1,("company",2024):1}))
        pairs,sc = AD.regular_community_deltas({"A":{2023}},{"A":{2023},"B":{2024}},scopes)
        self.assertEqual(pairs,Counter())  # no common year
        self.assertEqual(sc,Counter({("group:EN/X",2024):1,("branch:EN",2024):1,("company",2024):1}))

def M(y,m) -> date:
    return date(y,m,1)

class Retention(unittest.TestCase):
    def test_month_index_roundtrip(self):
        self.assertEqual(AD.month_index(M(2017,1)),0)
        self.assertEqual(AD.month_index(M(2018,2)),13)
        for i in (0,11,12,100,167):
            self.assertEqual(AD.month_index(AD.month_at(i)),i)

    def test_contrib(self):
        self.assertEqual(AD.retention_contrib(set()),set())
        self.assertEqual(AD.retention_contrib({M(2024,3),M(2024,4),M(2025,3)}),
                         {(M(2024,3),0),(M(2024,3),1),(M(2024,3),12)})
        # returning = also active the month before, across a year boundary too
        self.assertEqual(AD.mix_contrib({M(2023,12),M(2024,1),M(2024,3)}),{M(2024,1)})

    def test_first_month(self):
        ret,mix = AD.retention_deltas(set(),{M(2024,3)},"A")
        self.assertEqual(ret,Counter({("A",M(2024,3),0):1}))
        self.assertEqual(mix,Counter())

    def test_later_adjacent_month(self):
        ret,mix = AD.retention_deltas({M(2024,3)},{M(2024,3),M(2024,4)},"A")
        self.assertEqual(ret,Counter({("A",M(2024,3),1):1}))
        self.assertEqual(mix,Counter({(M(2024,4),"returning"):1}))

    def test_back_after_a_break(self):
        ret,mix = AD.retention_deltas({M(2024,3)},{M(2024,3),M(2024,6)},"R")
        self.assertEqual(ret,Counter({("R",M(2024,3),3):1}))
        self.assertEqual(mix,Counter())

    def test_out_of_order_month_moves_cohort(self):
        ret,mix = AD.retention_deltas({M(2024,3),M(2024,5)},{M(2024,2),M(2024,3),M(2024,5)},"A")
        self.assertEqual(ret,Counter({("A",M(2024,3),0):-1,("A",M(2024,3),2):-1,
                                      ("A",M(2024,2),0):1,("A",M(2024,2),1):1,("A",M(2024,2),3):1}))
        self.assertEqual(mix,Counter({(M(2024,3),"returning"):1}))

    def test_batch_deltas_retention_and_activity(self):
        w = wk(2024,3,4)
        d = AD.batch_deltas([msg("u1")],[res(1,o_total=3,n_total=4,o_status=-1,o_first=ts(2024,2),o_last=ts(2024,3),
                                             n_first=ts(2024,2),n_last=ts(2024,3),o_mod=False,o_fvid="v1",
                                             o_weeks=bits(w - 4,w,w+1,w+2),n_weeks=bits(w - 4,w,w+1,w+2,w+3))])
        # weeks: Feb 5 (Feb), Mar 4/11/18 -> + Mar 25 completes a 4-week run in March; active months unchanged
        self.assertEqual(d.retention,Counter({(CH,"R",M(2024,3),0):1}))
        self.assertEqual(d.activity,{(CH,"u1"):(frozenset(),frozenset({M(2024,3)}))})
        d = AD.batch_deltas([msg("u1"),msg("u1")],[res(1,inserted=True,n_weeks=bits(wk(2024,5,6))),
                                                  res(2,o_total=1,n_total=2,o_status=-1,o_first=ts(2024,5),o_last=ts(2024,5),
                                                      o_mod=False,o_fvid="v1",o_weeks=bits(wk(2024,5,6)),
                                                      n_weeks=bits(wk(2024,5,6),wk(2024,6,3)))])
        self.assertEqual(d.retention,Counter({(CH,"A",M(2024,5),0):1,(CH,"A",M(2024,5),1):1}))
        self.assertEqual(d.reg_month[(CH,M(2024,6),"returning")],1)
        self.assertEqual(d.activity,{(CH,"u1"):(frozenset({M(2024,5),M(2024,6)}),frozenset())})

    SCOPES = {"A":["group:EN/X","branch:EN","company"],"B":["group:EN/X","branch:EN","company"],
              "C":["group:EN/Y","branch:EN","company"],"O":["official"]}

    def test_scope_month_already_active_elsewhere(self):
        # B becomes active in March, but A already was: no change for any shared scope
        before = {"A":({M(2024,3)},set())}
        after = {"A":({M(2024,3)},set()),"B":({M(2024,3)},set())}
        self.assertEqual(AD.scope_retention_deltas(before,after,self.SCOPES),(Counter(),Counter()))

    def test_scope_new_month_only_in_touched_scopes(self):
        before = {"A":({M(2024,3)},set())}
        after = {"A":({M(2024,3)},set()),"C":({M(2024,4)},set())}
        ret,mix = AD.scope_retention_deltas(before,after,self.SCOPES)
        self.assertEqual(ret,Counter({("group:EN/Y","A",M(2024,4),0):1,("branch:EN","A",M(2024,3),1):1,
                                      ("company","A",M(2024,3),1):1}))
        self.assertEqual(mix,Counter({("branch:EN",M(2024,4),"returning"):1,("company",M(2024,4),"returning"):1}))

    def test_incremental_matches_full_recompute(self):
        """Random growing month sets: summed per-step deltas == the final set's full contribution."""
        import random
        rng = random.Random(7)
        months = [AD.month_at(i) for i in range(84,120)]
        chans = list(self.SCOPES)
        for _ in range(300):
            state = {ch:(set(),set()) for ch in chans}
            got_c,got_s,got_sm = Counter(),Counter(),Counter()
            for _ in range(rng.randint(1,12)):
                ch = rng.choice(chans)
                before = {c:(set(a),set(r)) for c,(a,r) in state.items()}
                add_a = set(rng.sample(months,rng.randint(1,3)))
                add_r = set(rng.sample(sorted(state[ch][0] | add_a),1)) if rng.random() < 0.5 else set()
                state[ch][0].update(add_a); state[ch][1].update(add_r)
                for k,kind in enumerate(AD.RETENTION_KINDS):
                    r,m = AD.retention_deltas(before[ch][k],state[ch][k],kind)
                    for key,v in r.items(): got_c[(ch,) + key] += v
                    for key,v in m.items(): got_c[(ch,"mix") + key] += v
                r,m = AD.scope_retention_deltas(before,{c:(set(a),set(rr)) for c,(a,rr) in state.items()},self.SCOPES)
                got_s.update(r); got_sm.update(m)
            want_c,want_s,want_sm = Counter(),Counter(),Counter()
            for ch,sets in state.items():
                for k,kind in enumerate(AD.RETENTION_KINDS):
                    for c,a in AD.retention_contrib(sets[k]): want_c[(ch,kind,c,a)] += 1
                    for mo in AD.mix_contrib(sets[k]): want_c[(ch,"mix",mo,AD.MIX_COLUMN[kind])] += 1
            for s in {s for ss in self.SCOPES.values() for s in ss}:
                for k,kind in enumerate(AD.RETENTION_KINDS):
                    u = set().union(*(state[ch][k] for ch in chans if s in self.SCOPES[ch]))
                    for c,a in AD.retention_contrib(u): want_s[(s,kind,c,a)] += 1
                    for mo in AD.mix_contrib(u): want_sm[(s,mo,AD.MIX_COLUMN[kind])] += 1
            for c in (got_c,got_s,got_sm):
                for k in [k for k,v in c.items() if v == 0]: del c[k]
            self.assertEqual(got_c,want_c)
            self.assertEqual(got_s,want_s)
            self.assertEqual(got_sm,want_sm)

class VectorisedMatchesReference(unittest.TestCase):
    def test_regular_matrices_match_regular_months(self):
        import random
        rng = random.Random(42)
        rows = []
        for _ in range(2000):
            density = rng.choice([0.02, 0.1, 0.3, 0.6])
            rows.append("".join("1" if rng.random() < density else "0" for _ in range(AD.WEEK_BITS)))
        rows += [None, "", bits(), bits(0, 1, 2, 3), bits(AD.WEEK_BITS - 4, AD.WEEK_BITS - 3, AD.WEEK_BITS - 2, AD.WEEK_BITS - 1)]
        reg_m, act_m = AD.regular_matrices(rows)
        for i, r in enumerate(rows):
            want_reg = AD.regular_months(r if r and len(r) == AD.WEEK_BITS else None)
            want_act = AD.active_months(r if r and len(r) == AD.WEEK_BITS else None)
            got_reg = {AD.MONTHS[j] for j in range(len(AD.MONTHS)) if reg_m[i, j]}
            got_act = {AD.MONTHS[j] for j in range(len(AD.MONTHS)) if act_m[i, j]}
            self.assertEqual(got_reg, want_reg, i)
            self.assertEqual(got_act, want_act, i)

if __name__ == "__main__":
    unittest.main()
