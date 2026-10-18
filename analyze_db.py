"""
File 2 of 2: analyzes company.db and compares the company to national benchmarks.

Run:   python analyze_db.py            -> readable text report
       python analyze_db.py --json     -> the same results as JSON (for charts)
Import: from analyze_db import analyze; data = analyze()   (use in a FastAPI endpoint)

Reports gaps honestly; it never alters data (read-only connection).
Every number in the result dict is chart-ready: lists of flat records, no prose inside the data.
"""
import sqlite3, sys, json, statistics as st
from collections import defaultdict

DB = "company.db"
MIN_GROUP = 5  # small-cell suppression: groups smaller than this are never reported

# ---- National benchmarks: PLACEHOLDERS. Replace with current BLS / Census / EEOC figures. ----
BENCHMARKS = {
    "avg_annual_pay": 59000,
    "women_to_men_pay_ratio": 0.83,
    "pct_women": 0.47,
    "pct_women_in_management": 0.41,
    "pct_disability": 0.08,
}

# Optional columns in v_pay. If present, a gap table is built for each. Rename to match your schema.
OPTIONAL_DIMENSIONS = ["department", "team", "ethnicity", "race"]

SKIP_GROUPS = {"", "undisclosed", "unknown", "none"}

mean = lambda xs: sum(xs) / len(xs)
pct = lambda a, b: (a - b) / b * 100 if b else None


def severity(gap_pct):
    """gap_pct is negative when women are paid less."""
    if gap_pct <= -8: return "high"
    if gap_pct <= -4: return "medium"
    if gap_pct < 0:   return "low"
    return "none"


def gap_table(rows, keyfn):
    """Women vs men average pay per group. Groups where either side is < MIN_GROUP are suppressed."""
    cells = defaultdict(lambda: {"Female": [], "Male": []})
    for r in rows:
        if r["gender"] in ("Female", "Male"):
            cells[keyfn(r)][r["gender"]].append(r["annual_salary"])
    out, hidden = [], 0
    for key, v in cells.items():
        f, m = v["Female"], v["Male"]
        if len(f) < MIN_GROUP or len(m) < MIN_GROUP:
            hidden += 1
            continue
        gap = pct(mean(f), mean(m))
        target = st.median(m)
        out.append({
            "group": key, "n_women": len(f), "n_men": len(m),
            "avg_women": round(mean(f)), "avg_men": round(mean(m)),
            "gap_pct": round(gap, 1), "severity": severity(gap),
            "people_below_target": sum(1 for s in f if s < target),
            "cost_to_close": round(max(0, mean(m) - mean(f)) * len(f)),  # raise women's average to men's average
        })
    out.sort(key=lambda x: x["gap_pct"])  # worst first
    return out, hidden


