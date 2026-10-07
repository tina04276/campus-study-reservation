"""Reservation v2: additive migration, wallet ledger and attendance."""
import os
import uuid
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Annotated, Literal

import bcrypt
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Integer, String, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, User, StudySpace, Seat, OpeningHour, Reservation, TAIPEI, aware, reservation_dict


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)


class Wallet(Base):
    __tablename__ = "wallets"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_wallet_balance"),)


class PaymentOrder(Base):
    __tablename__ = "payment_orders"
    order_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    provider: Mapped[str] = mapped_column(String(20), default="DEMO")
    request_key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "request_key"), CheckConstraint("amount > 0"), CheckConstraint("status IN ('PENDING','PAID','CANCELLED')"))


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    transaction_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reservations.reservation_id"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payment_orders.order_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("kind IN ('TOPUP','BOOKING','REFUND')"), CheckConstraint("(kind='BOOKING' AND amount<0) OR (kind IN ('TOPUP','REFUND') AND amount>0)"))


class Attendance(Base):
    __tablename__ = "attendance"
    reservation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservations.reservation_id"), primary_key=True)
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def migrate_v2(engine):
    columns = {
        "study_spaces": {"category": "VARCHAR(20) NOT NULL DEFAULT 'GENERAL'", "capacity": "INTEGER NOT NULL DEFAULT 1", "equipment": "VARCHAR(500) NOT NULL DEFAULT ''", "hourly_rate": "INTEGER NOT NULL DEFAULT 0", "grid_rows": "INTEGER NOT NULL DEFAULT 4", "grid_cols": "INTEGER NOT NULL DEFAULT 7", "aisle_col": "INTEGER NOT NULL DEFAULT 4", "entrance_col": "INTEGER NOT NULL DEFAULT 4"},
        "seats": {"row_no": "INTEGER NOT NULL DEFAULT 0", "col_no": "INTEGER NOT NULL DEFAULT 0"},
        "reservations": {"amount": "INTEGER NOT NULL DEFAULT 0", "party_size": "INTEGER NOT NULL DEFAULT 1"},
    }
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(73204122)"))
        catalog = inspect(conn)
        for table, fields in columns.items():
            if not catalog.has_table(table): continue
            existing = {c["name"] for c in catalog.get_columns(table)}
            for name, definition in fields.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))


