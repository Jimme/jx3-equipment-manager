import type { StatKind, StatRow } from './types';
import { optionalInteger } from './api';

export const STAT_LABELS: Record<StatKind, string> = { base: '基础体质', refine: '精炼加成', stone: '五行石', enchant: '附魔', rainbow: '武器五彩石', buff: '技能阈值' };
export const STAT_TYPES = [['hat','帽子'],['chest','上衣'],['belt','腰带'],['weapon','武器'],['ranged','暗器'],['wrist','护腕'],['legs','下装'],['feet','鞋子'],['necklace','项链'],['pendant','腰坠'],['ring','戒指']] as const;
export const VALUE_FIELD = (kind: StatKind) => kind === 'buff' ? 'threshold' : 'constitution';
export const asDraft = (rows: StatRow[]) => rows.map(row => Object.fromEntries(Object.entries(row).map(([key, value]) => [key, value === null ? '' : String(value)])));
export const rowFingerprint = (rows: StatRow[]) => JSON.stringify(rows.map(row => JSON.stringify(Object.fromEntries(Object.entries(row).sort(([a], [b]) => a.localeCompare(b))))).sort());
export function payloadRows(rows: StatRow[]): StatRow[] {
  const numeric = new Set(['actual_grade','refine_level','stone_level','constitution','stacks','threshold']);
  return rows.map(row => Object.fromEntries(Object.entries(row).map(([key,value]) => [key, numeric.has(key) ? optionalInteger(String(value ?? '')) : value])));
}
export function dimensionText(row: StatRow | null): string {
  if (!row) return '';
  return [row.actual_grade != null ? `${row.actual_grade}品` : '', row.equipment_type ? STAT_TYPES.find(([key]) => key === row.equipment_type)?.[1] : '', row.refine_level != null ? `精炼${row.refine_level}` : '', row.stone_level != null ? `五行石${row.stone_level}级` : '', row.name, row.skill, row.stacks != null ? `${row.stacks}层` : ''].filter(Boolean).join(' · ');
}
