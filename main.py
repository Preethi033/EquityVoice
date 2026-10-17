from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, create_engine, Session, select


BASE_DIR = Path(__file__).resolve().parent
MIN_GROUP_SIZE = 5


class Submission(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    position: str
    salary_min: int
    salary_max: int
    team: str | None = None
    consent: bool = False
    disability_status: str | None = None


engine = create_engine(
    f"sqlite:///{BASE_DIR / 'salaries.db'}",
    connect_args={"check_same_thread": False},
)

SQLModel.metadata.create_all(engine)

# Add new columns without deleting existing submissions.
with engine.begin() as connection:
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(submission)"
        )
    }

    for name, definition in {
        "team": "TEXT",
        "consent": "BOOLEAN NOT NULL DEFAULT 0",
        "disability_status": "TEXT",
    }.items():
        if name not in columns:
            connection.exec_driver_sql(
                f"ALTER TABLE submission ADD COLUMN {name} {definition}"
            )


app = FastAPI()


class SubmissionIn(BaseModel):
    position: str
    salary_min: int
    salary_max: int
    team: str | None = None
    consent: bool = False
    disability_status: str | None = None


@app.get("/")
def dashboard():
    return FileResponse(BASE_DIR / "equityvoice-dashboard.html")


@app.post("/submit")
def submit(data: SubmissionIn):
    if not data.position.strip():
        raise HTTPException(400, "Role is required")

    if data.salary_min <= 0 or data.salary_max <= 0:
        raise HTTPException(400, "Salary must be positive")

    if data.salary_min > data.salary_max:
        raise HTTPException(400, "Min can't be higher than max")

    if not data.consent:
        raise HTTPException(400, "Consent is required")

    if not data.team or not data.team.strip():
        raise HTTPException(400, "Team is required")

    if data.disability_status not in (None, "Has disability"):
        raise HTTPException(400, "Invalid disability status")

    row = Submission(
        position=data.position.strip().lower(),
        salary_min=data.salary_min,
        salary_max=data.salary_max,
        team=data.team.strip(),
        consent=data.consent,
        disability_status=data.disability_status,
    )

    with Session(engine) as db:
        db.add(row)
        db.commit()

    return {"status": "saved"}


@app.get("/stats/{position}")
def stats(position: str):
    position = position.strip().lower()

    with Session(engine) as db:
        rows = db.exec(
            select(Submission).where(
                Submission.position == position,
                Submission.consent == True,
            )
        ).all()

    if len(rows) < MIN_GROUP_SIZE:
        return {
            "message": "Results are hidden until there are at least 5 consenting submissions."
        }

    return {
        "position": position,
        "count": len(rows),
        "avg_min": sum(r.salary_min for r in rows) // len(rows),
        "avg_max": sum(r.salary_max for r in rows) // len(rows),
    }