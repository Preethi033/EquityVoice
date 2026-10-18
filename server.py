import json, os, re, tempfile, uuid
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

import analyze_db
import build_db

if not os.environ.get("ID_SALT"):
    raise RuntimeError("Set ID_SALT before starting the server.")

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
client = genai.Client()  # reads GEMINI_API_KEY from the environment

PERIODS = {"annual": 1, "monthly": 12, "semimonthly": 24, "biweekly": 26, "weekly": 52, "hourly": 2080}
LEVELS = ["Entry", "Mid", "Senior", "Management", "Executive"]
GENDERS = ["Female", "Male", "Nonbinary", "Undisclosed"]
MAX_BYTES = 10_000_000


class Columns(BaseModel):
    id: str | None
    gender: str | None
    ethnicity: str | None
    age: str | None
    dob: str | None
    position: str | None
    level: str | None
    salary: str | None
    disability: str | None


class Mapping(BaseModel):
    header_row: int
    columns: Columns
    salary_period: str


class Pair(BaseModel):
    raw: str
    value: str


class Norm(BaseModel):
    gender_map: list[Pair]
    disability_map: list[Pair]
    level_map: list[Pair]
    ethnicity_map: list[Pair]


class Employee(BaseModel):
    id: str
    gender: Literal["Female", "Male", "Nonbinary", "Undisclosed"]
    ethnicity: str | None = None
    age: int = Field(ge=16, le=100)
    position: str
    level: Literal["Entry", "Mid", "Senior", "Management", "Executive"]
    salary: float = Field(gt=0)
    disability: bool


def ask(schema, prompt):
    r = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
        ),
    )
    return r.parsed or schema.model_validate_json(r.text)


def load_raw(data, name):
    if name.endswith((".xlsx", ".xlsm")):
        sheets = pd.read_excel(BytesIO(data), sheet_name=None, header=None, dtype=str)
        df = max(sheets.values(), key=lambda d: d.size)
    else:
        df = pd.read_csv(BytesIO(data), header=None, dtype=str, sep=None, engine="python")
    return df.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)


def get_mapping(raw):
    sample = raw.head(15).to_csv(header=False)
    prompt = (
        "Below are the first rows of a payroll file. The first value on each line is the 0-based row index. "
        "Map the file to these target fields: id, gender, age (or dob if there is no age), "
        "position (job title), level (seniority, only if a column literally holds it), salary, disability, "
        "ethnicity (race or ethnicity). "
        "For each field give the exact header text of the column, or null if the file has no such column. "
        "header_row is the row index of the header. salary_period must be one of: "
        + ", ".join(PERIODS) + ".\n\n" + sample
    )
    m = ask(Mapping, prompt).model_dump()
    if m["salary_period"] not in PERIODS:
        raise ValueError("Could not tell what pay period the salaries cover.")
    return m


def extract(raw, m):
    h = m["header_row"]
    df = raw.iloc[h + 1:].copy()
    df.columns = raw.iloc[h].astype(str).str.strip()
    cols = {k: v for k, v in m["columns"].items() if v}
    missing = [v for v in cols.values() if v not in df.columns]
    if missing:
        raise ValueError(f"Mapped columns not found in file: {missing}")
    return pd.DataFrame({k: df[v].values for k, v in cols.items()})


def get_norm(t, m):
    def uniq(c):
        return sorted(t[c].dropna().astype(str).str.strip().unique().tolist())[:200] if c in t else []

    level_src = "level" if m["columns"]["level"] else "position"
    payload = {
        "gender_values": uniq("gender"),
        "disability_values": uniq("disability"),
        "level_source_values": uniq(level_src),
        "ethnicity_values": uniq("ethnicity"),
    }
    prompt = (
        "Normalize these raw values. gender_map: each raw gender value -> one of "
        + ", ".join(GENDERS) + ". disability_map: each raw value -> the string true or false. "
        "level_map: each raw level_source value -> one of " + ", ".join(LEVELS)
        + " (infer from the job title if needed). "
        "ethnicity_map: each raw ethnicity value -> a short consistent label such as Asian, Black, "
        "Hispanic/Latino, White, Two or more, Other or Undisclosed. "
        "Return one pair per raw value.\n\n" + json.dumps(payload)
    )
    n = ask(Norm, prompt)
    return {
        "gender_map": {p.raw: p.value for p in n.gender_map},
        "disability_map": {p.raw: p.value.strip().lower() == "true" for p in n.disability_map},
        "level_map": {p.raw: p.value for p in n.level_map},
        "ethnicity_map": {p.raw: p.value for p in n.ethnicity_map},
    }


