SLOTS = [
    ("hat", "帽子"), ("chest", "上衣"), ("belt", "腰带"),
    ("weapon", "武器"), ("ranged", "暗器"), ("wrist", "护腕"),
    ("legs", "下装"), ("feet", "鞋子"), ("necklace", "项链"),
    ("pendant", "腰坠"), ("ring1", "戒指1"), ("ring2", "戒指2"),
]
SLOT_KEYS = [key for key, _ in SLOTS]
EQUIPMENT_TYPES = [key for key in SLOT_KEYS if not key.startswith("ring")] + ["ring"]


def equipment_type(slot: str) -> str:
    return "ring" if slot in ("ring1", "ring2") else slot


def compare(grade, socket, green, target, mode):
    if target is None:
        return {"status": "unset", "reasons": ["未设置目标"]}
    if grade is None:
        return {"status": "missing", "reasons": ["未装备"]}
    if mode == "grade" or grade != target:
        good = grade >= target
        return {"status": "met" if good else "gap", "reasons": [] if good else ["品级不足"]}
    reasons = []
    if not socket:
        reasons.append("同品级缺体质孔")
    if not green:
        reasons.append("同品级绿字未达标")
    status = ("grade_met" if mode == "combined" else "gap") if reasons else "met"
    return {"status": status, "reasons": reasons}


def label(short_label, socket, green):
    return f"{short_label}{'' if socket else '-'}{'+' if green else ''}"
