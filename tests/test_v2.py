import uuid
from datetime import datetime, timedelta
from sqlalchemy import select, text, inspect
from app.main import create_app, Reservation, TAIPEI
from app.extensions import Wallet, WalletTransaction, StudySpace, Seat, Attendance
from test_app import login, future_slot, client


def room(client, category='ROOM2'):
    spaces=client.get('/api/admin/spaces',headers=login(client,'admin@example.edu','admin123')).json()
    return next(s for s in spaces if s['category']==category)


def topup(client, headers, amount=500, key=None):
    order=client.post('/api/payments/topups',headers=headers,json={'amount':amount,'request_key':key or str(uuid.uuid4())})
    assert order.status_code==201,order.text
    paid=client.post('/api/payments/'+order.json()['order_id']+'/demo-confirm',headers=headers)
    assert paid.status_code==200,paid.text
    return order.json()


def book(client, headers, space, **extra):
    start,end=future_slot()
    return client.post('/api/reservations',headers=headers,json={'seat_id':space['seats'][0]['seat_id'],'start_at':start,'end_at':end,**extra})


def test_wallet_booking_refund_and_filters(client):
    headers=login(client);space=room(client)
    rejected=book(client,headers,space)
    assert rejected.status_code==402
    assert client.get('/api/me/reservations',headers=headers).json()==[]
    topup(client,headers)
    assert book(client,headers,space,party_size=3).status_code==422
    r=book(client,headers,space,party_size=2)
    assert r.status_code==201,r.text
    rid=r.json()['reservation_id']; assert r.json()['amount']==60
    assert client.get('/api/wallet',headers=headers).json()['balance']==440
    assert book(client,headers,space).status_code==409
    assert client.get('/api/wallet',headers=headers).json()['balance']==440
    assert len(client.get('/api/me/reservations?status=RESERVED',headers=headers).json())==1
    assert client.get('/api/me/reservations?date=2000-01-01',headers=headers).json()==[]
    assert client.get('/api/me/reservations?status=INVALID',headers=headers).status_code==422
    assert client.get('/api/reservations/'+rid,headers=headers).status_code==200
    assert client.delete('/api/reservations/'+rid,headers=headers).status_code==200
    assert client.delete('/api/reservations/'+rid,headers=headers).status_code==409
    wallet=client.get('/api/wallet',headers=headers).json()
    assert wallet['balance']==500
    assert sorted(t['amount'] for t in wallet['transactions'])==[-60,60,500]
    assert client.get('/api/me/reservations?status=CANCELLED',headers=headers).json()[0]['payment_status']=='REFUNDED'


def test_payment_idempotence_ownership_and_disabled(client, monkeypatch, tmp_path):
    h=login(client);o=topup(client,h,key='unique-request')
    duplicate=client.post('/api/payments/topups',headers=h,json={'amount':500,'request_key':'unique-request'})
    assert duplicate.json()['order_id']==o['order_id']
    assert client.post('/api/payments/'+o['order_id']+'/demo-confirm',headers=h).status_code==200
    assert client.get('/api/wallet',headers=h).json()['balance']==500
    assert client.post('/api/payments/topups',headers=h,json={'amount':600,'request_key':'unique-request'}).status_code==409
    admin=login(client,'admin@example.edu','admin123')
    client.post('/api/admin/users',headers=admin,json={'email':'other2@example.edu','password':'another123'})
    other=login(client,'other2@example.edu','another123')
    assert client.post('/api/payments/'+o['order_id']+'/demo-confirm',headers=other).status_code==403
    pending=client.post('/api/payments/topups',headers=h,json={'amount':50,'request_key':'cancel-request'}).json()
    assert client.post('/api/payments/'+pending['order_id']+'/cancel',headers=h).status_code==200
    assert client.post('/api/payments/'+pending['order_id']+'/demo-confirm',headers=h).status_code==409
    assert client.get('/api/admin/payments',headers=h).status_code==403
    monkeypatch.setenv('PAYMENT_MODE','disabled')
    from fastapi.testclient import TestClient
    with TestClient(create_app('sqlite:///'+str(tmp_path/'disabled.db'))) as c:
        hh=login(c)
        assert c.post('/api/payments/topups',headers=hh,json={'amount':50,'request_key':'test-disabled'}).status_code==503


