"""Versioned management changes and conservative deletion of unused entities."""
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import (Account, AuditEvent, Character, CharacterCalculation, CreationReceipt,
                     MutationReceipt, RaidDrop, RaidMemoState, SlotSetting, WarehouseStack, WornEquipment)
from .schemas import ManagementPatch, MutationInput
from .creation import begin_creation, finish_creation
from .services import audit, dump, require


def edit_entity(session, entity, payload, account_id):
    operation = ('manage_account:' if isinstance(entity, Account) else 'manage_character:') + entity.id
    fingerprint, replay = begin_creation(session, operation, payload)
    if replay is not None:
        return replay
    if payload.expected_version != entity.version:
        raise HTTPException(409, '名称、排序或归档状态已变化，请读取最新记录核对')
    before = dump(entity)
    for key, value in payload.model_dump(exclude={'expected_version', 'request_id'}, exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, '管理字段不能为空')
        setattr(entity, key, value)
    if dump(entity) != before:
        entity.version += 1
        audit(session, 'edit_account' if isinstance(entity, Account) else 'edit_character', entity.id, before, dump(entity), account_id)
    return finish_creation(session, payload, fingerprint, dump(entity))


def make_management_router(engine):
    router = APIRouter(prefix='/api/accounts')

    def database():
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session

    def owned_character(session, account_id, character_id):
        require(session, Account, account_id)
        character = require(session, Character, character_id)
        if character.account_id != account_id:
            raise HTTPException(404, '此账号下没有该角色')
        return character

    @router.patch('/{account_id}')
    def patch_account(account_id: str, payload: ManagementPatch, session: Session = Depends(database)):
        return edit_entity(session, require(session, Account, account_id), payload, account_id)

    @router.patch('/{account_id}/characters/{character_id}')
    def patch_character(account_id: str, character_id: str, payload: ManagementPatch, session: Session = Depends(database)):
        return edit_entity(session, owned_character(session, account_id, character_id), payload, account_id)

    def expire_creation(session, entity_id):
        for receipt in session.scalars(select(CreationReceipt)):
            if json.loads(receipt.result).get('id') == entity_id:
                receipt.result = json.dumps({'_restored_request': True})

    @router.delete('/{account_id}/characters/{character_id}')
    def delete_character(account_id: str, character_id: str, payload: MutationInput | None = None, expected_version: int = Query(ge=0), session: Session = Depends(database)):
        fingerprint, replay = begin_creation(session, f'delete_character:{account_id}:{character_id}:{expected_version}', payload) if payload else (None, None)
        if replay is not None:
            return replay
        character = owned_character(session, account_id, character_id)
        if character.version != expected_version:
            raise HTTPException(409, '角色已变化，请读取最新记录核对')
        worn = list(session.scalars(select(WornEquipment).where(WornEquipment.character_id == character_id)))
        settings = list(session.scalars(select(SlotSetting).where(SlotSetting.character_id == character_id)))
        calculation = require(session, CharacterCalculation, character_id)
        allowed = {'create_character', 'edit_character', 'delete_character'}
        history = list(session.scalars(select(AuditEvent).where(AuditEvent.account_id == account_id)))
        used = any(row.operation not in allowed and character_id in row.snapshots for row in history)
        if (not character.history_complete or used or any(row.config_id for row in worn) or
            any(row.refine_level is not None or row.stone_state != 'unknown' for row in settings) or
            calculation.additional_constitution is not None or calculation.scope_confirmed or
            character.raid_10 or character.raid_25pt or character.raid_25yx):
            raise HTTPException(409, '此角色存在业务或无法确认完整历史，请使用移除角色并保留记录')
        before = dump(character)
        for model in (WornEquipment, SlotSetting, CharacterCalculation):
            session.execute(delete(model).where(model.character_id == character_id))
        expire_creation(session, character_id)
        session.delete(character)
        audit(session, 'delete_character', character_id, before, None, account_id)
        if payload:
            return finish_creation(session, payload, fingerprint, {'deleted': True})
        session.commit()
        return {'deleted': True}

    @router.delete('/{account_id}')
    def delete_account(account_id: str, payload: MutationInput | None = None, expected_version: int = Query(ge=0), session: Session = Depends(database)):
        fingerprint, replay = begin_creation(session, f'delete_account:{account_id}:{expected_version}', payload) if payload else (None, None)
        if replay is not None:
            return replay
        account = require(session, Account, account_id)
        if account.version != expected_version:
            raise HTTPException(409, '账号已变化，请读取最新记录核对')
        references = any(session.scalar(select(model).where(model.account_id == account_id).limit(1)) is not None
                         for model in (Character, WarehouseStack, RaidDrop, MutationReceipt))
        events = list(session.scalars(select(AuditEvent).where(AuditEvent.account_id == account_id)))
        allowed = {'create_account', 'edit_account', 'create_character', 'edit_character', 'delete_character'}
        if references or any(row.operation not in allowed for row in events) or account.target_grade is not None or account.comparison_mode != 'strict':
            raise HTTPException(409, '此账号存在角色、库存或业务历史，请使用归档账号')
        before = dump(account)
        for row in events:
            row.account_id = None
        session.execute(delete(RaidMemoState).where(RaidMemoState.account_id == account_id))
        expire_creation(session, account_id)
        session.delete(account)
        audit(session, 'delete_account', account_id, before, None)
        if payload:
            return finish_creation(session, payload, fingerprint, {'deleted': True})
        session.commit()
        return {'deleted': True}

    return router
