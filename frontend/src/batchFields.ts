import { optionalInteger } from './api';
import type { Data, EquipmentInput, Source, Template } from './types';

export function fieldsFor(equipment: Template | null, cycleId: string) {
  return {
    cycle_origin_id: equipment?.cycle_origin_id ?? cycleId, source_id: '', source_type: equipment?.source_type ?? '特殊',
    source_name: equipment?.source_name ?? '', short_label: equipment?.short_label ?? '',
    actual_grade: equipment?.actual_grade?.toString() ?? '', has_constitution_socket: String(equipment?.has_constitution_socket ?? false),
    green_stats_ok: String(equipment?.green_stats_ok ?? false), name: equipment?.name ?? '', note: equipment?.note ?? '',
    base_constitution_override: equipment?.base_constitution_override?.toString() ?? '',
  };
}

export type Fields = ReturnType<typeof fieldsFor>;

export function withSource(fields: Fields, source: Source, protectGrade: boolean): Fields {
  return { ...fields, cycle_origin_id: source.cycle_id, source_id: source.id, source_type: source.source_type,
    source_name: source.name, short_label: source.short_label,
    actual_grade: protectGrade ? fields.actual_grade : source.default_grade?.toString() ?? '' };
}

export function socketApplicable(type: string) {
  return type !== 'ring';
}

export function equipmentInput(fields: Fields, type: string): EquipmentInput {
  return { ...fields, source_id: fields.source_id || null, equipment_type: type,
    actual_grade: optionalInteger(fields.actual_grade), base_constitution_override: optionalInteger(fields.base_constitution_override),
    has_constitution_socket: socketApplicable(type) && fields.has_constitution_socket === 'true',
    green_stats_ok: fields.green_stats_ok === 'true' };
}

export function batchGrades(data: Data) {
  const grades = new Set<number>();
  for (const source of data.sources) if (source.default_grade != null) grades.add(source.default_grade);
  for (const item of data.daily?.warehouse ?? []) if (item.equipment.actual_grade != null) grades.add(item.equipment.actual_grade);
  for (const cells of Object.values(data.matrix?.cells ?? {})) {
    for (const cell of Object.values(cells)) if (cell.equipment?.actual_grade != null) grades.add(cell.equipment.actual_grade);
  }
  return [...grades].sort((a, b) => b - a);
}
