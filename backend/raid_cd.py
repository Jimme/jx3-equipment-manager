"""One-container, durable raid CD reconciliation in Asia/Shanghai."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .models import Character, RaidCDSetting
from .schemas import RaidCDInput
from .services import audit, dump, require

SHANGHAI = ZoneInfo('Asia/Shanghai')


def utc_now():
    return datetime.now(timezone.utc)


def period_boundaries(now, ten):
    local = now.astimezone(SHANGHAI)
    day = local.replace(hour=6, minute=0, second=0, microsecond=0)
    candidates = [day + timedelta(days=offset) for offset in range(-7, 8)]
    candidates = [value for value in candidates if value.weekday() in ((0, 4) if ten else (0,))]
    return (max(value for value in candidates if value <= local).astimezone(timezone.utc).isoformat(),
            min(value for value in candidates if value > local).astimezone(timezone.utc).isoformat())


def reconcile(session, now=None):
    now = now or utc_now()
    setting = require(session, RaidCDSetting, 1)
    current_10, _ = period_boundaries(now, True)
    current_25, _ = period_boundaries(now, False)
    if not setting.enabled:
        return setting
    if (setting.changed_at and now < datetime.fromisoformat(setting.changed_at)) or any(
        cursor and datetime.fromisoformat(cursor) > now for cursor in (setting.cursor_10, setting.cursor_25)
    ):
        return setting
    ten, twenty_five = setting.cursor_10 != current_10, setting.cursor_25 != current_25
    if ten or twenty_five:
        before = dump(setting)
        fields = {'raid_version': Character.raid_version + 1}
        conditions = []
        if ten:
            fields['raid_10'] = False
            conditions.append(Character.raid_10.is_(True))
        if twenty_five:
            fields.update(raid_25pt=False, raid_25yx=False)
            conditions.extend([Character.raid_25pt.is_(True), Character.raid_25yx.is_(True)])
        count = session.execute(update(Character).where(or_(*conditions)).values(**fields)).rowcount
        setting.cursor_10, setting.cursor_25 = current_10, current_25
        setting.changed_at = now.isoformat()
        audit(session, 'raid_cd_reset', '1', before, {**dump(setting), 'characters_changed': count})
    return setting


def sync_raid_cd(engine):
    with Session(engine.execution_options(sqlite_immediate=True)) as session:
        reconcile(session)
        session.commit()


def raid_state(session, setting=None, now=None):
    now = now or utc_now()
    setting = setting or require(session, RaidCDSetting, 1)
    ten, next_10 = period_boundaries(now, True)
    twenty_five, next_25 = period_boundaries(now, False)
    warning = bool(setting.enabled and ((setting.changed_at and now < datetime.fromisoformat(setting.changed_at)) or
                   any(cursor and datetime.fromisoformat(cursor) > now for cursor in (setting.cursor_10, setting.cursor_25))))
    return {**dump(setting), 'timezone': 'Asia/Shanghai', 'next_10': next_10, 'next_25': next_25,
            'period_10': f'{setting.version}:{ten}' if setting.enabled else f'manual:{setting.version}',
            'period_25': f'{setting.version}:{twenty_five}' if setting.enabled else f'manual:{setting.version}',
            'clock_warning': warning}


def make_raid_router(engine):
    router = APIRouter(prefix='/api/raid-cd')

    def database():
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session

    @router.get('')
    def get_state(session: Session = Depends(database)):
        setting = reconcile(session)
        result = raid_state(session, setting)
        session.commit()
        return result

    @router.put('')
    def configure(payload: RaidCDInput, session: Session = Depends(database)):
        setting = reconcile(session)
        if setting.version != payload.expected_version:
            raise HTTPException(409, '自动重置设置已变化，请读取最新状态后核对')
        if setting.enabled != payload.enabled:
            before = dump(setting)
            now = utc_now()
            setting.enabled = payload.enabled
            setting.version += 1
            setting.cursor_10 = period_boundaries(now, True)[0]
            setting.cursor_25 = period_boundaries(now, False)[0]
            setting.changed_at = now.isoformat()
            audit(session, 'raid_cd_configure', '1', before, dump(setting))
        result = raid_state(session, setting)
        session.commit()
        return result

    return router
