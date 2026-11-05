from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .database import ROOT, get_data_dir, make_engine, migrate
from .domain import SLOT_KEYS, SLOTS, compare, equipment_type, label
from .models import Account, AuditEvent, Character, CharacterCalculation, Cycle, EquipmentConfig, SlotSetting, Source, Template, Workspace, WornEquipment
from .services import new_id, dump, require, audit, create_template, create_config, choices_for, template_view
from .inventory import make_inventory_router
from .cycle_data import make_cycle_data_router
from .growth import make_growth_router, growth_report
from .data_management import make_data_router
from .schemas import AccountPreferences, ActiveCycle, CycleInput, EquipInput, NamedInput, SourceInput, TemplateInput


def create_app(data_dir: Path | None = None):
    engine = make_engine(data_dir or get_data_dir())

    @asynccontextmanager
    async def lifespan(_):
        migrate(engine)
        with Session(engine) as session, session.begin():
            if session.get(Workspace, 1) is None:
                session.add(Workspace(id=1, active_cycle_id=None))
        yield
        engine.dispose()

    app = FastAPI(title="剑网三坦克装备管理", version="0.1.4", lifespan=lifespan)
    app.state.engine = engine

    def database():
        with Session(engine) as session:
            yield session

    DB = Depends(database)

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
        return {"status": "ok", "version": "0.1.4"}

    @app.get("/api/workspace")
    def workspace(session: Session = DB):
        return {
            "active_cycle_id": session.get(Workspace, 1).active_cycle_id,
            "cycles": [dump(c) for c in session.scalars(select(Cycle).order_by(Cycle.name, Cycle.id))],
            "accounts": [dump(a) for a in session.scalars(select(Account).order_by(Account.sort_order, Account.name, Account.id))],
            "slots": [{"key": key, "name": name} for key, name in SLOTS],
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
    def add_cycle(payload: CycleInput, session: Session = DB):
        cycle = Cycle(id=new_id(), name=payload.name, archived=False)
        session.add(cycle)
        session.flush()
        workspace = session.get(Workspace, 1)
        workspace.active_cycle_id = cycle.id
        audit(session, "create_cycle", cycle.id, None, dump(cycle))
        session.commit()
        return dump(cycle)

    @app.put("/api/cycles/{cycle_id}")
    def edit_cycle(cycle_id: str, payload: CycleInput, session: Session = DB):
        cycle = require(session, Cycle, cycle_id)
        if cycle.archived:
            raise HTTPException(409, '归档周期只读，请先恢复再编辑')
        if payload.expected_version is not None and cycle.version != payload.expected_version:
            raise HTTPException(409, '周期配置已变化，请重新读取后编辑')
        before = dump(cycle)
        cycle.name = payload.name
        cycle.version += 1
        audit(session, "rename_cycle", cycle.id, before, dump(cycle))
        session.commit()
        return dump(cycle)

    @app.post("/api/accounts", status_code=201)
    def add_account(payload: NamedInput, session: Session = DB):
        account = Account(id=new_id(), **payload.model_dump(), target_grade=None, comparison_mode="strict")
        session.add(account)
        session.flush()
        audit(session, "create_account", account.id, None, dump(account), account.id)
        session.commit()
        return dump(account)

    @app.put("/api/accounts/{account_id}")
    def edit_account(account_id: str, payload: NamedInput, session: Session = DB):
        account = require(session, Account, account_id)
        before = dump(account)
        for key, value in payload.model_dump().items():
            setattr(account, key, value)
        audit(session, "edit_account", account.id, before, dump(account), account.id)
        session.commit()
        return dump(account)

    @app.put("/api/accounts/{account_id}/preferences")
    def preferences(account_id: str, payload: AccountPreferences, session: Session = DB):
        account = require(session, Account, account_id)
        for key, value in payload.model_dump().items():
            setattr(account, key, value)
        session.commit()
        return dump(account)

    def account_character(session, account_id, character_id):
        account = require(session, Account, account_id)
        character = require(session, Character, character_id)
        if character.account_id != account_id:
            raise HTTPException(404, "此账号下没有该角色")
        return account, character

    @app.get("/api/accounts/{account_id}/characters")
    def characters(account_id: str, session: Session = DB):
        require(session, Account, account_id)
        return [dump(c) for c in session.scalars(select(Character).where(Character.account_id == account_id).order_by(Character.sort_order, Character.name, Character.id))]

    @app.post("/api/accounts/{account_id}/characters", status_code=201)
    def add_character(account_id: str, payload: NamedInput, session: Session = DB):
        account = require(session, Account, account_id)
        if account.archived:
            raise HTTPException(409, "请先恢复账号")
        character = Character(id=new_id(), account_id=account_id, **payload.model_dump())
        session.add(character)
        session.flush()
        session.add(CharacterCalculation(character_id=character.id))
        for slot in SLOT_KEYS:
            session.add(WornEquipment(character_id=character.id, slot=slot, config_id=None, version=0))
            session.add(SlotSetting(character_id=character.id, slot=slot, refine_level=None, stone_state="unknown", stone_level=None))
        audit(session, "create_character", character.id, None, dump(character), account_id)
        session.commit()
        return dump(character)

    @app.put("/api/accounts/{account_id}/characters/{character_id}")
    def edit_character(account_id: str, character_id: str, payload: NamedInput, session: Session = DB):
        _, character = account_character(session, account_id, character_id)
        before = dump(character)
        for key, value in payload.model_dump().items():
            setattr(character, key, value)
        audit(session, "edit_character", character.id, before, dump(character), account_id)
        session.commit()
        return dump(character)

    @app.get("/api/cycles/{cycle_id}/sources")
    def sources(cycle_id: str, session: Session = DB):
        require(session, Cycle, cycle_id)
        return [dump(s) for s in session.scalars(select(Source).where(Source.cycle_id == cycle_id).order_by(Source.name, Source.id))]

    @app.post("/api/cycles/{cycle_id}/sources", status_code=201)
    def add_source(cycle_id: str, payload: SourceInput, session: Session = DB):
        cycle = require(session, Cycle, cycle_id)
        if cycle.archived:
            raise HTTPException(409, '归档周期只读，请先恢复再编辑')
        if payload.expected_version is not None and cycle.version != payload.expected_version:
            raise HTTPException(409, '周期配置已变化，请重新读取后编辑')
        source = Source(id=new_id(), cycle_id=cycle_id, **payload.model_dump(exclude={'expected_version'}))
        session.add(source)
        session.flush()
        session.execute(update(Cycle).where(Cycle.id == cycle_id).values(version=Cycle.version + 1))
        audit(session, "create_source", source.id, None, dump(source))
        session.commit()
        return dump(source)

    @app.put("/api/cycles/{cycle_id}/sources/{source_id}")
    def edit_source(cycle_id: str, source_id: str, payload: SourceInput, session: Session = DB):
        cycle = require(session, Cycle, cycle_id)
        if cycle.archived:
            raise HTTPException(409, '归档周期只读，请先恢复再编辑')
        if payload.expected_version is not None and cycle.version != payload.expected_version:
            raise HTTPException(409, '周期配置已变化，请重新读取后编辑')
        source = require(session, Source, source_id)
        if source.cycle_id != cycle_id:
            raise HTTPException(404, "此周期下没有该来源")
        before = dump(source)
        for key, value in payload.model_dump(exclude={'expected_version'}).items():
            setattr(source, key, value)
        session.execute(update(Cycle).where(Cycle.id == cycle_id).values(version=Cycle.version + 1))
        audit(session, "edit_source", source.id, before, dump(source))
        session.commit()
        return dump(source)

    @app.get("/api/templates")
    def templates(equipment_type: str | None = None, session: Session = DB):
        query = select(Template)
        if equipment_type:
            query = query.where(Template.equipment_type == equipment_type)
        return [template_view(session, t) for t in session.scalars(query.order_by(Template.actual_grade.desc(), Template.id))]

    @app.post("/api/templates", status_code=201)
    def add_template(payload: TemplateInput, session: Session = DB):
        template = create_template(session, payload)
        audit(session, "save_template", template.id, None, dump(template))
        session.commit()
        return dump(template)

    @app.get("/api/accounts/{account_id}/matrix")
    def matrix(account_id: str, session: Session = DB):
        account = require(session, Account, account_id)
        characters = list(session.scalars(select(Character).where(Character.account_id == account_id, Character.archived.is_(False)).order_by(Character.sort_order, Character.name, Character.id)))
        records = session.execute(
            select(WornEquipment, Template, EquipmentConfig).join(Character, Character.id == WornEquipment.character_id)
            .outerjoin(EquipmentConfig, EquipmentConfig.id == WornEquipment.config_id)
            .outerjoin(Template, Template.id == EquipmentConfig.template_id)
            .where(Character.account_id == account_id, Character.archived.is_(False))
        ).all()
        cells = {}
        for worn, template, config in records:
            equipment = template_view(session, template) if template else None
            comparison = compare(template.actual_grade if template else None,
                                 template.has_constitution_socket if template else False,
                                 template.green_stats_ok if template else False,
                                 account.target_grade, account.comparison_mode)
            cells.setdefault(worn.character_id, {})[worn.slot] = {
                "equipment": equipment, "version": worn.version, **comparison,
                "choices": choices_for(config) if config else None,
                "label": label(template.short_label, template.has_constitution_socket, template.green_stats_ok) if template else "未装备",
            }
        return {"account": dump(account), "characters": [dump(c) for c in characters], "cells": cells, "growth": {c.id: {k: v for k, v in growth_report(session, c).items() if k in ("known_subtotal", "equipment_total", "verified_total", "buffs")} for c in characters},
                "constitution": {"status": "per_character", "message": "体质与层数按角色计算，详见 growth 字段", "total": None, "stacks": None}}

    def writable_slot(session, account_id, character_id, slot):
        account, character = account_character(session, account_id, character_id)
        if account.archived or character.archived:
            raise HTTPException(409, "请先恢复账号和角色，再修改装备")
        if slot not in SLOT_KEYS:
            raise HTTPException(422, "装备部位无效")
        return require(session, WornEquipment, (character_id, slot))

    def save_worn(session, account_id, worn, config_id, expected_version):
        before = dump(worn)
        result = session.execute(update(WornEquipment).where(
            WornEquipment.character_id == worn.character_id, WornEquipment.slot == worn.slot,
            WornEquipment.version == expected_version,
        ).values(config_id=config_id, version=WornEquipment.version + 1).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(409, "此格装备已在其他页面修改，请关闭编辑并刷新后重试")
        session.expire(worn)
        audit(session, "replace_equipment", f"{worn.character_id}:{worn.slot}", before, dump(worn), account_id)
        session.commit()
        return {"version": worn.version}

    @app.put("/api/accounts/{account_id}/characters/{character_id}/equipment/{slot}")
    def equip(account_id: str, character_id: str, slot: str, payload: EquipInput, session: Session = DB):
        worn = writable_slot(session, account_id, character_id, slot)
        template = require(session, Template, payload.template_id) if payload.template_id else create_template(session, payload.equipment)
        if template.equipment_type != equipment_type(slot):
            raise HTTPException(422, "装备模板与角色部位不匹配")
        config = create_config(session, template, payload.choices)
        return save_worn(session, account_id, worn, config.id, payload.expected_version)

    @app.delete("/api/accounts/{account_id}/characters/{character_id}/equipment/{slot}")
    def clear_equipment(account_id: str, character_id: str, slot: str, expected_version: int = Query(ge=0), session: Session = DB):
        worn = writable_slot(session, account_id, character_id, slot)
        return save_worn(session, account_id, worn, None, expected_version)

    app.include_router(make_inventory_router(engine, database))
    app.include_router(make_cycle_data_router(engine, database))
    app.include_router(make_growth_router(engine, database))
    app.include_router(make_data_router(engine, database))

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
