from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Cycle(Base):
    __tablename__ = "equipment_cycles"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    archived: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(Integer, default=0)
    copied_from_id: Mapped[str | None] = mapped_column(ForeignKey('equipment_cycles.id'))


class Workspace(Base):
    __tablename__ = "workspace_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    active_cycle_id: Mapped[str | None] = mapped_column(ForeignKey("equipment_cycles.id"))
    version_seed: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("id = 1"), CheckConstraint('version_seed >= 0', name='ck_workspace_version_seed'))


class Account(Base):
    __tablename__ = "game_accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    sort_order: Mapped[int] = mapped_column(default=0)
    archived: Mapped[bool] = mapped_column(default=False)
    target_grade: Mapped[int | None] = mapped_column(Integer)
    comparison_mode: Mapped[str] = mapped_column(String, default="strict")
    __table_args__ = (
        CheckConstraint("target_grade IS NULL OR target_grade > 0", name="ck_game_accounts_target_grade"),
        CheckConstraint("comparison_mode IN ('strict', 'grade', 'combined')", name="ck_game_accounts_comparison_mode"),
    )


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("game_accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    sort_order: Mapped[int] = mapped_column(default=0)
    archived: Mapped[bool] = mapped_column(default=False)
    raid_10: Mapped[bool] = mapped_column(default=False, server_default="0")
    raid_25pt: Mapped[bool] = mapped_column(default=False, server_default="0")
    raid_25yx: Mapped[bool] = mapped_column(default=False, server_default="0")


class Source(Base):
    __tablename__ = "equipment_sources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("equipment_cycles.id"), index=True)
    source_type: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    short_label: Mapped[str] = mapped_column(String)
    default_grade: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("default_grade IS NULL OR default_grade > 0"),)


class Template(Base):
    __tablename__ = "equipment_templates"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    cycle_origin_id: Mapped[str] = mapped_column(ForeignKey("equipment_cycles.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("equipment_sources.id"))
    source_type: Mapped[str] = mapped_column(String)
    source_name: Mapped[str] = mapped_column(String)
    short_label: Mapped[str] = mapped_column(String)
    equipment_type: Mapped[str] = mapped_column(String)
    actual_grade: Mapped[int] = mapped_column(Integer)
    has_constitution_socket: Mapped[bool] = mapped_column()
    green_stats_ok: Mapped[bool] = mapped_column()
    base_constitution_override: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        CheckConstraint("actual_grade > 0"),
        CheckConstraint("equipment_type IN ('hat','chest','belt','weapon','ranged','wrist','legs','feet','necklace','pendant','ring')"),
        CheckConstraint("has_constitution_socket IN (0,1) AND green_stats_ok IN (0,1)"),
        CheckConstraint("base_constitution_override IS NULL OR base_constitution_override >= 0"),
    )


class EquipmentConfig(Base):
    __tablename__ = "equipment_configs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("equipment_templates.id"), index=True)
    config_key: Mapped[str] = mapped_column(String, unique=True)
    enchant_state: Mapped[str] = mapped_column(String, default="unknown")
    enchant_name: Mapped[str] = mapped_column(String, default="")
    enchant_constitution: Mapped[int | None] = mapped_column(Integer)
    rainbow_state: Mapped[str] = mapped_column(String, default="unknown")
    rainbow_name: Mapped[str] = mapped_column(String, default="")
    rainbow_constitution: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("(enchant_state IN ('unknown','none') AND enchant_name='' AND enchant_constitution IS NULL) OR (enchant_state='configured' AND (enchant_name<>'' OR enchant_constitution IS NOT NULL))", name="ck_config_enchant"),
        CheckConstraint("(rainbow_state IN ('unknown','none','not_applicable') AND rainbow_name='' AND rainbow_constitution IS NULL) OR (rainbow_state='configured' AND (rainbow_name<>'' OR rainbow_constitution IS NOT NULL))", name="ck_config_rainbow"),
        CheckConstraint("enchant_constitution IS NULL OR enchant_constitution >= 0", name="ck_config_enchant_value"),
        CheckConstraint("rainbow_constitution IS NULL OR rainbow_constitution >= 0", name="ck_config_rainbow_value"),
    )


class SlotSetting(Base):
    __tablename__ = "character_slot_settings"
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), primary_key=True)
    slot: Mapped[str] = mapped_column(String, primary_key=True)
    refine_level: Mapped[int | None] = mapped_column(Integer)
    stone_state: Mapped[str] = mapped_column(String, default="unknown")
    stone_level: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint("refine_level IS NULL OR refine_level IN (0,4,6,8)"),
        CheckConstraint("(stone_state IN ('unknown','none') AND stone_level IS NULL) OR (stone_state = 'set' AND stone_level BETWEEN 1 AND 8 AND stone_level IS NOT NULL)"),
        CheckConstraint("version >= 0", name="ck_slot_setting_version"),
    )


class CharacterCalculation(Base):
    __tablename__ = 'character_calculation_settings'
    character_id: Mapped[str] = mapped_column(ForeignKey('characters.id'), primary_key=True)
    additional_constitution: Mapped[int | None] = mapped_column(Integer)
    scope_confirmed: Mapped[bool] = mapped_column(default=False)
    threshold_cycle_id: Mapped[str | None] = mapped_column(ForeignKey('equipment_cycles.id'))
    threshold_signature: Mapped[str] = mapped_column(String, default='')
    version: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint('additional_constitution IS NULL OR additional_constitution >= 0'),
        CheckConstraint('version >= 0'),
    )


