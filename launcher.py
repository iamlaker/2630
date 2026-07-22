"""五年经营测算台启动器：进程管理 UI（仅标准库）。

用法:
    python launcher.py            打开图形界面
    python launcher.py --self-test  无窗自检：枚举实例并打印后退出
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / ".workbench" / "launcher.json"
DEFAULT_PORT = "8765"
TEMPLATE_DIR = ROOT / "模版"
DEFAULT_TEMPLATE = "2026-2030年盈利测算表0720-模板.xlsx"


@dataclass
class Instance:
    pid: int
    created: str
    cmdline: str
    port: str = ""
    token: str = ""
    listening_ports: list[int] = field(default_factory=list)
    template: str = "—"
    warm: str = "—"

    @property
    def alive_http(self) -> bool:
        return self.template != "—"


def _run_powershell(script: str) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
    )
    return completed.stdout


def list_workbench_processes() -> list[Instance]:
    """枚举命令行含 workbench.py 的 python 进程（含 --port/--admin-token 解析）。"""
    out = _run_powershell(
        "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" "
        "| Where-Object { $_.CommandLine -match 'workbench\\.py' } "
        "| Select-Object ProcessId, CreationDate, CommandLine | ConvertTo-Json -Depth 2"
    ).strip()
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    instances = []
    for item in data:
        cmdline = item.get("CommandLine") or ""
        port = (re.search(r"--port\s+(\d+)", cmdline) or [None, ""])[1]
        token = (re.search(r"--admin-token\s+(\S+)", cmdline) or [None, ""])[1]
        instances.append(Instance(
            pid=item["ProcessId"], created=str(item.get("CreationDate") or "")[:19],
            cmdline=cmdline, port=port, token=token or "(随机/环境变量)",
        ))
    return instances


def listener_ports() -> dict[int, list[int]]:
    """netstat LISTENING 映射：PID -> [端口]。"""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15).stdout
    mapping: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            match = re.search(r":(\d+)$", parts[1])
            if match:
                mapping.setdefault(int(parts[4]), []).append(int(match.group(1)))
    return mapping


def probe(port: int) -> tuple[str, str]:
    """HTTP 探测实例：返回 (模板描述, warm 状态)。"""
    template = warm = "—"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/workbench", timeout=2) as resp:
            data = json.loads(resp.read())
        t = data.get("template", {})
        template = f"V{t.get('version')} · {str(t.get('fingerprint', ''))[:10]}{'（活动）' if t.get('activity') else '（历史只读）'}"
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/warm-health", timeout=2) as resp:
            health = json.loads(resp.read())
        warm = "正常" if health.get("healthy") else "不可用"
    except Exception:
        pass
    return template, warm


def discover() -> list[Instance]:
    listeners = listener_ports()
    instances = list_workbench_processes()
    for inst in instances:
        inst.listening_ports = listeners.get(inst.pid, [])
        ports = inst.listening_ports or ([int(inst.port)] if inst.port.isdigit() else [])
        if ports:
            inst.template, inst.warm = probe(ports[0])
    return instances


def kill(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)


def port_free(port: str) -> bool:
    return not any(port in [str(p) for ports in listener_ports().values() for p in ports])


def available_templates() -> list[str]:
    return [path.name for path in sorted(TEMPLATE_DIR.glob("*.xlsx"))]


def start_instance(port: str, token: str, template: str) -> None:
    cmd = [sys.executable, str(ROOT / "workbench.py"), "--port", port]
    if token:
        cmd += ["--admin-token", token]
    if template:
        cmd += ["--template", str(TEMPLATE_DIR / template)]
    subprocess.Popen(cmd, cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE)


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(port: str, token: str, template: str) -> None:
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"port": port, "token": token, "template": template}, ensure_ascii=False, indent=1), encoding="utf-8")


def self_test() -> int:
    instances = discover()
    print(f"发现 {len(instances)} 个 workbench 进程：")
    for inst in instances:
        print(f"  PID {inst.pid} | 端口 {inst.port or '?'} | 监听 {inst.listening_ports} | {inst.template} | warm {inst.warm} | 令牌 {inst.token}")
    conflicts = {}
    for inst in instances:
        for port in inst.listening_ports:
            conflicts.setdefault(port, []).append(inst.pid)
    for port, pids in conflicts.items():
        if len(pids) > 1:
            print(f"!! 端口 {port} 冲突：{pids}")
    return 0


def main() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    config = load_config()
    root = tk.Tk()
    root.title("五年经营测算台 · 启动器")
    root.geometry("980x460")

    columns = ("pid", "port", "created", "template", "warm", "token")
    tree = ttk.Treeview(root, columns=columns, show="headings", height=12)
    for key, text, width in [
        ("pid", "PID", 70), ("port", "端口", 60), ("created", "启动时间", 150),
        ("template", "服务模板", 200), ("warm", "warm", 70), ("token", "管理员令牌", 180),
    ]:
        tree.heading(key, text=text)
        tree.column(key, width=width, anchor="w")
    tree.tag_configure("conflict", background="#f8d7da")
    tree.tag_configure("dead", foreground="#999999")
    tree.pack(fill="both", expand=True, padx=10, pady=(10, 4))

    status = tk.StringVar(value="就绪")
    instances: list[Instance] = []

    def refresh() -> None:
        nonlocal instances
        tree.delete(*tree.get_children())
        instances = discover()
        port_owners: dict[int, int] = {}
        for inst in instances:
            for port in inst.listening_ports:
                port_owners[port] = port_owners.get(port, 0) + 1
        conflicts = {port for port, count in port_owners.items() if count > 1}
        for inst in instances:
            tags = []
            if any(port in conflicts for port in inst.listening_ports):
                tags.append("conflict")
            if not inst.listening_ports:
                tags.append("dead")
            tree.insert("", "end", iid=str(inst.pid), tags=tags, values=(
                inst.pid, inst.port or "?", inst.created, inst.template, inst.warm, inst.token,
            ))
        if conflicts:
            status.set(f"⚠ 端口冲突：{', '.join(map(str, sorted(conflicts)))} 被多个进程监听——请求会被随机路由，请终止多余实例")
        elif not instances:
            status.set("没有运行中的工作台实例")
        else:
            status.set(f"{len(instances)} 个实例运行中")

    def selected_pids() -> list[int]:
        return [int(item) for item in tree.selection()]

    def kill_selected() -> None:
        for pid in selected_pids():
            kill(pid)
        time.sleep(0.5)
        refresh()

    def kill_all() -> None:
        if not instances:
            return
        if messagebox.askyesno("确认", f"终止全部 {len(instances)} 个工作台进程？"):
            for inst in instances:
                kill(inst.pid)
            time.sleep(0.5)
            refresh()

    def start() -> None:
        port, token, template = port_var.get().strip(), token_var.get().strip(), template_var.get().strip()
        if not port.isdigit():
            status.set("端口必须是数字")
            return
        if template not in available_templates():
            status.set("请选择模版目录中的有效模板")
            return
        if not port_free(port):
            status.set(f"端口 {port} 已被占用——请先终止旧实例或用「重启」")
            return
        save_config(port, token, template)
        start_instance(port, token, template)
        status.set(f"已启动新实例（端口 {port}，模板 {template}），等待就绪…")
        root.after(3000, refresh)

    def restart() -> None:
        port, token, template = port_var.get().strip(), token_var.get().strip(), template_var.get().strip()
        if not port.isdigit() or template not in available_templates():
            status.set("请输入有效端口并选择模版目录中的有效模板")
            return
        victims = [inst for inst in instances if inst.port == port or int(port) in inst.listening_ports]
        for inst in victims:
            kill(inst.pid)
        save_config(port, token, template)
        time.sleep(0.8)
        start_instance(port, token, template)
        status.set(f"已重启（端口 {port}，模板 {template}，清理 {len(victims)} 个残留），等待就绪…")
        root.after(4000, refresh)

    def open_workbench() -> None:
        port = port_var.get().strip() or DEFAULT_PORT
        webbrowser.open(f"http://127.0.0.1:{port}/")

    def copy_token() -> None:
        root.clipboard_clear()
        root.clipboard_append(token_var.get().strip())
        status.set("令牌已复制")

    bar = tk.Frame(root)
    bar.pack(fill="x", padx=10, pady=4)
    tk.Label(bar, text="端口").pack(side="left")
    port_var = tk.StringVar(value=config.get("port", DEFAULT_PORT))
    tk.Entry(bar, textvariable=port_var, width=7).pack(side="left", padx=(2, 10))
    tk.Label(bar, text="管理员令牌").pack(side="left")
    token_var = tk.StringVar(value=config.get("token") or "abcd1234")
    tk.Entry(bar, textvariable=token_var, width=24).pack(side="left", padx=(2, 10))
    tk.Label(bar, text="当前模板").pack(side="left")
    templates = available_templates()
    selected_template = config.get("template", DEFAULT_TEMPLATE)
    template_var = tk.StringVar(value=selected_template if selected_template in templates else (DEFAULT_TEMPLATE if DEFAULT_TEMPLATE in templates else (templates[-1] if templates else "")))
    ttk.Combobox(bar, textvariable=template_var, values=templates, state="readonly", width=34).pack(side="left", padx=(2, 10))
    for text, cmd in [("刷新", refresh), ("启动", start), ("重启(清残留)", restart),
                      ("终止选中", kill_selected), ("全部终止", kill_all),
                      ("打开工作台", open_workbench), ("复制令牌", copy_token)]:
        tk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)

    tk.Label(root, textvariable=status, anchor="w", fg="#8a5a00").pack(fill="x", padx=10, pady=(0, 8))
    refresh()
    root.mainloop()


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else main())
