"""
File 2 of 2: analyzes company.db and compares the company to national benchmarks.
Run: python analyze_db.py
Reports gaps honestly; it never alters data.
"""
import sqlite3, sys

DB = "company.db"
MIN_GROUP = 5  # small-cell suppression: groups smaller than this are not reported

# ---- National benchmarks: PLACEHOLDERS. Replace with current BLS / Census / EEOC figures. ----
BENCHMARKS = {
    "avg_annual_pay": 59000,
    "women_to_men_pay_ratio": 0.83,
    "pct_women": 0.47,
    "pct_women_in_management": 0.41,
    "pct_disability": 0.08,
}

def main():
    # read-only connection: analysis cannot modify data
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = lambda s, a=(): con.execute(s, a).fetchall()

    total = q("SELECT COUNT(*) FROM employees")[0][0]
    avg = q("SELECT AVG(annual_salary) FROM payroll")[0][0]
    payroll_total = q("SELECT SUM(annual_salary) FROM payroll")[0][0]
    print(f"Headcount: {total} | Total payroll: ${payroll_total:,.0f} | Avg pay: ${avg:,.0f}")

    print(f"\nAverage pay by gender (groups < {MIN_GROUP} suppressed):")
    by_g = {}
    for g, n, a in q("SELECT gender, COUNT(*), AVG(annual_salary) FROM v_pay GROUP BY gender"):
        if n < MIN_GROUP:
            print(f"  {g}: suppressed (n={n})")
            continue
        by_g[g] = a
        print(f"  {g}: n={n}, avg ${a:,.0f}")

    print("\nAverage pay by position:")
    for t, lvl, n, a in q("SELECT title, level, COUNT(*), AVG(annual_salary) FROM v_pay "
                          "GROUP BY title, level HAVING COUNT(*) >= ? ORDER BY AVG(annual_salary)", (MIN_GROUP,)):
        print(f"  {t:<18}{lvl:<11} n={n:<3} ${a:,.0f}")

    print("\nPay by level and gender (same-level comparison is the fairer test):")
    for lvl, g, n, a in q("SELECT level, gender, COUNT(*), AVG(annual_salary) FROM v_pay "
                          "GROUP BY level, gender HAVING COUNT(*) >= ? ORDER BY level, gender", (MIN_GROUP,)):
        print(f"  {lvl:<11}{g:<8} n={n:<3} ${a:,.0f}")

    pct_f = q("SELECT AVG(gender='Female') FROM employees")[0][0]
    pct_d = q("SELECT AVG(has_disability) FROM disability_status")[0][0]
    pct_fm = q("SELECT AVG(gender='Female') FROM v_pay WHERE level IN ('Management','Executive')")[0][0]
    ratio = by_g["Female"] / by_g["Male"] if "Female" in by_g and "Male" in by_g else None

    B = BENCHMARKS
    print("\nCompany vs. national benchmark:")
    rows = [("Average annual pay", avg, B["avg_annual_pay"], "$"),
            ("Women/men pay ratio", ratio, B["women_to_men_pay_ratio"], "r"),
            ("% women in workforce", pct_f, B["pct_women"], "%"),
            ("% women in management+", pct_fm, B["pct_women_in_management"], "%"),
            ("% employees w/ disability", pct_d, B["pct_disability"], "%")]
    fmt = {"$": lambda x: f"${x:,.0f}", "%": lambda x: f"{x:.1%}", "r": lambda x: f"{x:.2f}"}
    for name, ours, nat, kind in rows:
        if ours is None:
            continue
        status = "meets/exceeds national" if ours >= nat else "BELOW national - review"
        print(f"  {name:<28} company {fmt[kind](ours):>9} | national {fmt[kind](nat):>9} | {status}")
    con.close()

if __name__ == "__main__":
    main()