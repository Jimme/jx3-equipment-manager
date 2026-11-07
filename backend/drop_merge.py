"""Normalize historical duplicate drop rows without losing their quantities."""
from collections import defaultdict


def merge_drop_rows(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row['account_id'], row['config_id'])].append(row)
    result, changes = [], []
    for group in groups.values():
        ordered = sorted(group, key=lambda row: (row['created_at'], row['id']))
        first = dict(ordered[0])
        if len(ordered) > 1:
            quantity = sum(row['quantity'] for row in ordered)
            if quantity > 2147483647:
                raise ValueError('相同配置的掉落合计超过数量上限，请先在旧版本更正数量')
            first.update(quantity=quantity, version=max(row['version'] for row in ordered) + 1)
            changes.append((ordered, first))
        result.append(first)
    return result, changes
