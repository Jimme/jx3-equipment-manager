from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Grade = Annotated[int, Field(strict=True, gt=0, le=2147483647)]
Nonnegative = Annotated[int, Field(strict=True, ge=0, le=2147483647)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NamedInput(Input):
    name: Name
    sort_order: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0
    archived: bool = False


class CycleInput(Input):
    name: Name
    expected_version: Nonnegative | None = None


class ActiveCycle(Input):
    cycle_id: str


class AccountPreferences(Input):
    target_grade: Grade | None
    comparison_mode: Literal["strict", "grade", "combined"]


class SourceInput(Input):
    source_type: Name
    name: Name
    short_label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12)]
    default_grade: Grade | None = None
    expected_version: Nonnegative | None = None


class TemplateInput(Input):
    cycle_origin_id: str
    source_id: str | None = None
    source_type: Name = "特殊"
    source_name: Name = "手动录入"
    short_label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12)] = "自"
    equipment_type: Literal["hat", "chest", "belt", "weapon", "ranged", "wrist", "legs", "feet", "necklace", "pendant", "ring"]
    actual_grade: Grade | None = None
    has_constitution_socket: bool
    green_stats_ok: bool
    base_constitution_override: Nonnegative | None = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    note: Annotated[str, StringConstraints(max_length=2000)] = ""


class EquipInput(Input):
    expected_version: Nonnegative
    template_id: str | None = None
    equipment: TemplateInput | None = None
    choices: 'EquipmentChoices | None' = None

    @model_validator(mode="after")
    def one_equipment(self):
        if (self.template_id is None) == (self.equipment is None):
            raise ValueError("template_id 和 equipment 必须且只能提供一项")
        return self


class AttachmentChoice(Input):
    state: Literal['unknown', 'none', 'configured'] = 'unknown'
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ''
    constitution: Nonnegative | None = None

    @model_validator(mode='after')
    def valid_choice(self):
        if self.state == 'configured':
            if not self.name and self.constitution is None:
                raise ValueError('请填写名称或已确认的体质贡献')
        elif self.name or self.constitution is not None:
            raise ValueError('未录入或确认无时，不能同时填写名称或数值')
        return self


class EquipmentChoices(Input):
    enchant: AttachmentChoice = Field(default_factory=AttachmentChoice)
    rainbow: AttachmentChoice = Field(default_factory=AttachmentChoice)


class GrowthSlotInput(Input):
    slot: Literal['hat','chest','belt','weapon','ranged','wrist','legs','feet','necklace','pendant','ring1','ring2']
    expected_version: Nonnegative
    refine_level: Annotated[int, Field(strict=True)] | None
    stone_state: Literal['unknown', 'none', 'set']
    stone_level: Annotated[int, Field(strict=True, ge=1, le=8)] | None

    @model_validator(mode='after')
    def valid_growth(self):
        if self.refine_level is not None and self.refine_level not in (0, 4, 6, 8):
            raise ValueError('精炼等级只能为 0、4、6、8 或未知')
        if (self.stone_state == 'set') != (self.stone_level is not None):
            raise ValueError('五行石等级与镶嵌状态不一致')
        return self


class GrowthInput(Input):
    slots: Annotated[list[GrowthSlotInput], Field(min_length=12, max_length=12)]
    expected_version: Nonnegative
    expected_cycle_id: str | None
    expected_cycle_version: Nonnegative | None
    additional_constitution: Nonnegative | None
    scope_confirmed: Annotated[bool, Field(strict=True)]

    @model_validator(mode='after')
    def valid_scope(self):
        if len({s.slot for s in self.slots}) != 12:
            raise ValueError('请提交 12 个不同部位')
        if self.scope_confirmed and (self.additional_constitution is None or self.expected_cycle_id is None):
            raise ValueError('核实计算口径前，请填写额外修正（确认没有时填 0）并选择当前周期')
        return self


class MutationInput(Input):
    request_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class InventoryAdd(MutationInput):
    equipment: TemplateInput
    choices: EquipmentChoices = Field(default_factory=EquipmentChoices)
    quantity: Grade


class QuantityChange(MutationInput):
    expected_version: Nonnegative
    quantity: Nonnegative


class WarehouseEquip(MutationInput):
    character_id: str
    slot: str
    expected_version: Nonnegative
    expected_equipment_version: Nonnegative
    choices: EquipmentChoices | None = None


class DropTransfer(MutationInput):
    expected_version: Nonnegative
    quantity: Grade


class DropClear(MutationInput):
    expected_version: Nonnegative


EquipInput.model_rebuild()


StatType = Literal['hat', 'chest', 'belt', 'weapon', 'ranged', 'wrist', 'legs', 'feet', 'necklace', 'pendant', 'ring']


class BaseStatInput(Input):
    actual_grade: Grade
    equipment_type: StatType
    constitution: Nonnegative | None = None


class RefineStatInput(BaseStatInput):
    refine_level: Annotated[int, Field(strict=True)]

    @field_validator('refine_level')
    @classmethod
    def valid_refine(cls, value):
        if value not in (0, 4, 6, 8):
            raise ValueError('精炼等级只能为 0、4、6、8')
        return value


class StoneStatInput(Input):
    stone_level: Annotated[int, Field(strict=True, ge=1, le=8)]
    constitution: Nonnegative | None = None


class EnchantStatInput(Input):
    equipment_type: StatType
    name: Name
    constitution: Nonnegative | None = None


class RainbowStatInput(Input):
    name: Name
    constitution: Nonnegative | None = None


class BuffThresholdInput(Input):
    skill: Name
    stacks: Nonnegative
    threshold: Nonnegative | None = None


class CycleTablePreviewInput(Input):
    expected_version: Nonnegative
    rows: Annotated[list[dict[str, Any]], Field(max_length=5000)]


class CycleCsvPreviewInput(Input):
    expected_version: Nonnegative
    csv_text: Annotated[str, Field(max_length=2000000)]


class CycleCopyInput(Input):
    expected_version: Nonnegative
    name: Name


class CycleArchiveInput(Input):
    expected_version: Nonnegative
    archived: bool


class DataApplyInput(Input):
    confirm_replace: Annotated[bool, Field(strict=True)]
