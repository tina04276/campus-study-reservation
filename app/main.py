import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import bcrypt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy import (
    Integer,
    inspect,
    update,
    event,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Select,
    String,
    Time,
    UniqueConstraint,
    Uuid,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

TAIPEI = ZoneInfo("Asia/Taipei")
TOKEN_TTL = timedelta(hours=2)
DEFAULT_SECRET = "development-only-change-me"
ROOT = Path(__file__).resolve().parent


from .models import Base, User, StudySpace, Seat, OpeningHour, Reservation, aware, reservation_dict


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("password")
    @classmethod
    def password_size(cls, value):
        if len(value.encode()) > 72: raise ValueError("密碼長度超出限制")
        return value


class SpaceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=255)
    category: Literal["GENERAL", "VIP", "ROOM2", "ROOM4"] = "GENERAL"
    capacity: int = Field(default=1, ge=1, le=4)
    equipment: str = Field(default="", max_length=500)
    hourly_rate: int = Field(default=0, ge=0, le=10000)
    grid_rows: int = Field(default=4, ge=1, le=20)
    grid_cols: int = Field(default=7, ge=1, le=15)
    aisle_col: int = Field(default=4, ge=0, le=15)
    entrance_col: int = Field(default=4, ge=1, le=15)


class SpacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    location: str | None = Field(default=None, min_length=1, max_length=255)
    category: Literal["GENERAL", "VIP", "ROOM2", "ROOM4"] | None = None
    capacity: int | None = Field(default=None, ge=1, le=4)
    equipment: str | None = Field(default=None, max_length=500)
    hourly_rate: int | None = Field(default=None, ge=0, le=10000)
    grid_rows: int | None = Field(default=None, ge=1, le=20)
    grid_cols: int | None = Field(default=None, ge=1, le=15)
    aisle_col: int | None = Field(default=None, ge=0, le=15)
    entrance_col: int | None = Field(default=None, ge=1, le=15)
    is_active: bool | None = None


class SeatInput(BaseModel):
    seat_code: str = Field(min_length=1, max_length=30)
    row_no: int = Field(default=0, ge=0, le=20)
    col_no: int = Field(default=0, ge=0, le=15)


class SeatPatch(BaseModel):
    seat_code: str | None = Field(default=None, min_length=1, max_length=30)
    row_no: int | None = Field(default=None, ge=1, le=20)
    col_no: int | None = Field(default=None, ge=1, le=15)
    is_active: bool | None = None


class HoursInput(BaseModel):
    open_time: time
    close_time: time

    @field_validator("close_time")
    @classmethod
    def close_after_open(cls, value: time, info):
        opened = info.data.get("open_time")
        if opened is not None and value <= opened:
            raise ValueError("close_time must be later than open_time")
        return value


class ReservationInput(BaseModel):
    seat_id: uuid.UUID
    start_at: AwareDatetime
    end_at: AwareDatetime
    party_size: int = Field(default=1, ge=1, le=4)
    expected_amount: int | None = Field(default=None, ge=0, le=1000000)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def issue_token(user: User, secret: str, now: datetime | None = None) -> str:
    issued = now or datetime.now(timezone.utc)
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({
        "sub": str(user.user_id), "role": user.role,
        "iat": int(issued.timestamp()), "exp": int((issued + TOKEN_TTL).timestamp()),
    }, separators=(",", ":")).encode())
    message = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return f"{header}.{payload}.{b64url(signature)}"


