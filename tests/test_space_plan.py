import uuid
from sqlalchemy import select, delete
from app.models import StudySpace, Seat, Reservation
from app.extensions import SchemaVersion
from app.space_plan import PLAN, NAMES, LEGACY_COUNTS, apply_space_plan
from test_app import client, login
from test_v2 import book, room, topup


def test_planned_capacity_and_links(client):
    h=login(client,'admin@example.edu','admin123')
    spaces=client.get('/api/admin/spaces',headers=h).json()
    planned=[s for s in spaces if s['plan_zone']]
    assert len(planned)==4
    assert sum(len(s['seats'])*s['capacity'] for s in planned)==52
    for s in planned:
        p=PLAN[s['category']]
        assert len(s['seats'])==p['count']
        assert len({(x['row_no'],x['col_no']) for x in s['seats']})==p['count']
        assert all(x['col_no']!=s['aisle_col'] for x in s['seats'])
        assert s['entrance_col']==s['aisle_col']
    assert all(not s['plan_zone'] for s in spaces if s['name'] not in NAMES.values())


def test_upgrade_keeps_booking_ids_and_is_idempotent(client):
    h=login(client);topup(client,h);space=room(client);r=book(client,h,space).json()
    factory=client.app.state.SessionLocal
    with factory() as db:
        db.execute(delete(SchemaVersion).where(SchemaVersion.version=='v3-space-plan'))
        for s in db.scalars(select(StudySpace)).all():
            if s.name not in NAMES.values():continue
            seats=db.scalars(select(Seat).where(Seat.space_id==s.space_id).order_by(Seat.seat_code)).all()
            for seat in seats[LEGACY_COUNTS[s.category]:]:db.delete(seat)
            s.location='校園學習中心 · 示範配置';s.equipment='Wi-Fi、插座、閱讀燈'+('、白板' if s.capacity>1 else '')
            s.grid_rows=4;s.grid_cols=7;s.aisle_col=4;s.entrance_col=4
        db.commit()
        apply_space_plan(db);db.commit()
        before=db.scalars(select(Seat.seat_id)).all()
        apply_space_plan(db);db.commit()
        assert set(db.scalars(select(Seat.seat_id)).all())==set(before)
        reservation=db.get(Reservation,uuid.UUID(r['reservation_id']))
        assert str(reservation.seat_id)==space['seats'][0]['seat_id'] and reservation.amount==60
    assert client.get('/api/reservations/'+r['reservation_id'],headers=h).json()['status']=='RESERVED'


def test_upgrade_preserves_manager_customizations(client):
    with client.app.state.SessionLocal() as db:
        db.execute(delete(SchemaVersion).where(SchemaVersion.version=='v3-space-plan'))
        space=db.scalar(select(StudySpace).where(StudySpace.category=='VIP'))
        space.location='管理者的配置';space.hourly_rate=88
        db.commit();before=[s.seat_id for s in db.scalars(select(Seat).where(Seat.space_id==space.space_id))]
        apply_space_plan(db);db.commit()
        assert space.location=='管理者的配置' and space.hourly_rate==88
        assert [s.seat_id for s in db.scalars(select(Seat).where(Seat.space_id==space.space_id))]==before
