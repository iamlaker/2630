import io, json, sys, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:8765"

def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))

print("== 1. 初始场景列表 ==")
s, b = call("GET", "/api/scenarios")
print(s, "count:", len(b["scenarios"]), "types:", b["scenario_types"])

print("\n== 2. 保存真实场景（10年期国债收益率 +10bp，未计算） ==")
s, b = call("POST", "/api/scenarios", {
    "name": "国债收益率+10bp", "scenario_type": "custom", "template_version_id": 2,
    "rule_publication_id": "0b473b8a-44d5-40e0-a957-ed151116fd44",
    "input_adjustments": {"重要参数|10年期国债收益率|63": {"2026": 0.0185, "2027": 0.0175, "2028": 0.0175, "2029": 0.0175, "2030": 0.0175}},
})
print(s, b.get("scenario_id"), b.get("read_only"), b.get("validation_state"))
sid = b["scenario_id"]

print("\n== 3. 重算场景（真实 COM，异步） ==")
s, task = call("POST", f"/api/scenarios/{sid}/recalculate", {})
print(s, task.get("status"))
t0 = time.time()
final = None
while time.time() - t0 < 120:
    s, snap = call("GET", f"/api/calculations/{task['task_id']}")
    if snap["status"] in ("succeeded", "failed", "cancelled", "cycle_not_converged"):
        final = snap
        break
    time.sleep(1)
print("task:", final["status"], "elapsed:", final["elapsed_ms"], "ms")
s, sc = call("GET", f"/api/scenarios/{sid}")
print("场景更新后: validation_state=", sc["validation_state"], "快照输出数=", len(sc["calculation_result_snapshot"] or {}), "read_only=", sc["read_only"])

print("\n== 4. 复制 + 重命名 ==")
s, cp = call("POST", f"/api/scenarios/{sid}/copy", {})
print("copy:", s, cp.get("name"), cp.get("read_only"))
s, rn = call("POST", f"/api/scenarios/{cp['scenario_id']}/rename", {"name": "国债+10bp 副本A"})
print("rename:", s, rn.get("name"))

print("\n== 5. 删除副本 ==")
s, d = call("DELETE", f"/api/scenarios/{cp['scenario_id']}")
print(s, d)

print("\n== 6. 校验失败路径 ==")
s, e1 = call("POST", "/api/scenarios", {"name": "  ", "template_version_id": 2})
print("空名称:", s, e1)
s, e2 = call("POST", "/api/scenarios", {"name": "x", "template_version_id": 1})
print("历史模板保存:", s, e2)
s, e3 = call("GET", "/api/scenarios/nonexistent")
print("不存在场景:", s, e3)

print("\n== 7. 最终列表 ==")
s, b = call("GET", "/api/scenarios")
for item in b["scenarios"]:
    print(" -", item["name"], item["scenario_type"], item["validation_state"], "adjustments:", item["adjustment_count"], "read_only:", item["read_only"])
