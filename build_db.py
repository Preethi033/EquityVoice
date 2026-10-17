"""
File 1 of 2: turns a JSON file into a secure SQLite database (company.db) plus a portable SQL dump (company.sql).
Run: python build_db.py [employees.json]
Expected JSON fields per employee: id, gender, age, position, level, salary, disability
"""
import json, os, sqlite3, sys, hashlib, stat

DB, DUMP = "company.db", "company.sql"

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE positions(
  position_id INTEGER PRIMARY KEY,
  title TEXT NOT NULL UNIQUE,
  level TEXT NOT NULL CHECK(level IN ('Entry','Mid','Senior','Management','Executive'))
);
CREATE TABLE employees(
  employee_id TEXT PRIMARY KEY,
  gender TEXT NOT NULL CHECK(gender IN ('Female','Male','Nonbinary','Undisclosed')),
  age INTEGER NOT NULL CHECK(age BETWEEN 16 AND 100),
  position_id INTEGER NOT NULL REFERENCES positions(position_id)
);
CREATE TABLE payroll(
  payroll_id INTEGER PRIMARY KEY,
  employee_id TEXT NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
  annual_salary REAL NOT NULL CHECK(annual_salary > 0),
  pay_period TEXT NOT NULL
);
CREATE TABLE disability_status(
  employee_id TEXT PRIMARY KEY REFERENCES employees(employee_id) ON DELETE CASCADE,
  has_disability INTEGER NOT NULL CHECK(has_disability IN (0,1))
);
CREATE TABLE audit_log(ts TEXT DEFAULT CURRENT_TIMESTAMP, action TEXT);
CREATE VIEW v_pay AS
  SELECT e.employee_id, e.gender, e.age, p.title, p.level, pr.annual_salary
  FROM employees e JOIN positions p USING(position_id) JOIN payroll pr USING(employee_id);
"""

def main(json_path):
    data = json.load(open(json_path))
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    salt = os.environ.get("ID_SALT", "change-me")  # set ID_SALT in your environment, don't hardcode
    for r in data:
        # parameterized queries only -> no SQL injection
        con.execute("INSERT OR IGNORE INTO positions(title,level) VALUES(?,?)", (r["position"], r["level"]))
        pid = con.execute("SELECT position_id FROM positions WHERE title=?", (r["position"],)).fetchone()[0]
        eid = hashlib.sha256((salt + str(r["id"])).encode()).hexdigest()[:12]  # pseudonymous ID, no names stored
        con.execute("INSERT INTO employees VALUES(?,?,?,?)", (eid, r["gender"], int(r["age"]), pid))
        con.execute("INSERT INTO payroll(employee_id,annual_salary,pay_period) VALUES(?,?,?)",
                    (eid, float(r["salary"]), "annual"))
        con.execute("INSERT INTO disability_status VALUES(?,?)", (eid, int(bool(r["disability"]))))
    con.execute("INSERT INTO audit_log(action) VALUES('initial load')")
    con.commit()
    with open(DUMP, "w") as f:
        f.write("\n".join(con.iterdump()))
    con.close()
    for p in (DB, DUMP):
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # owner-only permissions
    print(f"Built {DB} and {DUMP} from {len(data)} employees.")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample_employees.json")