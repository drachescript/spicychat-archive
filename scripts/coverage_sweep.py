"""Select bounded missing-file repair work from an actual R2 inventory.
Absence of a file never establishes SpicyChat deletion or visibility.
"""
import re
UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")

def select_missing(order, stored, excluded, cursor, limit):
    total = len(order)
    selected, positions = [], []
    if not total or limit <= 0:
        return selected, positions
    cursor %= total
    for i in range(total):
        pos = (cursor+i) % total
        bot_id = order[pos]
        if (bot_id not in stored and bot_id not in excluded and
                UUID.fullmatch(bot_id)):
            selected.append(bot_id)
            positions.append((pos+1) % total)
            if len(selected) >= limit:
                break
    return selected, positions

def safe_budget(status, config):
    usage = status.get("usage") or {}
    guard = (config.get("storage") or {}).get("quota_guard") or {}
    if status.get("selectedMode") != "r2" or not status.get("r2ReadAllowed"):
        return 0
    if not all(isinstance(usage.get(k), int) for k in ("classA", "classB", "storageBytes")):
        return 0
    # Conservative planning reserve for data writes, index updates, inventory,
    # other scheduled work and delayed provider metrics. Existing hard limits
    # and rate limiting still apply in the crawler.
    a = max(0, int(guard.get("class_a_pause") or 950000)-usage["classA"]-25000)
    b = max(0, int(guard.get("class_b_pause") or 6000000)-usage["classB"]-50000)
    storage = max(0, int(guard.get("storage_pause_bytes") or 9400000000)-usage["storageBytes"]-100000000)
    return min(50000, a//8, b//5, storage//20000)
