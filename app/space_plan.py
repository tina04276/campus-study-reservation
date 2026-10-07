"""Concept learning centre. No claim of measured building dimensions."""
from sqlalchemy import select
from .models import StudySpace, Seat

PLAN = {
    'GENERAL': dict(zone='A', title='安靜自習區', location='學習中心 1F · 西北側 A 區', count=24, rows=4, cols=7, aisle=4,
                    equipment='Wi-Fi、每席插座、閱讀燈、桌面隔板', purpose='個人閱讀、考試準備；保持安靜'),
    'VIP': dict(zone='B', title='專注 VIP 區', location='學習中心 1F · 西南側 B 區', count=12, rows=2, cols=7, aisle=4,
                equipment='Wi-Fi、每席插座、閱讀燈、高隔板、加寬桌面、人體工學椅', purpose='長時間個人學習；保持安靜'),
    'ROOM2': dict(zone='C', title='雙人討論區', location='學習中心 1F · 東北側 C 區', count=4, rows=1, cols=5, aisle=3,
                  equipment='Wi-Fi、插座、雙人桌、白板、可關閉房門', purpose='兩人討論；每間最多 2 人，整間預約'),
    'ROOM4': dict(zone='D', title='四人協作區', location='學習中心 1F · 東南側 D 區', count=2, rows=1, cols=3, aisle=3,
                  equipment='Wi-Fi、插座、四人桌、白板、顯示器、可關閉房門', purpose='小組協作；每間最多 4 人，整間預約'),
}
NAMES = {'GENERAL':'一般自習區（示範）','VIP':'VIP 自習區（示範）','ROOM2':'雙人研究室（示範）','ROOM4':'四人研究室（示範）'}
LEGACY_COUNTS = {'GENERAL':8,'VIP':6,'ROOM2':2,'ROOM4':2}
RATES = {'GENERAL':20,'VIP':35,'ROOM2':60,'ROOM4':100}


def plan_for(space):
    """Link the concept map only to known plan spaces, never unrelated rooms."""
    p = PLAN.get(space.category)
    return dict(p) if p and space.name == NAMES[space.category] else None


def apply_space_plan(db):
    """Upgrade untouched v2 demos in place; keep IDs, bookings and admin edits."""
    from .extensions import SchemaVersion
    if db.get(SchemaVersion, 'v3-space-plan'): return
    for category, p in PLAN.items():
        space = db.scalar(select(StudySpace).where(StudySpace.name == NAMES[category], StudySpace.category == category))
        if not space: continue
        seats = db.scalars(select(Seat).where(Seat.space_id == space.space_id).order_by(Seat.seat_code)).all()
        old_equipment = 'Wi-Fi、插座、閱讀燈' + ('、白板' if space.capacity > 1 else '')
        if not (space.location == '校園學習中心 · 示範配置' and space.is_active and
                space.hourly_rate == RATES[category] and space.equipment == old_equipment and
                (space.grid_rows,space.grid_cols,space.aisle_col,space.entrance_col) == (4,7,4,4) and
                len(seats) == LEGACY_COUNTS[category] and
                all(s.is_active and s.seat_code == f'{category}-{i+1:02}' and
                    (s.row_no,s.col_no) == (i//6+1,[1,2,3,5,6,7][i%6]) for i,s in enumerate(seats))):
            continue
        # Existing occupied coordinates fit the new layouts and never move.
        space.location = p['location']; space.equipment = p['equipment']
        space.grid_rows = p['rows']; space.grid_cols = p['cols']
        space.aisle_col = space.entrance_col = p['aisle']
        occupied = {(s.row_no,s.col_no) for s in seats}
        free = [(r,c) for r in range(1,p['rows']+1) for c in range(1,p['cols']+1)
                if c != p['aisle'] and (r,c) not in occupied]
        for i,(r,c) in enumerate(free[:p['count']-len(seats)],start=len(seats)+1):
            db.add(Seat(space_id=space.space_id,seat_code=f'{category}-{i:02}',row_no=r,col_no=c))
    db.add(SchemaVersion(version='v3-space-plan'))
