import type { Template } from './types';

export function compareEquipment(a: Template, b: Template, mode: 'grade' | 'strict' | 'combined' = 'strict'): number {
  const grade = Math.sign(a.actual_grade - b.actual_grade);
  if (grade || mode === 'grade') return grade;
  return Number(a.has_constitution_socket || a.socket_applicable === false || a.equipment_type === 'ring') - Number(b.has_constitution_socket || b.socket_applicable === false || b.equipment_type === 'ring')
    || Number(a.green_stats_ok) - Number(b.green_stats_ok);
}