def decode_token(token: str, secret: str) -> dict:
    try:
        header, payload, signature = token.split(".")
        message = f"{header}.{payload}".encode("ascii")
        expected = b64url(hmac.new(secret.encode(), message, hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data.get("exp", 0) <= int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("expired")
        if data.get("role") not in {"STUDENT", "ADMIN"}:
            raise ValueError("bad role")
        uuid.UUID(data["sub"])
        return data
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


def install_postgres_overlap_constraint(engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        conn.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ex_reservations_no_overlap'
                ) THEN
                    ALTER TABLE reservations ADD CONSTRAINT ex_reservations_no_overlap
                    EXCLUDE USING gist (
                        seat_id WITH =,
                        tstzrange(start_at, end_at, '[)') WITH &&
                    ) WHERE (status = 'RESERVED');
                END IF;
            END $$;
        """))


def create_app(database_url: str | None = None, seed: bool = True) -> FastAPI:
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///./reservation.db")
    # Render Postgres exposes postgres:// or postgresql:// URLs. SQLAlchemy 2
    # needs the psycopg 3 dialect selected explicitly in this deployment.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    secret = os.getenv("JWT_SECRET", DEFAULT_SECRET)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_fk(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    migrate_v2(engine)
    Base.metadata.create_all(engine)
    install_postgres_overlap_constraint(engine)

    app = FastAPI(title="Campus Study Reservation", version="2.0.0")
    app.state.engine = engine
    app.state.SessionLocal = session_factory
    app.state.jwt_secret = secret

    if seed:
        with session_factory() as db:
            if db.scalar(select(User).limit(1)) is None:
                db.add_all([
                    User(email="student@example.edu", password_hash=bcrypt.hashpw(b"student123", bcrypt.gensalt(rounds=10)).decode(), role="STUDENT"),
                    User(email="admin@example.edu", password_hash=bcrypt.hashpw(b"admin123", bcrypt.gensalt(rounds=10)).decode(), role="ADMIN"),
                ])
            if db.scalar(select(StudySpace).limit(1)) is None:
                space_a = StudySpace(name="Library Study Hall", location="Library, 2F", is_active=True)
                space_b = StudySpace(name="Learning Commons", location="Academic Building, 1F", is_active=True)
                db.add_all([space_a, space_b])
                db.flush()
                db.add_all([Seat(space_id=space_a.space_id, seat_code=f"A{i:02d}") for i in range(1, 9)])
                db.add_all([Seat(space_id=space_b.space_id, seat_code=f"B{i:02d}") for i in range(1, 7)])
                for space in (space_a, space_b):
                    db.add_all([OpeningHour(space_id=space.space_id, weekday=day, open_time=time(8), close_time=time(22)) for day in range(1, 8)])
            db.commit()

    seed_v2(session_factory, seed)
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_seats_layout ON seats (space_id, row_no, col_no) WHERE row_no > 0 AND col_no > 0"))

    def get_db(request: Request):
        with request.app.state.SessionLocal() as db:
            yield db

    DB = Annotated[Session, Depends(get_db)]

    def current_user(request: Request, authorization: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)) -> User:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Bearer token required")
        claims = decode_token(authorization.split(" ", 1)[1], request.app.state.jwt_secret)
        user = db.get(User, uuid.UUID(claims["sub"]))
        if user is None:
            raise HTTPException(status_code=401, detail="User no longer exists")
        return user

    def require_role(role: str):
        def dependency(user: User = Depends(current_user)) -> User:
            if user.role != role:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return user
        return dependency

    Student = Annotated[User, Depends(require_role("STUDENT"))]
    Admin = Annotated[User, Depends(require_role("ADMIN"))]

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/me")
    def me(user: User = Depends(current_user)):
        return {"user_id": str(user.user_id), "email": user.email, "role": user.role}

    @app.post("/api/login")
    def login(body: LoginInput, request: Request, db: DB):
        user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
        if user is None or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
            raise HTTPException(status_code=401, detail="Email or password is incorrect")
        return {"access_token": issue_token(user, request.app.state.jwt_secret), "token_type": "bearer",
                "expires_in": int(TOKEN_TTL.total_seconds()),
                "user": {"user_id": str(user.user_id), "email": user.email, "role": user.role}}

    @app.get("/api/spaces")
    def list_spaces(
        db: DB, user: Student,
        day: date = Query(alias="date"),
        start_time: time = Query(), end_time: time = Query(),
    ):
        if end_time <= start_time:
            raise HTTPException(status_code=422, detail="end_time must be later than start_time")
        start_at = datetime.combine(day, start_time, TAIPEI)
        end_at = datetime.combine(day, end_time, TAIPEI)
        spaces = db.scalars(select(StudySpace).where(StudySpace.is_active.is_(True)).order_by(StudySpace.name)).all()
        result = []
        for space in spaces:
            hours = db.scalar(select(OpeningHour).where(
                OpeningHour.space_id == space.space_id, OpeningHour.weekday == day.isoweekday()))
            is_open = hours is not None and start_time >= hours.open_time and end_time <= hours.close_time
            seats = db.scalars(select(Seat).where(Seat.space_id == space.space_id, Seat.is_active.is_(True)).order_by(Seat.seat_code)).all()
            seat_results = []
            for seat in seats:
                overlap = db.scalar(select(Reservation.reservation_id).where(
                    Reservation.seat_id == seat.seat_id,
                    Reservation.status == "RESERVED",
                    Reservation.start_at < end_at,
                    Reservation.end_at > start_at,
                ).limit(1)) is not None
                seat_results.append({**seat_dict(seat),
                                     "is_available": is_open and not overlap})
            result.append({**space_dict(space), "opening_hours": {"open_time": hours.open_time.strftime("%H:%M"), "close_time": hours.close_time.strftime("%H:%M")} if hours else None,
                           "is_open": is_open, "seats": seat_results})
        return result

    @app.post("/api/reservations", status_code=201)
    def create_reservation(body: ReservationInput, db: DB, user: Student):
        start_at, end_at = body.start_at.astimezone(TAIPEI), body.end_at.astimezone(TAIPEI)
        if end_at <= start_at or start_at.date() != end_at.date():
            raise HTTPException(status_code=422, detail="Reservation must end after it starts on the same date")
        if start_at <= datetime.now(TAIPEI):
            raise HTTPException(status_code=422, detail="Reservation must start in the future")
        seat = db.get(Seat, body.seat_id)
        if seat is None or not seat.is_active:
            raise HTTPException(status_code=404, detail="Seat not found or inactive")
        space = db.get(StudySpace, seat.space_id)
        if space is None or not space.is_active:
            raise HTTPException(status_code=404, detail="Study space not found or inactive")
        hours = db.scalar(select(OpeningHour).where(
            OpeningHour.space_id == space.space_id, OpeningHour.weekday == start_at.isoweekday()))
        if hours is None or start_at.time().replace(tzinfo=None) < hours.open_time or end_at.time().replace(tzinfo=None) > hours.close_time:
            raise HTTPException(status_code=422, detail="Reservation is outside opening hours")
        overlap = db.scalar(select(Reservation.reservation_id).where(
            Reservation.seat_id == seat.seat_id, Reservation.status == "RESERVED",
            Reservation.start_at < end_at, Reservation.end_at > start_at).limit(1))
        if overlap is not None:
            raise HTTPException(status_code=409, detail="Seat is already reserved for this time")
        if body.party_size > space.capacity:
            raise HTTPException(422, "人數超過此座位／研究室容量")
        amount = price(space.hourly_rate, start_at, end_at)
        if body.expected_amount is not None and body.expected_amount != amount:
            raise HTTPException(409, "價格已更新，請重新查詢並確認金額")
        reservation = Reservation(user_id=user.user_id, seat_id=seat.seat_id, amount=amount, party_size=body.party_size,
                                  start_at=start_at, end_at=end_at, status="RESERVED")
        db.add(reservation)
        try:
            db.flush()
            if amount:
                debit(db, user.user_id, amount, reservation.reservation_id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Seat is already reserved for this time") from exc
        db.refresh(reservation)
        return booking_dict(db, reservation, seat, space)

    @app.get("/api/me/reservations")
    def my_reservations(db: DB, user: Student, day: date | None = Query(default=None, alias="date"), booking_status: Literal["RESERVED", "CANCELLED"] | None = Query(default=None, alias="status")):
        rows = db.execute(select(Reservation, Seat, StudySpace)
                          .join(Seat, Reservation.seat_id == Seat.seat_id)
                          .join(StudySpace, Seat.space_id == StudySpace.space_id)
                          .where(Reservation.user_id == user.user_id)
                          .order_by(Reservation.start_at.desc())).all()
        return [booking_dict(db, r, seat, space) for r, seat, space in rows if (day is None or aware(r.start_at).astimezone(TAIPEI).date() == day) and (booking_status is None or r.status == booking_status)]

    @app.delete("/api/reservations/{reservation_id}")
    def cancel_reservation(reservation_id: uuid.UUID, db: DB, user: Student):
        reservation = db.scalar(select(Reservation).where(Reservation.reservation_id == reservation_id).with_for_update())
        if reservation is None:
            raise HTTPException(status_code=404, detail="Reservation not found")
        if reservation.user_id != user.user_id:
            raise HTTPException(status_code=403, detail="Cannot cancel another user's reservation")
        if reservation.status != "RESERVED" or aware(reservation.start_at) <= datetime.now(TAIPEI):
            raise HTTPException(status_code=409, detail="Reservation can no longer be cancelled")
        attendance = db.get(Attendance, reservation_id)
        if attendance and attendance.checked_in_at:
            raise HTTPException(409, "已報到的預約不可取消")
        if reservation.amount:
            credit(db, user.user_id, reservation.amount, "REFUND", "refund:" + str(reservation_id), reservation_id=reservation_id)
        reservation.status = "CANCELLED"
        db.commit()
        return {"message": "Reservation cancelled", "reservation_id": str(reservation_id), "status": "CANCELLED"}

    @app.get("/api/admin/spaces")
    def admin_spaces(db: DB, user: Admin):
        spaces = db.scalars(select(StudySpace).order_by(StudySpace.name)).all()
        output = []
        for space in spaces:
            seats = db.scalars(select(Seat).where(Seat.space_id == space.space_id).order_by(Seat.seat_code)).all()
            hours = db.scalars(select(OpeningHour).where(OpeningHour.space_id == space.space_id).order_by(OpeningHour.weekday)).all()
            output.append({**space_dict(space),
                           "is_active": space.is_active,
                           "seats": [{**seat_dict(s), "is_active": s.is_active} for s in seats],
                           "opening_hours": [{"weekday": h.weekday, "open_time": h.open_time.strftime("%H:%M"),
                                              "close_time": h.close_time.strftime("%H:%M")} for h in hours]})
        return output

    @app.post("/api/admin/spaces", status_code=201)
    def add_space(body: SpaceInput, db: DB, user: Admin):
        values = body.model_dump()
        validate_space(values)
        space = StudySpace(**values, is_active=True)
        db.add(space); db.commit(); db.refresh(space)
        return {"space_id": str(space.space_id), "name": space.name, "location": space.location, "is_active": space.is_active}

    @app.patch("/api/admin/spaces/{space_id}")
    def patch_space(space_id: uuid.UUID, body: SpacePatch, db: DB, user: Admin):
        space = db.get(StudySpace, space_id)
        if space is None: raise HTTPException(status_code=404, detail="Study space not found")
        changes = body.model_dump(exclude_unset=True)
        if any(v is None for v in changes.values()): raise HTTPException(422, "欄位不可為空值")
        values = space_dict(space) | changes
        validate_space(values)
        existing = db.scalars(select(Seat).where(Seat.space_id == space_id)).all()
        for seat in existing:
            if seat.row_no > values["grid_rows"] or seat.col_no > values["grid_cols"] or seat.col_no == values["aisle_col"]:
                raise HTTPException(422, "新配置會覆蓋既有座位，請先調整座位位置")
        for key, value in changes.items(): setattr(space, key, value)
        db.commit(); db.refresh(space)
        return {"space_id": str(space.space_id), "name": space.name, "location": space.location, "is_active": space.is_active}

    @app.post("/api/admin/spaces/{space_id}/seats", status_code=201)
    def add_seat(space_id: uuid.UUID, body: SeatInput, db: DB, user: Admin):
        space = db.get(StudySpace, space_id)
        if space is None: raise HTTPException(status_code=404, detail="Study space not found")
        # Serialize placement writes for this space on PostgreSQL.
        db.scalar(select(StudySpace).where(StudySpace.space_id == space_id).with_for_update())
        row, col = seat_position(db, space, body.row_no, body.col_no)
        seat = Seat(space_id=space_id, seat_code=body.seat_code.strip(), row_no=row, col_no=col, is_active=True)
        if not seat.seat_code: raise HTTPException(422, "座位代碼不可空白")
        db.add(seat)
        try: db.commit()
        except IntegrityError as exc:
            db.rollback(); raise HTTPException(status_code=409, detail="Seat code already exists in this space") from exc
        db.refresh(seat)
        return {"seat_id": str(seat.seat_id), "space_id": str(space_id), "seat_code": seat.seat_code, "is_active": seat.is_active}

    @app.patch("/api/admin/seats/{seat_id}")
    def patch_seat(seat_id: uuid.UUID, body: SeatPatch, db: DB, user: Admin):
        seat = db.get(Seat, seat_id)
        if seat is None: raise HTTPException(status_code=404, detail="Seat not found")
        changes = body.model_dump(exclude_unset=True)
        if any(v is None for v in changes.values()): raise HTTPException(422, "欄位不可為空值")
        space = db.scalar(select(StudySpace).where(StudySpace.space_id == seat.space_id).with_for_update())
        seat_position(db, space, changes.get("row_no", seat.row_no), changes.get("col_no", seat.col_no), seat.seat_id)
        if "seat_code" in changes and not changes["seat_code"].strip(): raise HTTPException(422, "座位代碼不可空白")
        for key, value in changes.items(): setattr(seat, key, value)
        try: db.commit()
        except IntegrityError as exc:
            db.rollback(); raise HTTPException(status_code=409, detail="Seat code already exists in this space") from exc
        return {"seat_id": str(seat.seat_id), "seat_code": seat.seat_code, "is_active": seat.is_active}

    @app.put("/api/admin/spaces/{space_id}/hours/{weekday}")
    def set_hours(space_id: uuid.UUID, weekday: int, body: HoursInput, db: DB, user: Admin):
        if weekday < 1 or weekday > 7: raise HTTPException(status_code=422, detail="weekday must be between 1 and 7")
        if db.get(StudySpace, space_id) is None: raise HTTPException(status_code=404, detail="Study space not found")
        hours = db.scalar(select(OpeningHour).where(OpeningHour.space_id == space_id, OpeningHour.weekday == weekday))
        if hours is None:
            hours = OpeningHour(space_id=space_id, weekday=weekday, open_time=body.open_time, close_time=body.close_time)
            db.add(hours)
        else:
            hours.open_time, hours.close_time = body.open_time, body.close_time
        db.commit()
        return {"space_id": str(space_id), "weekday": weekday, "open_time": body.open_time.strftime("%H:%M"), "close_time": body.close_time.strftime("%H:%M")}

    @app.get("/api/admin/reservations")
    def admin_reservations(db: DB, user: Admin):
        rows = db.execute(select(Reservation, Seat, StudySpace, User)
                          .join(Seat, Reservation.seat_id == Seat.seat_id)
                          .join(StudySpace, Seat.space_id == StudySpace.space_id)
                          .join(User, Reservation.user_id == User.user_id)
                          .order_by(Reservation.start_at.desc())).all()
        return [booking_dict(db, r, seat, space, owner) for r, seat, space, owner in rows]

    install_v2_routes(app, DB, Student, Admin)

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(ROOT / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


# V2 extension definitions are loaded before constructing the app.
from .extensions import Attendance, install_v2_routes, migrate_v2, seed_v2, space_dict, seat_dict, booking_dict, price, debit, credit, validate_space, seat_position

app = create_app()
