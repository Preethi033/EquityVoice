from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, create_engine, Session, select

# ---------- Database (Step 2) ----------
class Submission(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    position: str
    salary_min: int
    salary_max: int

engine = create_engine("sqlite:///salaries.db")
SQLModel.metadata.create_all(engine)

# ---------- Server (Step 3) ----------
app = FastAPI()

class SubmissionIn(BaseModel):      # what the employee is allowed to send
    position: str
    salary_min: int
    salary_max: int

@app.post("/submit")
def submit(data: SubmissionIn):
    # 1. Validate
    if data.salary_min <= 0 or data.salary_max <= 0:
        raise HTTPException(400, "Salary must be positive")
    if data.salary_min > data.salary_max:
        raise HTTPException(400, "Min can't be higher than max")

    # 2. Save
    row = Submission(
        position=data.position.strip().lower(),
        salary_min=data.salary_min,
        salary_max=data.salary_max,
    )
    with Session(engine) as db:
        db.add(row)
        db.commit()

    # 3. Reply
    return {"status": "saved"}
# ---------- Stats (Step 4) ----------
MIN_GROUP_SIZE = 5

@app.get("/stats/{position}")
def stats(position: str):
    position = position.strip().lower()   # match how we saved it

    with Session(engine) as db:
        rows = db.exec(
            select(Submission).where(Submission.position == position)
        ).all()

    if len(rows) < MIN_GROUP_SIZE:
        return {"message": "Not enough data yet to protect anonymity"}

    return {
        "position": position,
        "count": len(rows),
        "avg_min": sum(r.salary_min for r in rows) // len(rows),
        "avg_max": sum(r.salary_max for r in rows) // len(rows),
    }