def test_accounts_and_layout_management(client):
    admin=login(client,'admin@example.edu','admin123');student=login(client)
    payload={'email':'new@example.edu','password':'student456'}
    assert client.post('/api/admin/users',headers=student,json=payload).status_code==403
    assert client.post('/api/admin/users',headers=admin,json=payload).status_code==201
    assert client.post('/api/admin/users',headers=admin,json=payload).status_code==409
    assert client.post('/api/admin/users',headers=admin,json={'email':'bad','password':'12345678'}).status_code==422
    new=login(client,'new@example.edu','student456')
    assert client.get('/api/wallet',headers=new).json()['balance']==0
    space=room(client);sid=space['space_id'];seat=space['seats'][0]
    assert client.patch('/api/admin/spaces/'+sid,headers=admin,json={'name':'Updated','location':'Campus 2F','equipment':'Wi-Fi, Whiteboard','hourly_rate':70}).status_code==200
    assert client.patch('/api/admin/spaces/'+sid,headers=admin,json={'grid_rows':1,'grid_cols':1,'aisle_col':0,'entrance_col':1}).status_code==422
    assert client.patch('/api/admin/spaces/'+sid,headers=admin,json={'capacity':4}).status_code==422
    assert client.patch('/api/admin/seats/'+seat['seat_id'],headers=admin,json={'row_no':2,'col_no':1,'seat_code':'EDITED'}).status_code==200
    assert client.post('/api/admin/spaces/'+sid+'/seats',headers=admin,json={'seat_code':'DUPPOS','row_no':2,'col_no':1}).status_code==409
    assert client.patch('/api/admin/seats/'+seat['seat_id'],headers=admin,json={'col_no':4}).status_code==422
    assert client.patch('/api/admin/spaces/'+sid,headers=admin,json={'name':None}).status_code==422
    start,end=future_slot()
    data=client.get('/api/spaces?date='+start[:10]+'&start_time=10:00&end_time=11:00',headers=student).json()
    updated=next(s for s in data if s['space_id']==sid)
    assert updated['hourly_rate']==70 and updated['equipment']=='Wi-Fi, Whiteboard'
    schedule=client.get('/api/spaces/'+sid+'/schedule?date='+start[:10],headers=student)
    assert schedule.status_code==200,schedule.text
    assert schedule.json()['opening_hours']['open_time']=='08:00'
    assert 'email' not in schedule.text


def test_checkin_rules_and_privacy(client):
    h=login(client);space=room(client);topup(client,h)
    r=book(client,h,space).json();rid=r['reservation_id']
    assert client.post('/api/reservations/'+rid+'/check-in',headers=h).status_code==409
    assert client.post('/api/reservations/'+rid+'/check-out',headers=h).status_code==409
    admin=login(client,'admin@example.edu','admin123')
    client.post('/api/admin/users',headers=admin,json={'email':'other3@example.edu','password':'student333'})
    other=login(client,'other3@example.edu','student333')
    assert client.get('/api/reservations/'+rid,headers=other).status_code==403
    assert client.post('/api/reservations/'+rid+'/check-in',headers=other).status_code==403
    # Move fixture into the live check-in window without creating an invalid past booking.
    with client.app.state.SessionLocal() as db:
        row=db.get(Reservation,uuid.UUID(rid));row.start_at=datetime.now(TAIPEI)+timedelta(minutes=5);row.end_at=row.start_at+timedelta(hours=1);db.commit()
    assert client.get('/api/reservations/'+rid,headers=h).json()['can_check_in']
    assert client.post('/api/reservations/'+rid+'/check-in',headers=h).status_code==200
    assert client.post('/api/reservations/'+rid+'/check-in',headers=h).status_code==409
    assert client.delete('/api/reservations/'+rid,headers=h).status_code==409
    assert client.get('/api/reservations/'+rid,headers=h).json()['can_cancel'] is False
    assert client.post('/api/reservations/'+rid+'/check-out',headers=h).status_code==200
    assert client.post('/api/reservations/'+rid+'/check-out',headers=h).status_code==409
    assert client.get('/api/reservations/'+rid,headers=h).json()['attendance_status']=='CHECKED_OUT'


