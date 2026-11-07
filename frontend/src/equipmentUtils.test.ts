import { expect, it } from 'vitest';
import { compareEquipment } from './equipmentUtils';
import type { Template } from './types';

const template = (grade: number, socket: boolean, green: boolean) => ({ actual_grade: grade, has_constitution_socket: socket, green_stats_ok: green }) as Template;
it('降级提醒仍以真实品级优先，不因缺孔误判高品级装备', () => {
  expect(compareEquipment(template(1200, false, false), template(1110, true, true))).toBeGreaterThan(0);
  expect(compareEquipment(template(965, true, true), template(1110, false, false))).toBeLessThan(0);
});
it('同品级按孔、绿字判断，相同配置不提示降级', () => {
  expect(compareEquipment(template(1110, false, true), template(1110, true, false))).toBeLessThan(0);
  expect(compareEquipment(template(1110, true, false), template(1110, true, true))).toBeLessThan(0);
  expect(compareEquipment(template(1110, true, true), template(1110, true, true))).toBe(0);
});
it('仅品级模式忽略孔和绿字，无孔周期与戒指不比较孔', () => {
  expect(compareEquipment(template(1110, false, false), template(1110, true, true), 'grade')).toBe(0);
  expect(compareEquipment({ ...template(1110, false, true), socket_applicable: false }, template(1110, true, true))).toBe(0);
  expect(compareEquipment({ ...template(1110, false, true), equipment_type: 'ring' }, template(1110, true, true))).toBe(0);
});
