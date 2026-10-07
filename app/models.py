import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Time, UniqueConstraint, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
TAIPEI = ZoneInfo("Asia/Taipei")

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("role IN ('STUDENT', 'ADMIN')", name="ck_users_role"),)


class StudySpace(Base):
    __tablename__ = "study_spaces"

    space_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(20), default="GENERAL", server_default="GENERAL")
    capacity: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    equipment: Mapped[str] = mapped_column(String(500), default="", server_default="")
    hourly_rate: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    grid_rows: Mapped[int] = mapped_column(Integer, default=4, server_default="4")
    grid_cols: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    aisle_col: Mapped[int] = mapped_column(Integer, default=4, server_default="4")
    entrance_col: Mapped[int] = mapped_column(Integer, default=4, server_default="4")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Seat(Base):
    __tablename__ = "seats"

    seat_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    space_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("study_spaces.space_id", ondelete="RESTRICT"))
    seat_code: Mapped[str] = mapped_column(String(30))
    row_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    col_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (UniqueConstraint("space_id", "seat_code", name="uq_seats_space_code"),)


class OpeningHour(Base):
    __tablename__ = "opening_hours"

    opening_hours_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    space_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("study_spaces.space_id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column()
    open_time: Mapped[time] = mapped_column(Time)
    close_time: Mapped[time] = mapped_column(Time)
    __table_args__ = (
        UniqueConstraint("space_id", "weekday", name="uq_hours_space_weekday"),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_hours_weekday"),
        CheckConstraint("close_time > open_time", name="ck_hours_order"),
    )


class Reservation(Base):
    __tablename__ = "reservations"

    reservation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.user_id", ondelete="RESTRICT"))
    seat_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("seats.seat_id", ondelete="RESTRICT"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="RESERVED", server_default="RESERVED")
    amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    party_size: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_reservation_time_order"),
        CheckConstraint("status IN ('RESERVED', 'CANCELLED')", name="ck_reservation_status"),
    )


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=TAIPEI) if value.tzinfo is None else value


def reservation_dict(reservation: Reservation, seat: Seat, space: StudySpace, user: User | None = None) -> dict:
    data = {
        "reservation_id": str(reservation.reservation_id),
        "seat_id": str(seat.seat_id), "seat_code": seat.seat_code,
        "space_id": str(space.space_id), "space_name": space.name,
        "location": space.location, "start_at": aware(reservation.start_at).isoformat(),
        "end_at": aware(reservation.end_at).isoformat(), "status": reservation.status,
        "created_at": aware(reservation.created_at).isoformat() if reservation.created_at else None,
        "amount": reservation.amount, "party_size": reservation.party_size,
        "category": space.category,
        "can_cancel": reservation.status == "RESERVED" and aware(reservation.start_at) > datetime.now(TAIPEI),
    }
    if user is not None:
        data.update({"user_id": str(user.user_id), "email": user.email})
    return data