def test_schedule_marks_reserved_and_cancel_releases(client):
    h=login(client);s=room(client);topup(client,h);r=book(client,h,s).json();start,_=future_slot()
    url='/api/spaces/'+s['space_id']+'/schedule?date='+start[:10]
    assert len(client.get(url,headers=h).json()['seats'][0]['booked'])==1
    assert client.delete('/api/reservations/'+r['reservation_id'],headers=h).status_code==200
    assert client.get(url,headers=h).json()['seats'][0]['booked']==[]


def test_existing_database_migration_preserves_records(tmp_path):
    from sqlalchemy import create_engine
    engine=create_engine('sqlite:///'+str(tmp_path/'old.db'))
    with engine.begin() as c:
        c.execute(text('CREATE TABLE study_spaces (space_id CHAR(32) PRIMARY KEY, name VARCHAR(100) NOT NULL, location VARCHAR(255) NOT NULL, is_active BOOLEAN NOT NULL)'))
        c.execute(text('CREATE TABLE seats (seat_id CHAR(32) PRIMARY KEY, space_id CHAR(32) NOT NULL, seat_code VARCHAR(30) NOT NULL, is_active BOOLEAN NOT NULL)'))
        c.execute(text('INSERT INTO study_spaces VALUES (:id,\'Existing\',\'Campus\',1)'),{'id':'a'*32})
        for n in range(2): c.execute(text('INSERT INTO seats VALUES (:id,:space,:code,1)'),{'id':str(n+1)*32,'space':'a'*32,'code':f'A{n}'})
    app=create_app('sqlite:///'+str(tmp_path/'old.db'),seed=False)
    with app.state.SessionLocal() as db:
        space=db.get(StudySpace,uuid.UUID('a'*32));assert space.name=='Existing' and space.hourly_rate==0
        seats=db.scalars(select(Seat)).all();assert [(s.row_no,s.col_no) for s in seats]==[(1,1),(1,2)]
    again=create_app('sqlite:///'+str(tmp_path/'old.db'),seed=False)
    with again.state.SessionLocal() as db: assert len(db.scalars(select(Seat)).all())==2


def test_quote_price_snapshot_and_rounding(client):
    h=login(client);admin=login(client,'admin@example.edu','admin123');s=room(client);topup(client,h)
    assert book(client,h,s,expected_amount=1).status_code==409
    assert client.get('/api/wallet',headers=h).json()['balance']==500
    start,end=future_slot();end=start[:11]+'10:01:01+08:00'
    r=client.post('/api/reservations',headers=h,json={'seat_id':s['seats'][0]['seat_id'],'start_at':start,'end_at':end,'expected_amount':2})
    assert r.status_code==201,r.text
    assert r.json()['amount']==2
    client.patch('/api/admin/spaces/'+s['space_id'],headers=admin,json={'hourly_rate':120})
    assert client.get('/api/reservations/'+r.json()['reservation_id'],headers=h).json()['amount']==2
    assert client.delete('/api/reservations/'+r.json()['reservation_id'],headers=h).status_code==200
    assert client.get('/api/wallet',headers=h).json()['balance']==500


def test_inactive_seat_and_started_cancellation(client):
    h=login(client);admin=login(client,'admin@example.edu','admin123');s=room(client);topup(client,h)
    client.patch('/api/admin/seats/'+s['seats'][0]['seat_id'],headers=admin,json={'is_active':False})
    assert book(client,h,s).status_code==404
    client.patch('/api/admin/seats/'+s['seats'][0]['seat_id'],headers=admin,json={'is_active':True})
    r=book(client,h,s).json()
    with client.app.state.SessionLocal() as db:
        row=db.get(Reservation,uuid.UUID(r['reservation_id']));row.start_at=datetime.now(TAIPEI)-timedelta(minutes=1);row.end_at=datetime.now(TAIPEI)+timedelta(minutes=30);db.commit()
    assert client.get('/api/reservations/'+r['reservation_id'],headers=h).json()['can_cancel'] is False
    assert client.delete('/api/reservations/'+r['reservation_id'],headers=h).status_code==409
