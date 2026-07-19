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

ADJ = {"template_version_id": 2, "adjustments": [{"rule_id": "1f5ea4fd-7b64-489a-99f5-e16cd251803a", "indicator_id": "重要参数|10年期国债收益率|63", "values": {"2026": 0.0185, "2027": 0.0175, "2028": 0.0175, "2029": 0.0175, "2030": 0.0175}}]}

print("== 1. 历史模板拒绝 ==")
s, b = call("POST", "/api/calculations", {"template_version_id": 1, "adjustments": []})
print(s, b)

print("\n== 2. 未知任务 ==")
s, b = call("GET", "/api/calculations/nope")
print(s, b)

print("\n== 3. 提交并中途取消（真实 COM） ==")
s, b = call("POST", "/api/calculations", ADJ)
print("submit:", s, {k: b[k] for k in ("task_id", "status")})
task_id = b["task_id"]
saw_stages, saw_cancel_requested = [], False
t0 = time.time()
cancel_sent = False
final = None
while time.time() - t0 < 120:
    s, snap = call("GET", f"/api/calculations/{task_id}")
    stage = snap.get("current_stage")
    if stage and stage not in saw_stages:
        saw_stages.append(stage)
        print(f"  t+{time.time()-t0:5.1f}s status={snap['status']} stage={stage} elapsed={snap['elapsed_ms']}ms timings={list((snap.get('stage_timings') or {}).keys())}")
    if not cancel_sent and time.time() - t0 > 2:
        s, c = call("POST", f"/api/calculations/{task_id}/cancel")
        cancel_sent = True
        print(f"  t+{time.time()-t0:5.1f}s 取消已发送 -> {c['status']}")
    if snap["status"] == "cancel_requested":
        saw_cancel_requested = True
    if snap["status"] in ("succeeded", "failed", "cancelled", "cycle_not_converged"):
        final = snap
        break
    time.sleep(0.5)
print(f"最终: status={final['status']} result={'有' if final['result'] else '无(正确)'} cancel_requested_seen={saw_cancel_requested} 阶段序列={saw_stages}")

print("\n== 4. 完整任务跑到成功 ==")
s, b = call("POST", "/api/calculations", ADJ)
task_id = b["task_id"]
t0 = time.time()
final = None
while time.time() - t0 < 120:
    s, snap = call("GET", f"/api/calculations/{task_id}")
    if snap["status"] in ("succeeded", "failed", "cancelled", "cycle_not_converged"):
        final = snap
        break
    time.sleep(0.5)
cd = final["result"]["calculation_details"] if final["result"] else {}
print(f"status={final['status']} 总耗时={final['elapsed_ms']}ms trust={final['result']['trust']['status']} 迭代={final['iterations']} 最终差异={final['final_differences']}")
print("stage_timings:", json.dumps(cd.get("stage_timings"), ensure_ascii=False))
print("卡片数:", len(final["result"]["core_results"]), "快照输出:", len(final["result"]["scenario_draft"]["calculation_result_snapshot"]))
