#!/usr/bin/env python3
"""Recreates a container with the same settings plus an Unraid icon label. Rolls back on failure."""
import json, subprocess, sys, time
name, icon = sys.argv[1], sys.argv[2]
def sh(*a, check=True):
    return subprocess.run(a, capture_output=True, text=True, check=check).stdout.strip()
info = json.loads(sh("docker", "inspect", name))[0]
cfg, host = info["Config"], info["HostConfig"]
image_env = set(json.loads(sh("docker", "image", "inspect", cfg["Image"]))[0]["Config"].get("Env") or [])
args = ["docker", "run", "-d", "--name", name]
pol = host["RestartPolicy"]["Name"]
if pol and pol != "no": args += ["--restart", pol]
if host["NetworkMode"] and host["NetworkMode"] != "default": args += ["--network", host["NetworkMode"]]
for port, binds in (host.get("PortBindings") or {}).items():
    for b in binds or []:
        args += ["-p", f"{b['HostIp'] + ':' if b['HostIp'] else ''}{b['HostPort']}:{port}"]
for b in host.get("Binds") or []: args += ["-v", b]
for c in host.get("CapAdd") or []: args += ["--cap-add", c]
if host.get("Privileged"): args.append("--privileged")
for e in cfg.get("Env") or []:
    if e not in image_env: args += ["-e", e]
labels = dict(cfg.get("Labels") or {}); labels["net.unraid.docker.icon"] = icon
image_labels = json.loads(sh("docker", "image", "inspect", cfg["Image"]))[0]["Config"].get("Labels") or {}
for k, v in labels.items():
    if image_labels.get(k) != v: args += ["--label", f"{k}={v}"]
args.append(cfg["Image"])
image_cmd = json.loads(sh("docker", "image", "inspect", cfg["Image"]))[0]["Config"].get("Cmd")
if cfg.get("Cmd") and cfg["Cmd"] != image_cmd: args += cfg["Cmd"]
backup = name + "-pw-alt"
sh("docker", "rename", name, backup); sh("docker", "stop", "-t", "15", backup)
try:
    sh(*args)
    time.sleep(6)
    new = json.loads(sh("docker", "inspect", name))[0]
    if not new["State"]["Running"]: raise RuntimeError("läuft nicht")
    if sorted(new["Config"]["Env"]) != sorted(cfg["Env"]): raise RuntimeError("Umgebung abweichend")
    print(name, "ok", new["Config"]["Labels"].get("net.unraid.docker.icon"))
except Exception as e:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    sh("docker", "rename", backup, name); sh("docker", "start", name)
    sys.exit(f"{name}: zurückgerollt ({type(e).__name__}: {str(e)[:80].split(' -e ')[0]})")
sh("docker", "rm", backup)
