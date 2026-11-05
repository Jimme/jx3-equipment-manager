import { expect, it } from 'vitest';
import { asDraft, dimensionText, payloadRows, rowFingerprint } from './cycleDataUtils';

it('表格往返保留未知和显式零，不把空白当作零', () => {
  const rows=[{actual_grade:1110,equipment_type:'hat',constitution:0},{actual_grade:1110,equipment_type:'weapon',constitution:null}];
  expect(payloadRows(asDraft(rows))).toEqual(rows);
});

it('字段和行顺序不同不应误报草稿变化', () => {
  expect(rowFingerprint([{name:'甲',constitution:'0'},{name:'乙',constitution:''}])).toBe(rowFingerprint([{constitution:'',name:'乙'},{constitution:'0',name:'甲'}]));
});

it('预览维度显示戒指类型和明确的零级精炼', () => {
  expect(dimensionText({actual_grade:1110,equipment_type:'ring',refine_level:0,constitution:null})).toBe('1110品 · 戒指 · 精炼0');
});
