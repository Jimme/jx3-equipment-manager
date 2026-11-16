import { optionalInteger } from './api';
import type { Data, EquipmentInput, Source, Template } from './types';

export function fieldsFor(equipment: Template | null) {
  return { source_id: equipment?.source_id ?? '', has_constitution_socket: String(equipment?.has_constitution_socket ?? false),
    green_stats_ok: String(equipment?.green_stats_ok ?? false), name: equipment?.name ?? '', note: equipment?.note ?? '',
    base_constitution_override: equipment?.base_constitution_override?.toString() ?? '' };
}

export type Fields = ReturnType<typeof fieldsFor>;

export function availableSources(data: Data, existingId = '') {
  const active = data.sources.filter(source => source.active || source.id === existingId);
  return active.sort((a, b) => Number(b.cycle_id === data.cycleId) - Number(a.cycle_id === data.cycleId));
}

export function sourceLabel(data: Data, source: Source) {
  const cycle = data.workspace.cycles.find(item => item.id === source.cycle_id);
  return `${source.name} · ${source.actual_grade} · ${cycle?.name ?? ''}${source.active ? '' : '（已停用）'}`;
}

export function socketApplicable(type: string) {
  return type !== 'ring';
}

export function equipmentInput(fields: Fields, type: string, expectedGrade?: number): EquipmentInput {
  return { ...fields, expected_source_grade: expectedGrade, equipment_type: type, base_constitution_override: optionalInteger(fields.base_constitution_override),
    has_constitution_socket: socketApplicable(type) && fields.has_constitution_socket === 'true',
    green_stats_ok: fields.green_stats_ok === 'true' };
}
