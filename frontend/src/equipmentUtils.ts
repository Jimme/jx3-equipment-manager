import type { Template } from './types';

export function socketSummary(equipment: Template): string {
  if (equipment.equipment_type === 'ring') return '戒指无体质孔';
  const observed = equipment.has_constitution_socket ? '有体质孔' : '无体质孔';
  return equipment.socket_applicable === false ? `${observed}（达标时不要求孔）` : observed;
}

export function compareEquipment(a: Template, b: Template, mode: 'grade' | 'strict' | 'combined' = 'strict'): number {
  const grade = Math.sign(a.actual_grade - b.actual_grade);
  if (grade || mode === 'grade') return grade;
  return Number(a.has_constitution_socket || a.socket_applicable === false || a.equipment_type === 'ring') - Number(b.has_constitution_socket || b.socket_applicable === false || b.equipment_type === 'ring')
    || Number(a.green_stats_ok) - Number(b.green_stats_ok);
}
