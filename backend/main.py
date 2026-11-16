from contextlib import asynccontextmanager
import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .database import ROOT, get_data_dir, make_engine, migrate
from .domain import SLOT_KEYS, SLOTS, compare, equipment_type, label
from .models import Account, AuditEvent, Character, CharacterCalculation, Cycle, CycleBaseStat, EquipmentConfig, RaidDrop, SlotSetting, Source, Template, WarehouseStack, Workspace, WornEquipment
from .services import BASE_STAT_MATCH, new_id, dump, require, audit, create_template, create_config, choices_for, template_snapshot, template_view
from .inventory import make_inventory_router
from .equipment_batch import make_batch_router
from .warehouse_batch import make_warehouse_batch_router
from .cycle_data import make_cycle_data_router
from .growth import make_growth_router, growth_reports, load_growth_slots
from .data_management import make_data_router
from .creation import begin_creation, finish_creation
from .management import edit_entity, make_management_router
from .raid_cd import make_raid_router, raid_state, reconcile, sync_raid_cd
from .schemas import AccountPreferences, AccountPreferencesPatch, ActiveCycle, CycleInput, EquipInput, ManagementPatch, MutationInput, NamedInput, RaidCheckInput, SourceInput, TemplateInput


def create_app(data_dir: Path | None = None):
    engine = make_engine(data_dir or get_data_dir())

    @asynccontextmanager
    async def lifespan(_):
        migrate(engine)
        with Session(engine) as session, session.begin():
            if session.get(Workspace, 1) is None:
                session.add(Workspace(id=1, active_cycle_id=None))
        sync_raid_cd(engine)
        async def tick():
            while True:
                await asyncio.sleep(20)
                try:
                    await asyncio.to_thread(sync_raid_cd, engine)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Raid CD reconciliation failed; retrying on next tick')
        task = asyncio.create_task(tick())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            engine.dispose()

    app = FastAPI(title="剑网三坦克装备管理", version="0.1.15", lifespan=lifespan)
    app.state.engine = engine

    def database():
        with Session(engine) as session:
            yield session

    DB = Depends(database)
    def write_database():
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session
    WRITE = Depends(write_database)

    @app.exception_handler(IntegrityError)
    async def integrity_error(_, __):
        return JSONResponse(status_code=409, content={"detail": "数据约束冲突，操作未保存，请刷新检查"})

    @app.exception_handler(OperationalError)
    async def database_error(_, error):
        if "locked" in str(error).lower():
            return JSONResponse(status_code=409, content={"detail": "数据库正忙，操作未保存，请稍后重试"})
        return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请检查应用日志与 data 目录"})

    @app.get("/api/health")
    def health(session: Session = DB):
        session.execute(text("SELECT 1"))
        return {"status": "ok", "version": app.version}

    @app.get("/api/workspace")
    def workspace(session: Session = DB):
        sync_raid_cd(engine)
        return {
            "active_cycle_id": session.get(Workspace, 1).active_cycle_id,
            "cycles": [dump(c) for c in session.scalars(select(Cycle).order_by(Cycle.name, Cycle.id))],
            "accounts": [dump(a) for a in session.scalars(select(Account).order_by(Account.sort_order, Account.name, Account.id))],
            "slots": [{"key": key, "name": name} for key, name in SLOTS],
            "raid_cd": raid_state(session),
        }

    @app.put("/api/workspace/active-cycle")
    def active_cycle(payload: ActiveCycle, session: Session = DB):
        cycle = require(session, Cycle, payload.cycle_id)
        if cycle.archived:
            raise HTTPException(409, '请先恢复归档周期，再设为当前')
        workspace = session.get(Workspace, 1)
        before = dump(workspace)
        workspace.active_cycle_id = payload.cycle_id
        audit(session, "select_cycle", "1", before, dump(workspace))
        session.commit()
        return dump(workspace)

    @app.post("/api/cycles", status_code=201)
    def add_cycle(payload: CycleInput, session: Session = WRITE):
        fingerprint, replay = begin_creation(session, 'cycle', payload)
        if replay is not None:
            return replay
        cycle = Cycle(id=new_id(), name=payload.name, archived=False, socket_exempt_types=payload.socket_exempt_types or '')
        session.add(cycle)
        session.flush()
        workspace = session.get(Workspace, 1)
        workspace.active_cycle_id = cycle.id
        audit(session, "create_cycle", cycle.id, None, dump(cycle))
        return finish_creation(session, payload, fingerprint, dump(cycle))

    @app.put("/api/cycles/{cycle_id}")
    def edit_cycle(cycle_id: str, payload: CycleInput, session: Session = WRITE):
        cycle = require(session, Cycle, cycle_id)
        if cycle.archived:
            raise HTTPException(409, '归档周期只读，请先恢复再编辑')
        if payload.socket_exempt_types is not None and payload.expected_version is None:
            raise HTTPException(409, '修改比较规则需要周期版本，请重新读取')
        if payload.expected_version is not None and cycle.version != payload.expected_version:
            raise HTTPException(409, '周期配置已变化，请重新读取后编辑')
        before = dump(cycle)
        cycle.name = payload.name
        if payload.socket_exempt_types is not None:
            cycle.socket_exempt_types = payload.socket_exempt_types
        cycle.version += 1
        operation = "edit_cycle_socket_rules" if payload.socket_exempt_types is not None else "rename_cycle"
        audit(session, operation, cycle.id, before, dump(cycle))
        session.commit()
        return dump(cycle)

    @app.post("/api/accounts", status_code=201)
    def add_account(payload: NamedInput, session: Session = WRITE):
        fingerprint, replay = begin_creation(session, 'account', payload)
        if replay is not None:
            return replay
        account = Account(id=new_id(), **payload.model_dump(exclude={'expected_version', 'request_id'}), target_grade=None, comparison_mode="strict")
        session.add(account)
        session.flush()
        audit(session, "create_account", account.id, None, dump(account), account.id)
        return finish_creation(session, payload, fingerprint, dump(account))

    @app.put("/api/accounts/{account_id}")
    def edit_account(account_id: str, payload: NamedInput, session: Session = WRITE):
        account = require(session, Account, account_id)
        if payload.expected_version is None:
            raise HTTPException(409, '旧版管理表单缺少版本，请刷新页面后核对再保存')
        return edit_entity(session, account, ManagementPatch.model_validate(payload.model_dump(exclude={'request_id'})), account.id)

    @app.put("/api/accounts/{account_id}/preferences")
    def preferences(account_id: str, payload: AccountPreferences, session: Session = DB):
        return save_preferences(account_id, payload.model_dump(), session)

    @app.patch("/api/accounts/{account_id}/preferences")
    def patch_preferences(account_id: str, payload: AccountPreferencesPatch, session: Session = DB):
        return save_preferences(account_id, payload.model_dump(exclude_unset=True), session)

    def save_preferences(account_id, values, session):
        if not values:
            return dump(require(session, Account, account_id))
        account = session.scalar(update(Account).where(Account.id == account_id).values(**values).returning(Account))
        if account is None:
            raise HTTPException(404, "记录不存在，请刷新后重试")
        result = dump(account)
        session.commit()
        return result

    def account_character(session, account_id, character_id):
        account = require(session, Account, account_id)
        character = require(session, Character, character_id)
        if character.account_id != account_id:
            raise HTTPException(404, "此账号下没有该角色")
        return account, character

    @app.get("/api/accounts/{account_id}/characters")
    def characters(account_id: str, session: Session = DB):
        sync_raid_cd(engine)
        require(session, Account, account_id)
        return [dump(c) for c in session.scalars(select(Character).where(Character.account_id == account_id).order_by(Character.sort_order, Character.name, Character.id))]

    @app.post("/api/accounts/{account_id}/characters", status_code=201)
    def add_character(account_id: str, payload: NamedInput, session: Session = WRITE):
        fingerprint, replay = begin_creation(session, 'character:' + account_id, payload)
        if replay is not None:
            return replay
        account = require(session, Account, account_id)
        if account.archived:
            raise HTTPException(409, "请先恢复账号")
        character = Character(id=new_id(), account_id=account_id, **payload.model_dump(exclude={'request_id', 'expected_version'}))
        session.add(character)
        session.flush()
        session.add(CharacterCalculation(character_id=character.id))
        for slot in SLOT_KEYS:
            session.add(WornEquipment(character_id=character.id, slot=slot, config_id=None, version=0))
            session.add(SlotSetting(character_id=character.id, slot=slot, refine_level=None, stone_state="unknown", stone_level=None))
        audit(session, "create_character", character.id, None, dump(character), account_id)
        return finish_creation(session, payload, fingerprint, dump(character))

    @app.put("/api/accounts/{account_id}/characters/{character_id}")
    def edit_character(account_id: str, character_id: str, payload: NamedInput, session: Session = WRITE):
        _, character = account_character(session, account_id, character_id)
        if payload.expected_version is None:
            raise HTTPException(409, '旧版管理表单缺少版本，请刷新页面后核对再保存')
        return edit_entity(session, character, ManagementPatch.model_validate(payload.model_dump(exclude={'request_id'})), account_id)

    @app.put("/api/accounts/{account_id}/characters/{character_id}/raid-checks/{difficulty}")
    def set_raid_check(account_id: str, character_id: str, difficulty: Literal['10', '25pt', '25yx'],
                       payload: RaidCheckInput, session: Session = WRITE):
        setting = reconcile(session)
        account, character = account_character(session, account_id, character_id)
        if account.archived or character.archived:
            raise HTTPException(409, "请先恢复账号和角色")
        state = raid_state(session, setting)
        if state['clock_warning']:
            raise HTTPException(409, '服务时间异常，请先核对服务器时钟，进度未修改')
        period = state['period_10' if difficulty == '10' else 'period_25']
        if (payload.period is not None and payload.period != period) or (setting.enabled and payload.period is None):
            session.commit()
            raise HTTPException(409, '副本 CD 已变化，请读取本期进度后核对')
        if payload.expected_version is not None and character.raid_version != payload.expected_version:
            session.commit()
            raise HTTPException(409, '副本进度已变化，请读取最新记录后核对')
        before = dump(character)
        if getattr(character, f'raid_{difficulty}') != payload.cleared:
            setattr(character, f'raid_{difficulty}', payload.cleared)
            character.raid_version += 1
            audit(session, 'raid_check', character_id, before, dump(character), account_id)
        session.commit()
        return {"cleared": payload.cleared, 'raid_version': character.raid_version, 'period': period}

    @app.get("/api/sources")
    def all_sources(session: Session = DB):
        return [dump(source) for source in session.scalars(select(Source).order_by(Source.name, Source.id))]

    @app.get("/api/cycles/{cycle_id}/sources")
    def sources(cycle_id: str, session: Session = DB):
        require(session, Cycle, cycle_id)
        return [dump(s) for s in session.scalars(select(Source).where(Source.cycle_id == cycle_id).order_by(Source.name, Source.id))]

    @app.post("/api/cycles/{cycle_id}/sources", status_code=201)
    def add_source(cycle_id: str, payload: SourceInput, session: Session = WRITE):
        fingerprint, replay = begin_creation(session, 'source:' + cycle_id, payload)
        if replay is not None:
            return replay
        cycle = require(session, Cycle, cycle_id)
        if cycle.archived:
            raise HTTPException(409, '归档周期只读，请先恢复再编辑')
        if payload.expected_version is not None and cycle.version != payload.expected_version:
            raise HTTPException(409, '周期配置已变化，请重新读取后编辑')
        source = Source(id=new_id(), cycle_id=cycle_id, **payload.model_dump(exclude={'expected_version', 'request_id'}))
        session.add(source)
        session.flush()
        session.execute(update(Cycle).where(Cycle.id == cycle_id).values(version=Cycle.version + 1))
        audit(session, "create_source", source.id, None, dump(source))
        return finish_creation(session, payload, fingerprint, dump(source))

    @app.put("/api/cycles/{cycle_id}/sources/{source_id}")
    def edit_source(cycle_id: str, source_id: str, payload: SourceInput, session: Session = WRITE):
        cycle = require(session, Cycle, cycle_id)
        if cycle.archived:
            raise HTTPException(409, '归档周期只读，请先恢复再编辑')
        if payload.expected_version is not None and cycle.version != payload.expected_version:
            raise HTTPException(409, '周期配置已变化，请重新读取后编辑')
        source = require(session, Source, source_id)
        if source.cycle_id != cycle_id:
            raise HTTPException(404, "此周期下没有该来源")
        if source.actual_grade != payload.actual_grade and session.scalar(select(Template.id).where(Template.source_id == source_id).limit(1)):
            raise HTTPException(409, '已有装备引用此来源，品级已锁定。可新增来源并更正装备。')
        before = dump(source)
        for key, value in payload.model_dump(exclude={'expected_version', 'request_id'}).items():
            setattr(source, key, value)
        session.execute(update(Cycle).where(Cycle.id == cycle_id).values(version=Cycle.version + 1))
        audit(session, "edit_source", source.id, before, dump(source))
        session.commit()
        return dump(source)

    @app.delete("/api/cycles/{cycle_id}/sources/{source_id}")
    def delete_source(cycle_id: str, source_id: str, expected_version: int = Query(ge=0), session: Session = WRITE):
        cycle = require(session, Cycle, cycle_id)
        if cycle.archived:
            raise HTTPException(409, '归档周期只读，请先恢复再编辑')
        if cycle.version != expected_version:
            raise HTTPException(409, '周期配置已变化，请重新读取后编辑')
        source = require(session, Source, source_id)
        if source.cycle_id != cycle_id:
            raise HTTPException(404, '此周期下没有该来源')
        if session.scalar(select(Template.id).where(Template.source_id == source_id).limit(1)):
            raise HTTPException(409, '已有装备引用此来源，可停用以从录入列表隐藏')
        audit(session, 'delete_source', source_id, dump(source), None)
        session.delete(source)
        cycle.version += 1
        session.commit()
        return {'deleted': True}

    @app.get("/api/templates")
    def templates(equipment_type: str | None = None, include_history: bool = True, session: Session = DB):
        query = (select(Template, CycleBaseStat.constitution, Cycle.socket_exempt_types)
                 .join(Source, Source.id == Template.source_id).join(Cycle, Cycle.id == Source.cycle_id)
                 .outerjoin(CycleBaseStat, BASE_STAT_MATCH))
        if equipment_type:
            query = query.where(Template.equipment_type == equipment_type)
        if not include_history:
            held = select(WornEquipment.config_id).where(WornEquipment.config_id.is_not(None)).union(
                select(WarehouseStack.config_id).where(WarehouseStack.quantity > 0), select(RaidDrop.config_id))
            query = query.where(Template.id.in_(select(EquipmentConfig.template_id).where(EquipmentConfig.id.in_(held))))
        return [template_snapshot(template, base, available) for template, base, available in session.execute(
            query.order_by(Source.actual_grade.desc(), Template.id))]

    @app.post("/api/templates", status_code=201)
    def add_template(payload: TemplateInput, session: Session = WRITE):
        template = create_template(session, payload)
        audit(session, "save_template", template.id, None, dump(template))
        session.commit()
        return template_view(session, template)

    @app.get("/api/accounts/{account_id}/matrix")
    def matrix(account_id: str, include_archived: bool = False, session: Session = DB):
        sync_raid_cd(engine)
        account = require(session, Account, account_id)
        query = select(Character).where(Character.account_id == account_id).order_by(Character.sort_order, Character.name, Character.id)
        if not include_archived:
            query = query.where(Character.archived.is_(False))
        all_characters = list(session.scalars(query))
        characters = [character for character in all_characters if not character.archived]
        records = load_growth_slots(session, [character.id for character in characters])
        cells = {}
        for row in records:
            worn, template, config = row.WornEquipment, row.Template, row.EquipmentConfig
            equipment = template_snapshot(template, row.base, row.socket_exempt_types) if template else None
            comparison = compare(template.actual_grade if template else None,
                                 template.has_constitution_socket if template else False,
                                 template.green_stats_ok if template else False,
                                 account.target_grade, account.comparison_mode, equipment['socket_applicable'] if equipment else True)
            cells.setdefault(worn.character_id, {})[worn.slot] = {
                "equipment": equipment, "version": worn.version, **comparison,
                "choices": choices_for(config) if config else None,
                "label": label(template.short_label, template.has_constitution_socket, template.green_stats_ok, equipment['socket_applicable']) if template else "未装备",
            }
        reports = growth_reports(session, characters, records)
        result = {"account": dump(account), "characters": [dump(c) for c in characters], "cells": cells, "growth": {character_id: {key: report[key] for key in ("known_subtotal", "equipment_total", "verified_total", "buffs")} for character_id, report in reports.items()},
                "constitution": {"status": "per_character", "message": "体质与层数按角色计算，详见 growth 字段", "total": None, "stacks": None}}
        if include_archived:
            result["all_characters"] = [dump(character) for character in all_characters]
        return result

    def writable_slot(session, account_id, character_id, slot):
        account, character = account_character(session, account_id, character_id)
        if account.archived or character.archived:
            raise HTTPException(409, "请先恢复账号和角色，再修改装备")
        if slot not in SLOT_KEYS:
            raise HTTPException(422, "装备部位无效")
        return require(session, WornEquipment, (character_id, slot))

    def save_worn(session, account_id, worn, config_id, expected_version, intent=None, fingerprint=None):
        before = dump(worn)
        result = session.execute(update(WornEquipment).where(
            WornEquipment.character_id == worn.character_id, WornEquipment.slot == worn.slot,
            WornEquipment.version == expected_version,
        ).values(config_id=config_id, version=WornEquipment.version + 1).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(409, "此格装备已在其他页面修改，请关闭编辑并刷新后重试")
        session.expire(worn)
        audit(session, "replace_equipment", f"{worn.character_id}:{worn.slot}", before, dump(worn), account_id)
        result = {"version": worn.version}
        if intent is not None:
            return finish_creation(session, intent, fingerprint, result)
        session.commit()
        return result

    @app.put("/api/accounts/{account_id}/characters/{character_id}/equipment/{slot}")
    def equip(account_id: str, character_id: str, slot: str, payload: EquipInput, session: Session = WRITE):
        fingerprint, replay = begin_creation(session, f'equipment:{account_id}:{character_id}:{slot}', payload)
        if replay is not None:
            return replay
        worn = writable_slot(session, account_id, character_id, slot)
        config = session.get(EquipmentConfig, worn.config_id) if worn.config_id else None
        previous = session.get(Template, config.template_id) if config else None
        current_source = previous.source_id if previous else None
        template = require(session, Template, payload.template_id) if payload.template_id else create_template(session, payload.equipment, current_source)
        if not template.source.active and template.source_id != current_source:
            raise HTTPException(409, '此来源已停用，请选择仍在使用的来源')
        if template.equipment_type != equipment_type(slot):
            raise HTTPException(422, "装备模板与角色部位不匹配")
        config = create_config(session, template, payload.choices)
        return save_worn(session, account_id, worn, config.id, payload.expected_version, payload, fingerprint)

    @app.delete("/api/accounts/{account_id}/characters/{character_id}/equipment/{slot}")
    def clear_equipment(account_id: str, character_id: str, slot: str, payload: MutationInput | None = None, expected_version: int = Query(ge=0), session: Session = WRITE):
        fingerprint, replay = begin_creation(session, f'clear_equipment:{account_id}:{character_id}:{slot}:{expected_version}', payload) if payload else (None, None)
        if replay is not None:
            return replay
        worn = writable_slot(session, account_id, character_id, slot)
        return save_worn(session, account_id, worn, None, expected_version, payload, fingerprint)

    app.include_router(make_inventory_router(engine, database))
    app.include_router(make_warehouse_batch_router(engine))
    app.include_router(make_batch_router(engine))
    app.include_router(make_cycle_data_router(engine, database))
    app.include_router(make_growth_router(engine, database))
    app.include_router(make_data_router(engine, database))
    app.include_router(make_management_router(engine))
    app.include_router(make_raid_router(engine))

    @app.get("/api/{path:path}", include_in_schema=False)
    def unknown_api(path: str):
        raise HTTPException(404, "接口不存在")

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        if not (dist / "index.html").is_file():
            return JSONResponse(status_code=503, content={"detail": "前端尚未构建，请先执行 README 中的构建步骤"})
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
