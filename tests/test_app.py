from datetime import date, datetime, time, timedelta

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import Reservation, User, create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'test.db'}", seed=True)
    with TestClient(app) as test_client:
        yield test_client


def login(client, email="student@example.edu", password="student123"):
    response = client.post("/api/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def future_slot(start_hour=10, minutes=0):
    day = date.today() + timedelta(days=1)
    start = datetime.combine(day, time(start_hour, minutes))
    end = start + timedelta(hours=1)
    return start.isoformat(timespec="seconds") + "+08:00", end.isoformat(timespec="seconds") + "+08:00"


def seeded_seat(client, headers):
    spaces = client.get("/api/admin/spaces", headers=login(client, "admin@example.edu", "admin123"))
    assert spaces.status_code == 200
    return spaces.json()[0]["seats"][0]["seat_id"]


def create_reservation(client, headers, seat_id, start_at=None, end_at=None):
    if start_at is None:
        start_at, end_at = future_slot()
    return client.post("/api/reservations", headers=headers, json={
        "seat_id": seat_id, "start_at": start_at, "end_at": end_at,
    })


def test_login_returns_two_hour_token_and_invalid_password_is_rejected(client):
    result = client.post("/api/login", json={"email": "student@example.edu", "password": "student123"})
    assert result.status_code == 200
    assert result.json()["expires_in"] == 7200
    assert result.json()["user"]["role"] == "STUDENT"
    assert "password_hash" not in result.json()["user"]
    assert client.post("/api/login", json={"email": "student@example.edu", "password": "wrong"}).status_code == 401


def test_query_and_create_then_reject_overlapping_reservation(client):
    headers = login(client)
    seat_id = seeded_seat(client, headers)
    start_at, end_at = future_slot()
    slot_day = start_at[:10]
    search = client.get(f"/api/spaces?date={slot_day}&start_time=10:00&end_time=11:00", headers=headers)
    assert search.status_code == 200
    assert any(seat["seat_id"] == seat_id and seat["is_available"] for space in search.json() for seat in space["seats"])

    first = create_reservation(client, headers, seat_id, start_at, end_at)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "RESERVED"

    overlap_start = start_at[:11] + "10:30:00+08:00"
    overlap_end = start_at[:11] + "11:30:00+08:00"
    conflict = create_reservation(client, headers, seat_id, overlap_start, overlap_end)
    assert conflict.status_code == 409


def test_adjacent_reservations_are_allowed(client):
    headers = login(client)
    seat_id = seeded_seat(client, headers)
    start_at, end_at = future_slot(10)
    first = create_reservation(client, headers, seat_id, start_at, end_at)
    assert first.status_code == 201, first.text
    next_start = end_at
    next_end = (datetime.fromisoformat(end_at) + timedelta(hours=1)).isoformat(timespec="seconds")
    second = create_reservation(client, headers, seat_id, next_start, next_end)
    assert second.status_code == 201, second.text


def test_cancel_own_reservation_but_not_another_users(client):
    headers = login(client)
    seat_id = seeded_seat(client, headers)
    own = create_reservation(client, headers, seat_id)
    assert own.status_code == 201, own.text
    cancelled = client.delete("/api/reservations/" + own.json()["reservation_id"], headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    with client.app.state.SessionLocal() as db:
        other = User(email="other@example.edu", password_hash=bcrypt.hashpw(b"other123", bcrypt.gensalt(rounds=10)).decode(), role="STUDENT")
        db.add(other)
        db.commit()
    second = create_reservation(client, headers, seat_id)
    assert second.status_code == 201
    other_headers = login(client, "other@example.edu", "other123")
    forbidden = client.delete("/api/reservations/" + second.json()["reservation_id"], headers=other_headers)
    assert forbidden.status_code == 403


def test_reject_closed_time_and_invalid_interval(client):
    headers = login(client)
    seat_id = seeded_seat(client, headers)
    day = (date.today() + timedelta(days=1)).isoformat()
    after_hours = create_reservation(client, headers, seat_id, day + "T22:00:00+08:00", day + "T23:00:00+08:00")
    assert after_hours.status_code == 422
    reversed_time = create_reservation(client, headers, seat_id, day + "T11:00:00+08:00", day + "T10:00:00+08:00")
    assert reversed_time.status_code == 422


def test_admin_can_manage_space_seats_hours_and_student_is_forbidden(client):
    admin = login(client, "admin@example.edu", "admin123")
    student = login(client)
    created = client.post("/api/admin/spaces", headers=admin, json={"name": "Quiet Room", "location": "Hall 3F"})
    assert created.status_code == 201
    space_id = created.json()["space_id"]
    seat = client.post(f"/api/admin/spaces/{space_id}/seats", headers=admin, json={"seat_code": "Q01"})
    assert seat.status_code == 201
    hours = client.put(f"/api/admin/spaces/{space_id}/hours/1", headers=admin, json={"open_time": "09:00", "close_time": "18:00"})
    assert hours.status_code == 200
    denied = client.post("/api/admin/spaces", headers=student, json={"name": "Nope", "location": "Nowhere"})
    assert denied.status_code == 403
    duplicate = client.post(f"/api/admin/spaces/{space_id}/seats", headers=admin, json={"seat_code": "Q01"})
    assert duplicate.status_code == 409


def test_missing_token_is_unauthorized(client):
    assert client.get("/api/me/reservations").status_code == 401