def _has(con, table, col):
    try:
        return col in [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return False


def attach_attrs(con, rows):
    """Adds ethnicity and has_disability to each row when v_pay does not already carry them."""
    if not rows:
        return rows
    have = set(rows[0].keys())
    for table, col in (("employees", "ethnicity"), ("disability_status", "has_disability")):
        if col in have or not _has(con, table, col):
            continue
        keys = [k for k in have
                if (k.lower() in ("id", "employee_id", "emp_id") or k.lower().endswith("_id")) and _has(con, table, k)]
        if not keys:
            continue
        k = keys[0]
        lookup = {r[0]: r[1] for r in con.execute(f"SELECT {k}, {col} FROM {table}")}
        for r in rows:
            r[col] = lookup.get(r[k])
        have.add(col)
    return rows


def group_vs_rest(rows, flag):
    """One group against everyone else. Uses same-role cells when big enough, then same-level, then overall."""
    g = [r["annual_salary"] for r in rows if flag(r)]
    o = [r["annual_salary"] for r in rows if not flag(r)]
    if len(g) < MIN_GROUP or len(o) < MIN_GROUP:
        return None
    raw = pct(mean(g), mean(o))
    adj, basis = None, "overall"
    for name, kf in (("same role", lambda r: f'{r["title"]} ({r["level"]})'), ("same level", lambda r: r["level"])):
        cells = defaultdict(lambda: ([], []))
        for r in rows:
            cells[kf(r)][0 if flag(r) else 1].append(r["annual_salary"])
        use = [(a, b) for a, b in cells.values() if len(a) >= MIN_GROUP and len(b) >= MIN_GROUP]
        n = sum(len(a) for a, b in use)
        if n:
            adj = sum(pct(mean(a), mean(b)) * len(a) for a, b in use) / n
            basis = name
            break
    gap = raw if adj is None else adj
    qs = st.quantiles(sorted(g), n=4)
    return {
        "n": len(g), "n_others": len(o), "avg_pay": round(mean(g)), "avg_others": round(mean(o)),
        "raw_gap_pct": round(raw, 1), "gap_pct": round(gap, 1), "basis": basis, "severity": severity(gap),
        "people_below_target": sum(1 for s in g if s < st.median(o)),
        "cost_to_close": round(max(0, -gap / 100 * mean(o)) * len(g)),
        "spread": {"min": min(g), "p25": round(qs[0]), "median": round(qs[1]), "p75": round(qs[2]), "max": max(g)},
    }


def group_analysis(rows, level_order):
    """Ethnicity and disability gaps. Returns (detail by dimension, flat list for summary charts)."""
    specs = {}
    if rows and "ethnicity" in rows[0]:
        names = sorted({r["ethnicity"] for r in rows
                        if r.get("ethnicity") and str(r["ethnicity"]).strip().lower() not in SKIP_GROUPS})
        if names:
            specs["ethnicity"] = [(n, (lambda r, n=n: r.get("ethnicity") == n)) for n in names]
    if rows and "has_disability" in rows[0]:
        specs["disability"] = [("People with disabilities", lambda r: bool(r.get("has_disability")))]
    out, flat = {}, []
    for dim, items in specs.items():
        res, hidden = [], 0
        for name, fl in items:
            g = group_vs_rest(rows, fl)
            if not g:
                hidden += 1
                continue
            g.update(group=name, dimension=dim, by_level=[], rep_by_level=[])
            for l in level_order:
                sub = [r for r in rows if r["level"] == l]
                x = group_vs_rest(sub, fl)
                if x:
                    g["by_level"].append({"level": l, "n": x["n"], "gap_pct": x["raw_gap_pct"]})
                cnt = sum(1 for r in sub if fl(r))
                if len(sub) >= MIN_GROUP and cnt >= MIN_GROUP:
                    g["rep_by_level"].append({"level": l, "n": len(sub), "pct": round(cnt / len(sub), 3)})
            res.append(g)
        res.sort(key=lambda c: c["gap_pct"])
        out[dim] = {"available": True, "groups": res, "hidden": hidden}
        flat += [{k: c[k] for k in ("dimension", "group", "n", "gap_pct", "severity")} for c in res]
    for d in ("ethnicity", "disability"):
        out.setdefault(d, {"available": False, "groups": [], "hidden": 0})
    return out, flat


def groups_from_records(records):
    """Group gaps straight from the parsed upload, for when the database has no ethnicity column."""
    rows = [{"annual_salary": r["salary"], "title": r["position"], "level": r["level"], "gender": r["gender"],
             "ethnicity": r.get("ethnicity"), "has_disability": r.get("disability")} for r in records]
    lp = defaultdict(list)
    for r in rows:
        lp[r["level"]].append(r["annual_salary"])
    return group_analysis(rows, sorted(lp, key=lambda l: mean(lp[l])))


def analyze(db=DB):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # read-only
    con.row_factory = sqlite3.Row
    q = lambda s, a=(): con.execute(s, a).fetchall()

    rows = [dict(r) for r in q("SELECT * FROM v_pay")]
    rows = attach_attrs(con, rows)
    cols = set(rows[0].keys()) if rows else set()
    dims = [d for d in OPTIONAL_DIMENSIONS if d in cols]

    total = q("SELECT COUNT(*) FROM employees")[0][0]
    pays = [r["annual_salary"] for r in rows]
    avg, payroll_total = mean(pays), sum(pays)

    # ---- pay by gender ----
    by_g, gender_rows = {}, []
    for g, n, a in q("SELECT gender, COUNT(*), AVG(annual_salary) FROM v_pay GROUP BY gender"):
        if n < MIN_GROUP:
            continue
        by_g[g] = a
        gender_rows.append({"gender": g, "n": n, "avg_pay": round(a)})

    # ---- levels ordered by average pay (no need to know your level names) ----
    lvl_pay = defaultdict(list)
    for r in rows: lvl_pay[r["level"]].append(r["annual_salary"])
    level_order = sorted(lvl_pay, key=lambda l: mean(lvl_pay[l]))

    # ---- gap tables ----
    by_level, _ = gap_table(rows, lambda r: r["level"])
    by_level.sort(key=lambda x: level_order.index(x["group"]))
    by_position, hidden_pos = gap_table(rows, lambda r: f'{r["title"]} ({r["level"]})')
    by_dim = {d: gap_table(rows, lambda r, d=d: r[d])[0] for d in dims}

    # ---- raw gap vs same-level ("adjusted") gap ----
    raw_gap = pct(by_g["Female"], by_g["Male"]) if "Female" in by_g and "Male" in by_g else None
    wn = sum(c["n_women"] for c in by_position)
    adj_gap = sum(c["gap_pct"] * c["n_women"] for c in by_position) / wn if wn else None

    # ---- representation by level (pipeline) ----
    representation = []
    for l in level_order:
        grp = [r for r in rows if r["level"] == l]
        if len(grp) >= MIN_GROUP:
            representation.append({"level": l, "n": len(grp),
                                   "pct_women": round(sum(r["gender"] == "Female" for r in grp) / len(grp), 3)})

    # ---- pay spread by level ----
    spread = []
    for l in level_order:
        v = sorted(lvl_pay[l])
        if len(v) >= MIN_GROUP:
            qs = st.quantiles(v, n=4)
            spread.append({"level": l, "n": len(v), "min": v[0], "p25": round(qs[0]),
                           "median": round(qs[1]), "p75": round(qs[2]), "max": v[-1]})

    # ---- benchmarks ----
    pct_f = q("SELECT AVG(gender='Female') FROM employees")[0][0]
    pct_d = q("SELECT AVG(has_disability) FROM disability_status")[0][0]
    pct_fm = q("SELECT AVG(gender='Female') FROM v_pay WHERE level IN ('Management','Executive')")[0][0]
    ratio = by_g["Female"] / by_g["Male"] if "Female" in by_g and "Male" in by_g else None
    B = BENCHMARKS
    bench = []
    for key, label, ours, kind in [
        ("avg_annual_pay", "Average annual pay", avg, "$"),
        ("women_to_men_pay_ratio", "Women/men pay ratio", ratio, "ratio"),
        ("pct_women", "% women in workforce", pct_f, "%"),
        ("pct_women_in_management", "% women in management+", pct_fm, "%"),
        ("pct_disability", "% employees with a disability", pct_d, "%")]:
        if ours is None:
            continue
        nat = B[key]
        shortfall = (ours - nat) / nat * 100
        bench.append({"key": key, "label": label, "kind": kind, "company": round(ours, 4),
                      "national": nat, "diff_pct": round(shortfall, 1), "below": ours < nat})
    bench.sort(key=lambda b: b["diff_pct"])  # furthest below national first

    # ---- positions paying under the national average (rough: national avg covers all jobs) ----
    pos_pay = defaultdict(list)
    for r in rows: pos_pay[f'{r["title"]} ({r["level"]})'].append(r["annual_salary"])
    below_nat_pay = sorted(
        [{"position": p, "n": len(v), "avg_pay": round(mean(v)),
          "below_national_pct": round((mean(v) - B["avg_annual_pay"]) / B["avg_annual_pay"] * 100, 1)}
         for p, v in pos_pay.items() if len(v) >= MIN_GROUP and mean(v) < B["avg_annual_pay"]],
        key=lambda x: x["below_national_pct"])

    # ---- worst areas: one ranked list across everything ----
    worst = []
    for b in bench:
        if b["below"]:
            worst.append({"type": "benchmark", "label": b["label"], "metric": b["diff_pct"],
                          "detail": "vs national", "severity": severity(b["diff_pct"] / 2)})
    for c in by_position[:5]:
        if c["gap_pct"] < 0:
            worst.append({"type": "position_gap", "label": c["group"], "metric": c["gap_pct"],
                          "detail": "women vs men, same role", "severity": c["severity"]})
    for d, tbl in by_dim.items():
        for c in tbl[:3]:
            if c["gap_pct"] < 0:
                worst.append({"type": f"{d}_gap", "label": c["group"], "metric": c["gap_pct"],
                              "detail": f"women vs men within {d}", "severity": c["severity"]})
    worst.sort(key=lambda w: w["metric"])

    # ---- suggestions (rule-based, each tied to a finding) ----
    sug = []
    def add(priority, area, text): sug.append({"priority": priority, "area": area, "text": text})
    for c in by_position:
        if c["severity"] in ("high", "medium"):
            add(c["severity"], c["group"],
                f'Women earn {abs(c["gap_pct"])}% less than men in this role. Review pay for '
                f'{c["people_below_target"]} women below the men\'s median; closing it costs about ${c["cost_to_close"]:,}/yr.')
    for d, tbl in by_dim.items():
        for c in tbl[:2]:
            if c["severity"] in ("high", "medium"):
                add(c["severity"], f'{d}: {c["group"]}',
                    f'Women earn {abs(c["gap_pct"])}% less than men here. Audit pay bands and starting offers.')
    for b in bench:
        if b["below"] and b["key"] == "pct_women_in_management":
            add("medium", "Leadership pipeline",
                "Women are under-represented in management compared with the national figure. "
                "Track promotion rates by gender and set a sponsorship or succession goal.")
        if b["below"] and b["key"] == "pct_disability":
            add("low", "Disability inclusion",
                "Disability representation is under the national figure. Review job-posting reach, "
                "accommodations process, and make self-identification voluntary and well explained.")
        if b["below"] and b["key"] == "avg_annual_pay":
            add("low", "Overall pay level", "Average pay is under the national average. Benchmark each role against market data.")
    if raw_gap is not None and adj_gap is not None and raw_gap < adj_gap - 2:
        add("medium", "Level mix",
            f"The overall gap ({abs(raw_gap):.1f}%) is bigger than the same-level gap ({abs(adj_gap):.1f}%). "
            "Most of it comes from fewer women in higher-paid levels, so focus on promotion and hiring at senior levels.")
    order = {"high": 0, "medium": 1, "low": 2}
    sug.sort(key=lambda s: order[s["priority"]])

    groups, group_gaps = group_analysis(rows, level_order)

    con.close()
    total_cost = sum(c["cost_to_close"] for c in by_position)
    return {
        "summary": {"headcount": total, "payroll_total": round(payroll_total), "avg_pay": round(avg),
                    "raw_gap_pct": None if raw_gap is None else round(raw_gap, 1),
                    "adjusted_gap_pct": None if adj_gap is None else round(adj_gap, 1),
                    "total_cost_to_close": total_cost, "min_group": MIN_GROUP,
                    "positions_hidden_for_privacy": hidden_pos},
        "pay_by_gender": gender_rows,
        "gap_by_level": by_level,
        "gap_by_position": by_position,
        "gap_by_dimension": by_dim,
        "representation_by_level": representation,
        "pay_spread_by_level": spread,
        "benchmarks": bench,
        "positions_below_national_pay": below_nat_pay,
        "worst_areas": worst,
        "suggestions": sug,
        "groups": groups,
        "group_gaps": group_gaps,
    }


FMT = {"$": lambda x: f"${x:,.0f}", "%": lambda x: f"{x:.1%}", "ratio": lambda x: f"{x:.2f}"}

def print_report(d):
    s = d["summary"]
    print(f"Headcount: {s['headcount']} | Total payroll: ${s['payroll_total']:,} | Avg pay: ${s['avg_pay']:,}")
    if s["raw_gap_pct"] is not None:
        print(f"Women vs men: overall gap {s['raw_gap_pct']}% | same-level gap {s['adjusted_gap_pct']}% "
              f"| est. cost to close ${s['total_cost_to_close']:,}/yr")
    print(f"(groups under {s['min_group']} are suppressed)")

    print("\nCompany vs. national benchmark:")
    for b in d["benchmarks"]:
        f = FMT[b["kind"]]
        flag = "BELOW national - review" if b["below"] else "meets/exceeds national"
        print(f"  {b['label']:<30} company {f(b['company']):>9} | national {f(b['national']):>9} ({b['diff_pct']:+.1f}%) | {flag}")

    print("\nPay gap by level (women vs men):")
    for c in d["gap_by_level"]:
        print(f"  {c['group']:<12} {c['gap_pct']:>6}%  women n={c['n_women']}, men n={c['n_men']}")

    print("\nWorst same-role gaps:")
    for c in d["gap_by_position"][:5]:
        print(f"  {c['group']:<32} {c['gap_pct']:>6}%  [{c['severity']}]  fix ~${c['cost_to_close']:,}/yr")

    for dim, tbl in d["gap_by_dimension"].items():
        print(f"\nGap by {dim}:")
        for c in tbl:
            print(f"  {str(c['group']):<20} {c['gap_pct']:>6}%  [{c['severity']}]")

    for dim, g in d.get("groups", {}).items():
        if not g["available"]:
            continue
        print(f"\n{dim.capitalize()} pay gap (each group vs everyone else):")
        for c in g["groups"]:
            print(f"  {c['group']:<24} {c['gap_pct']:>6}%  [{c['severity']}]  n={c['n']}  ({c['basis']})")

    print("\n% women by level:")
    for r in d["representation_by_level"]:
        print(f"  {r['level']:<12} {r['pct_women']:.0%}  (n={r['n']})")

    if d["positions_below_national_pay"]:
        print("\nPositions paying under the national average (rough comparison):")
        for p in d["positions_below_national_pay"][:5]:
            print(f"  {p['position']:<32} ${p['avg_pay']:,} ({p['below_national_pct']}%)")

    print("\nSuggested actions:")
    for x in d["suggestions"]:
        print(f"  [{x['priority'].upper()}] {x['area']}: {x['text']}")
    print("\nNote: averages show where to look, not why. Check role, tenure and performance before changing pay.")


def main(db=DB):
    """Kept for backward compatibility with the original script."""
    print_report(analyze(db))


def report_text(db=DB):
    """The text report as a string, for an API endpoint to return."""
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_report(analyze(db))
    return buf.getvalue()


if __name__ == "__main__":
    data = analyze()
    print(json.dumps(data, indent=2)) if "--json" in sys.argv else print_report(data)