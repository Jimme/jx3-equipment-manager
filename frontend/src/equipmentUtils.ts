import type { Template } from './types';

export function compareEquipment(a: Template, b: Template): number {
  return Math.sign(a.actual_grade - b.actual_grade)
    || Number(a.has_constitution_socket) - Number(b.has_constitution_socket)
    || Number(a.green_stats_ok) - Number(b.green_stats_ok);
}