def seed_v2(factory, seed):
    with factory() as db:
        if db.bind.dialect.name == "postgresql": db.execute(text("SELECT pg_advisory_xact_lock(73204122)"))
        for user in db.scalars(select(User)).all():
            if db.get(Wallet, user.user_id) is None: db.add(Wallet(user_id=user.user_id, balance=0))
        for space in db.scalars(select(StudySpace)).all():
            seats = db.scalars(select(Seat).where(Seat.space_id == space.space_id).order_by(Seat.seat_code)).all()
            occupied = {(s.row_no, s.col_no) for s in seats if s.row_no and s.col_no}
            cols = [c for c in range(1, space.grid_cols+1) if c != space.aisle_col]
            for seat in seats:
                if not seat.row_no or not seat.col_no:
                    for row in range(1, 21):
                        free = next((c for c in cols if (row, c) not in occupied), None)
                        if free:
                            seat.row_no, seat.col_no = row, free
                            occupied.add((row, free)); space.grid_rows = max(space.grid_rows, row)
                            break
                    else: raise RuntimeError("座位超過配置上限，請調整後再部署")
        if seed and db.get(SchemaVersion, "v2-demo-spaces") is None:
            for category, name, capacity, rate, count in [("GENERAL", "一般自習區（示範）", 1, 20, 8), ("VIP", "VIP 自習區（示範）", 1, 35, 6), ("ROOM2", "雙人研究室（示範）", 2, 60, 2), ("ROOM4", "四人研究室（示範）", 4, 100, 2)]:
                space = StudySpace(name=name, location="校園學習中心 · 示範配置", category=category, capacity=capacity, equipment="Wi-Fi、插座、閱讀燈" + ("、白板" if capacity > 1 else ""), hourly_rate=rate)
                db.add(space); db.flush()
                for i in range(count):
                    db.add(Seat(space_id=space.space_id, seat_code=f"{category}-{i+1:02}", row_no=i//6+1, col_no=[1,2,3,5,6,7][i%6]))
                for day in range(1, 8): db.add(OpeningHour(space_id=space.space_id, weekday=day, open_time=time(8), close_time=time(22)))
            db.add(SchemaVersion(version="v2-demo-spaces"))
        db.commit()


def space_dict(space):
    return {"space_id": str(space.space_id), **{k: getattr(space, k) for k in ["name", "location", "category", "capacity", "equipment", "hourly_rate", "grid_rows", "grid_cols", "aisle_col", "entrance_col", "is_active"]}}


def seat_dict(seat):
    return {"seat_id": str(seat.seat_id), "seat_code": seat.seat_code, "row_no": seat.row_no, "col_no": seat.col_no}


def validate_space(values):
    if not values["name"].strip() or not values["location"].strip(): raise HTTPException(422, "名稱與位置不可空白")
    expected = {"GENERAL": 1, "VIP": 1, "ROOM2": 2, "ROOM4": 4}[values["category"]]
    if values["capacity"] != expected: raise HTTPException(422, "容量須符合空間類型：一般／VIP為1，雙人室為2，四人室為4")
    if values["entrance_col"] > values["grid_cols"] or values["aisle_col"] > values["grid_cols"]: raise HTTPException(422, "入口與走道必須位於配置範圍內")
    if values["grid_cols"] == 1 and values["aisle_col"] == 1: raise HTTPException(422, "配置至少須保留一欄座位")


def seat_position(db, space, row, col, exclude=None):
    occupied = {(s.row_no, s.col_no) for s in db.scalars(select(Seat).where(Seat.space_id == space.space_id)).all() if s.seat_id != exclude}
    if not row and not col:
        for r in range(1, space.grid_rows+1):
            for c in range(1, space.grid_cols+1):
                if c != space.aisle_col and (r,c) not in occupied: return r,c
        raise HTTPException(422, "座位圖已滿，請擴大配置")
    if not (1 <= row <= space.grid_rows and 1 <= col <= space.grid_cols) or col == space.aisle_col: raise HTTPException(422, "座位位置超出配置或位於走道")
    if (row,col) in occupied: raise HTTPException(409, "該位置已有座位")
    return row,col


def price(rate, start, end):
    minutes = ceil((end-start).total_seconds()/60)
    return (rate*minutes+59)//60


def debit(db, user_id, amount, reservation_id):
    changed = db.execute(update(Wallet).where(Wallet.user_id == user_id, Wallet.balance >= amount).values(balance=Wallet.balance-amount)).rowcount
    if not changed:
        db.rollback()
        raise HTTPException(402, "點數不足，請先到錢包儲值")
    db.add(WalletTransaction(user_id=user_id, amount=-amount, kind="BOOKING", reference="booking:"+str(reservation_id), reservation_id=reservation_id))


def credit(db, user_id, amount, kind, reference, reservation_id=None, order_id=None):
    db.add(WalletTransaction(user_id=user_id, amount=amount, kind=kind, reference=reference, reservation_id=reservation_id, order_id=order_id))
    db.flush()  # Unique reference prevents duplicate credit/refund.
    if not db.execute(update(Wallet).where(Wallet.user_id == user_id).values(balance=Wallet.balance+amount)).rowcount: raise HTTPException(409, "錢包尚未建立")


def booking_dict(db, r, seat, space, user=None):
    data = reservation_dict(r, seat, space, user)
    attendance = db.get(Attendance, r.reservation_id)
    now = datetime.now(TAIPEI)
    data.update({"payment_status": "REFUNDED" if r.status == "CANCELLED" and r.amount else "PAID" if r.amount else "FREE", "checked_in_at": aware(attendance.checked_in_at).isoformat() if attendance else None, "checked_out_at": aware(attendance.checked_out_at).isoformat() if attendance and attendance.checked_out_at else None,
                 "can_check_in": r.status == "RESERVED" and not attendance and aware(r.start_at)-timedelta(minutes=15) <= now < aware(r.end_at),
                 "can_check_out": r.status == "RESERVED" and attendance is not None and attendance.checked_out_at is None})
    data["can_cancel"] = data["can_cancel"] and attendance is None
    data["attendance_status"] = "CANCELLED" if r.status == "CANCELLED" else "CHECKED_OUT" if attendance and attendance.checked_out_at else "CHECKED_IN" if attendance else "NOT_ATTENDED" if now >= aware(r.end_at) else "WAITING"
    return data


class AccountInput(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=72)
    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        value = value.strip().lower()
        if "@" not in value or "." not in value.split("@")[-1]: raise ValueError("請輸入有效 Email")
        return value
    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode()) > 72: raise ValueError("密碼 UTF-8 長度不可超過72位元組")
        return value


class TopupInput(BaseModel):
    amount: int = Field(ge=50, le=10000)
    request_key: str = Field(min_length=8, max_length=80)


def order_dict(order):
    return {"order_id": str(order.order_id), "amount": order.amount, "status": order.status, "provider": order.provider, "created_at": aware(order.created_at).isoformat(), "paid_at": aware(order.paid_at).isoformat() if order.paid_at else None}


def install_v2_routes(app, DB, Student, Admin):
    demo = os.getenv("PAYMENT_MODE", "demo") == "demo"
    @app.get("/api/config")
    def config(): return {"payment_mode": "demo" if demo else "disabled", "currency": "TWD", "point_value": 1, "check_in_early_minutes": 15}

    @app.get("/api/admin/users")
    def users(db: DB, user: Admin):
        return [{"user_id": str(u.user_id), "email": u.email, "role": u.role} for u in db.scalars(select(User).order_by(User.email)).all()]

    @app.post("/api/admin/users", status_code=201)
    def create_user(body: AccountInput, db: DB, user: Admin):
        account = User(email=body.email, password_hash=bcrypt.hashpw(body.password.encode(), bcrypt.gensalt(rounds=10)).decode(), role="STUDENT")
        try:
            db.add(account); db.flush(); db.add(Wallet(user_id=account.user_id, balance=0)); db.commit()
        except IntegrityError:
            db.rollback(); raise HTTPException(409, "此 Email 已有帳號")
        return {"user_id": str(account.user_id), "email": account.email, "role": account.role}

    @app.get("/api/spaces/{space_id}/schedule")
    def schedule(space_id: uuid.UUID, day: date = Query(alias="date"), db: DB = None, user: Student = None):
        space = db.get(StudySpace, space_id)
        if not space or not space.is_active: raise HTTPException(404, "空間不存在或停用")
        hours = db.scalar(select(OpeningHour).where(OpeningHour.space_id == space_id, OpeningHour.weekday == day.isoweekday()))
        start = datetime.combine(day, time.min, TAIPEI); end = start + timedelta(days=1)
        output = []
        for seat in db.scalars(select(Seat).where(Seat.space_id == space_id, Seat.is_active.is_(True)).order_by(Seat.seat_code)).all():
            rows = db.scalars(select(Reservation).where(Reservation.seat_id == seat.seat_id, Reservation.status == "RESERVED", Reservation.start_at < end, Reservation.end_at > start).order_by(Reservation.start_at)).all()
            output.append({**seat_dict(seat), "booked": [{"start_at": aware(r.start_at).isoformat(), "end_at": aware(r.end_at).isoformat()} for r in rows]})
        return {"space": space_dict(space), "date": str(day), "opening_hours": {"open_time": str(hours.open_time)[:5], "close_time": str(hours.close_time)[:5]} if hours else None, "seats": output}

    @app.get("/api/reservations/{reservation_id}")
    def detail(reservation_id: uuid.UUID, db: DB, user: Student):
        r = db.get(Reservation, reservation_id)
        if not r: raise HTTPException(404, "預約不存在")
        if r.user_id != user.user_id: raise HTTPException(403, "不可查看其他人的預約")
        seat = db.get(Seat, r.seat_id); space = db.get(StudySpace, seat.space_id)
        return booking_dict(db, r, seat, space)

    def own_locked(db, user, reservation_id):
        r = db.scalar(select(Reservation).where(Reservation.reservation_id == reservation_id).with_for_update())
        if not r: raise HTTPException(404, "預約不存在")
        if r.user_id != user.user_id: raise HTTPException(403, "不可操作其他人的預約")
        if r.status != "RESERVED": raise HTTPException(409, "預約已取消")
        return r

    @app.post("/api/reservations/{reservation_id}/check-in")
    def check_in(reservation_id: uuid.UUID, db: DB, user: Student):
        r = own_locked(db, user, reservation_id); now = datetime.now(TAIPEI)
        if db.get(Attendance, reservation_id): raise HTTPException(409, "此預約已報到")
        if not aware(r.start_at)-timedelta(minutes=15) <= now < aware(r.end_at): raise HTTPException(409, "報到開放時間為開始前15分鐘至預約結束前")
        db.add(Attendance(reservation_id=reservation_id, checked_in_at=now))
        try: db.commit()
        except IntegrityError: db.rollback(); raise HTTPException(409, "此預約已報到")
        return {"message": "報到成功"}

    @app.post("/api/reservations/{reservation_id}/check-out")
    def check_out(reservation_id: uuid.UUID, db: DB, user: Student):
        own_locked(db, user, reservation_id)
        a = db.get(Attendance, reservation_id)
        if not a: raise HTTPException(409, "請先報到")
        if a.checked_out_at: raise HTTPException(409, "已完成離場")
        a.checked_out_at = datetime.now(TAIPEI); db.commit()
        return {"message": "離場成功"}

    @app.get("/api/wallet")
    def wallet(db: DB, user: Student):
        w = db.get(Wallet, user.user_id)
        return {"balance": w.balance, "payment_mode": "demo" if demo else "disabled", "transactions": [{"transaction_id": str(t.transaction_id), "amount": t.amount, "kind": t.kind, "reservation_id": str(t.reservation_id) if t.reservation_id else None, "order_id": str(t.order_id) if t.order_id else None, "created_at": aware(t.created_at).isoformat()} for t in db.scalars(select(WalletTransaction).where(WalletTransaction.user_id == user.user_id).order_by(WalletTransaction.created_at.desc(), WalletTransaction.transaction_id)).all()], "orders": [order_dict(o) for o in db.scalars(select(PaymentOrder).where(PaymentOrder.user_id == user.user_id).order_by(PaymentOrder.created_at.desc())).all()]}

    @app.post("/api/payments/topups", status_code=201)
    def topup(body: TopupInput, db: DB, user: Student):
        if not demo: raise HTTPException(503, "尚未串接正式金流，儲值暫停")
        old = db.scalar(select(PaymentOrder).where(PaymentOrder.user_id == user.user_id, PaymentOrder.request_key == body.request_key))
        if old:
            if old.amount != body.amount: raise HTTPException(409, "同一請求不可更改金額")
            return order_dict(old)
        order = PaymentOrder(user_id=user.user_id, amount=body.amount, request_key=body.request_key)
        try: db.add(order); db.commit(); db.refresh(order)
        except IntegrityError: db.rollback(); raise HTTPException(409, "儲值請求重複，請重新整理錢包")
        return order_dict(order)

    @app.post("/api/payments/{order_id}/demo-confirm")
    def confirm(order_id: uuid.UUID, db: DB, user: Student):
        if not demo: raise HTTPException(503, "模擬付款已停用")
        order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_id == order_id).with_for_update())
        if not order: raise HTTPException(404, "付款訂單不存在")
        if order.user_id != user.user_id: raise HTTPException(403, "不可操作其他人的訂單")
        if order.status == "PAID": return order_dict(order)
        if order.status != "PENDING": raise HTTPException(409, "此訂單不能付款")
        try:
            credit(db, user.user_id, order.amount, "TOPUP", "topup:"+str(order_id), order_id=order_id)
            order.status = "PAID"; order.paid_at = datetime.now(TAIPEI); db.commit()
        except IntegrityError: db.rollback(); raise HTTPException(409, "付款已處理，請重新整理")
        return order_dict(order)

    @app.post("/api/payments/{order_id}/cancel")
    def cancel_order(order_id: uuid.UUID, db: DB, user: Student):
        order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_id == order_id).with_for_update())
        if not order: raise HTTPException(404, "付款訂單不存在")
        if order.user_id != user.user_id: raise HTTPException(403, "不可操作其他人的訂單")
        if order.status != "PENDING": raise HTTPException(409, "只可取消待付款訂單")
        order.status = "CANCELLED"; db.commit(); return order_dict(order)

    @app.get("/api/admin/payments")
    def payments(db: DB, user: Admin):
        return [{**order_dict(o), "email": u.email} for o,u in db.execute(select(PaymentOrder, User).join(User, User.user_id == PaymentOrder.user_id).order_by(PaymentOrder.created_at.desc())).all()]