def num(v):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(v)))
    except ValueError:
        return None


def build_records(t, m, n):
    factor = PERIODS[m["salary_period"]]
    today = date.today()
    ok, bad = [], []
    for i, row in t.iterrows():
        try:
            a = num(row.get("age"))
            if a is not None:
                age = int(a)
            else:
                d = pd.to_datetime(row.get("dob"), errors="coerce")
                age = None if pd.isna(d) else today.year - d.year - ((today.month, today.day) < (d.month, d.day))
            src = row.get("level") if m["columns"]["level"] else row.get("position")
            salary = num(row.get("salary"))
            rec = Employee(
                id=str(row.get("id")).strip(),
                gender=n["gender_map"].get(str(row.get("gender", "")).strip(), "Undisclosed"),
                ethnicity=(n["ethnicity_map"].get(str(row.get("ethnicity", "")).strip(), "Undisclosed")
                           if m["columns"]["ethnicity"] else None),
                age=age,
                position=str(row.get("position")).strip(),
                level=n["level_map"].get(str(src).strip()),
                salary=None if salary is None else round(salary * factor, 2),
                disability=n["disability_map"].get(str(row.get("disability", "")).strip(), False),
            )
            ok.append(rec.model_dump())
        except (ValidationError, TypeError, ValueError) as e:
            bad.append({"row": int(i), "error": str(e)[:200]})
    return ok, bad


app = FastAPI()
PENDING = {}
RECORDS = Path(__file__).parent / "records.json"  # last confirmed upload, ids removed


@app.post("/upload")
def upload(file: UploadFile = File(...)):
    name = (file.filename or "").lower()
    if not name.endswith((".csv", ".xlsx", ".xlsm")):
        raise HTTPException(400, "Upload a .csv or .xlsx file.")
    data = file.file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is too large (10 MB max).")
    try:
        raw = load_raw(data, name)
        mapping = get_mapping(raw)
        table = extract(raw, mapping)
        norm = get_norm(table, mapping)
        records, errors = build_records(table, mapping, norm)
    except Exception as e:
        raise HTTPException(422, f"Could not read this file: {str(e)[:200]}")
    total = len(records) + len(errors)
    can_confirm = bool(records) and len(errors) / total <= 0.05
    pid = uuid.uuid4().hex
    PENDING[pid] = {"records": records, "ok": can_confirm}
    return {
        "id": pid,
        "mapping": mapping,
        "level_map": norm["level_map"],
        "accepted": len(records),
        "rejected": len(errors),
        "rejected_sample": errors[:5],
        "can_confirm": can_confirm,
    }


@app.post("/confirm/{pid}")
def confirm(pid: str):
    p = PENDING.get(pid)
    if not p or not p["ok"]:
        raise HTTPException(404, "This upload expired. Please upload the file again.")
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(p["records"], f)
        build_db.main(path)
        RECORDS.write_text(json.dumps([{k: v for k, v in r.items() if k != "id"} for r in p["records"]]))
    finally:
        os.remove(path)
    PENDING.pop(pid, None)
    return {"employees": len(p["records"])}


@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "equityvoice-dashboard.html")


@app.get("/analysis")
def analysis():
    data = analyze_db.analyze()
    if RECORDS.exists():
        try:
            groups, flat = analyze_db.groups_from_records(json.loads(RECORDS.read_text()))
            for dim in ("ethnicity", "disability"):
                if not data["groups"][dim]["available"] and groups[dim]["available"]:
                    data["groups"][dim] = groups[dim]
                    data["group_gaps"] = [g for g in data["group_gaps"] if g["dimension"] != dim] + [g for g in flat if g["dimension"] == dim]
        except Exception:
            pass
    return {"report": analyze_db.report_text(), "data": data}