class WornEquipment(Base):
    __tablename__ = "character_equipment"
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), primary_key=True)
    slot: Mapped[str] = mapped_column(String, primary_key=True)
    config_id: Mapped[str | None] = mapped_column(ForeignKey("equipment_configs.id"))
    version: Mapped[int] = mapped_column(default=0)
    __table_args__ = (
        CheckConstraint("slot IN ('hat','chest','belt','weapon','ranged','wrist','legs','feet','necklace','pendant','ring1','ring2')"),
        CheckConstraint("version >= 0"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("game_accounts.id"))
    operation: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str] = mapped_column(String)
    snapshots: Mapped[str] = mapped_column(Text)


class WarehouseStack(Base):
    __tablename__ = "warehouse_stacks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("game_accounts.id"), index=True)
    config_id: Mapped[str] = mapped_column(ForeignKey("equipment_configs.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("account_id", "config_id", name="uq_warehouse_account_config"), CheckConstraint("quantity BETWEEN 0 AND 2147483647", name="ck_warehouse_quantity"), CheckConstraint("version >= 0", name="ck_warehouse_version"))


class RaidDrop(Base):
    __tablename__ = "raid_drop_notes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("game_accounts.id"), index=True)
    config_id: Mapped[str] = mapped_column(ForeignKey("equipment_configs.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String)
    __table_args__ = (CheckConstraint("quantity BETWEEN 1 AND 2147483647", name="ck_drop_quantity"), CheckConstraint("version >= 0", name="ck_drop_version"))


class RaidMemoState(Base):
    __tablename__ = "raid_memo_states"
    account_id: Mapped[str] = mapped_column(ForeignKey("game_accounts.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("version >= 0"),)


class MutationReceipt(Base):
    __tablename__ = "mutation_receipts"
    account_id: Mapped[str] = mapped_column(ForeignKey("game_accounts.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String)
    result: Mapped[str] = mapped_column(Text)


class CycleBaseStat(Base):
    __tablename__ = 'cycle_base_stats'
    cycle_id: Mapped[str] = mapped_column(ForeignKey('equipment_cycles.id'), primary_key=True)
    actual_grade: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_type: Mapped[str] = mapped_column(String, primary_key=True)
    constitution: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("equipment_type IN ('hat','chest','belt','weapon','ranged','wrist','legs','feet','necklace','pendant','ring')"), CheckConstraint('actual_grade > 0'), CheckConstraint('constitution IS NULL OR constitution >= 0'))


class CycleRefineStat(Base):
    __tablename__ = 'cycle_refine_stats'
    cycle_id: Mapped[str] = mapped_column(ForeignKey('equipment_cycles.id'), primary_key=True)
    actual_grade: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_type: Mapped[str] = mapped_column(String, primary_key=True)
    refine_level: Mapped[int] = mapped_column(Integer, primary_key=True)
    constitution: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("equipment_type IN ('hat','chest','belt','weapon','ranged','wrist','legs','feet','necklace','pendant','ring')"), CheckConstraint('actual_grade > 0'), CheckConstraint('refine_level IN (0,4,6,8)'), CheckConstraint('constitution IS NULL OR constitution >= 0'))


class CycleStoneStat(Base):
    __tablename__ = 'cycle_stone_stats'
    cycle_id: Mapped[str] = mapped_column(ForeignKey('equipment_cycles.id'), primary_key=True)
    stone_level: Mapped[int] = mapped_column(Integer, primary_key=True)
    constitution: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint('stone_level BETWEEN 1 AND 8'), CheckConstraint('constitution IS NULL OR constitution >= 0'))


class CycleEnchantStat(Base):
    __tablename__ = 'cycle_enchants'
    cycle_id: Mapped[str] = mapped_column(ForeignKey('equipment_cycles.id'), primary_key=True)
    equipment_type: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, primary_key=True)
    constitution: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("equipment_type IN ('hat','chest','belt','weapon','ranged','wrist','legs','feet','necklace','pendant','ring')"), CheckConstraint('constitution IS NULL OR constitution >= 0'),)


class CycleRainbowStat(Base):
    __tablename__ = 'cycle_rainbow_stones'
    cycle_id: Mapped[str] = mapped_column(ForeignKey('equipment_cycles.id'), primary_key=True)
    name: Mapped[str] = mapped_column(String, primary_key=True)
    constitution: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint('constitution IS NULL OR constitution >= 0'),)


class CycleBuffThreshold(Base):
    __tablename__ = 'cycle_buff_thresholds'
    cycle_id: Mapped[str] = mapped_column(ForeignKey('equipment_cycles.id'), primary_key=True)
    skill: Mapped[str] = mapped_column(String, primary_key=True)
    stacks: Mapped[int] = mapped_column(Integer, primary_key=True)
    threshold: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint('stacks >= 0'), CheckConstraint('threshold IS NULL OR threshold >= 0'))


class CycleChangePreview(Base):
    __tablename__ = 'cycle_change_previews'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey('equipment_cycles.id'), index=True)
    expected_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)
    rows_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
    result_json: Mapped[str | None] = mapped_column(Text)


class DataChangePreview(Base):
    __tablename__ = 'data_change_previews'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    expected_fingerprint: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
    result_json: Mapped[str | None] = mapped_column(Text)
