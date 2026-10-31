#!/usr/bin/env python3
"""Turns running containers into Unraid-managed ones with a template, so the autostart switch works.

Usage: make_templates.py [--dry-run] <name> <icon-url> [webui] ...   (three values per container)
Everything else about a container stays as it is. A failed start goes back to the old container.
"""
import json
import re
import shutil
import subprocess
import sys
import time
import xml.sax.saxutils as sax

TEMPLATES = "/boot/config/plugins/dockerMan/templates-user"
AUTOSTART = "/var/lib/docker/unraid-autostart"
SECRET = re.compile(r"(pass|token|secret|key)", re.I)


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def config(name, target, value, kind, mode="", mask=False):
    attrs = {"Name": name, "Target": target, "Default": "", "Mode": mode, "Description": "", "Type": kind,
             "Display": "always", "Required": "false", "Mask": "true" if mask else "false"}
    joined = " ".join(f"{k}={sax.quoteattr(v)}" for k, v in attrs.items())
    return f"  <Config {joined}>{sax.escape(value)}</Config>"


def template(info, image, icon, webui):
    cfg, host = info["Config"], info["HostConfig"]
    name = info["Name"].lstrip("/")
    image_env = set(image["Config"].get("Env") or [])
    extra = [f"--restart={host['RestartPolicy']['Name']}"] + [f"--cap-add={c}" for c in host.get("CapAdd") or []]
    post = cfg["Cmd"] if cfg.get("Cmd") and cfg["Cmd"] != image["Config"].get("Cmd") else []
    lines = ['<?xml version="1.0"?>', '<Container version="2">', f"  <Name>{sax.escape(name)}</Name>",
             f"  <Repository>{sax.escape(cfg['Image'])}</Repository>", "  <Registry/>",
             f"  <Network>{sax.escape(host['NetworkMode'])}</Network>", "  <Shell>sh</Shell>",
             f"  <Privileged>{'true' if host.get('Privileged') else 'false'}</Privileged>",
             f"  <Overview>Teil von Home-Net: {sax.escape(name)}</Overview>", "  <Category>Tools:</Category>",
             f"  <WebUI>{sax.escape(webui)}</WebUI>", f"  <Icon>{sax.escape(icon)}</Icon>",
             f"  <ExtraParams>{sax.escape(' '.join(extra))}</ExtraParams>",
             f"  <PostArgs>{sax.escape(' '.join(post))}</PostArgs>"]
    for port, binds in (host.get("PortBindings") or {}).items():
        number, proto = port.split("/")
        for b in binds or []:
            lines.append(config(f"Port {number}", number, b["HostPort"], "Port", proto))
    for bind in host.get("Binds") or []:
        parts = bind.split(":")
        lines.append(config(f"Pfad {parts[1]}", parts[1], parts[0], "Path", parts[2] if len(parts) > 2 else "rw"))
    for entry in cfg.get("Env") or []:
        if entry not in image_env:
            key, _, value = entry.partition("=")
            lines.append(config(key, key, value, "Variable", mask=bool(SECRET.search(key))))
    lines.append("</Container>\n")
    return "\n".join(lines)


def recreate(info, image, icon, webui):
    cfg, host = info["Config"], info["HostConfig"]
    name = info["Name"].lstrip("/")
    image_env = set(image["Config"].get("Env") or [])
    args = ["docker", "run", "-d", "--name", name, "--restart", host["RestartPolicy"]["Name"],
            "--network", host["NetworkMode"]]
    for port, binds in (host.get("PortBindings") or {}).items():
        for b in binds or []:
            args += ["-p", f"{b['HostPort']}:{port}"]
    for bind in host.get("Binds") or []:
        args += ["-v", bind]
    for cap in host.get("CapAdd") or []:
        args += ["--cap-add", cap]
    for entry in cfg.get("Env") or []:
        if entry not in image_env:
            args += ["-e", entry]
    labels = {"net.unraid.docker.managed": "dockerman", "net.unraid.docker.icon": icon}
    if webui:
        labels["net.unraid.docker.webui"] = webui
    for key, value in labels.items():
        args += ["--label", f"{key}={value}"]
    args.append(cfg["Image"])
    if cfg.get("Cmd") and cfg["Cmd"] != image["Config"].get("Cmd"):
        args += cfg["Cmd"]
    backup = name + "-pw-alt"
    sh("docker", "rename", name, backup)
    sh("docker", "stop", "-t", "15", backup)
    try:
        sh(*args)
        time.sleep(6)
        new = json.loads(sh("docker", "inspect", name))[0]
        if not new["State"]["Running"] or sorted(new["Config"]["Env"]) != sorted(cfg["Env"]):
            raise RuntimeError("Prüfung fehlgeschlagen")
    except Exception as exc:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        sh("docker", "rename", backup, name)
        sh("docker", "start", name)
        raise SystemExit(f"{name}: zurück zum alten Container ({type(exc).__name__})")
    sh("docker", "rm", backup)


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    for i in range(0, len(argv), 3):
        name, icon, webui = argv[i:i + 3]
        info = json.loads(sh("docker", "inspect", name))[0]
        image = json.loads(sh("docker", "image", "inspect", info["Config"]["Image"]))[0]
        text = template(info, image, icon, webui)
        if dry:
            print(re.sub(r'(Mask="true">)[^<]*', r"\1***", text))
            continue
        path = f"{TEMPLATES}/my-{name}.xml"
        try:
            shutil.copy(path, path + ".pi-wartung.bak")
        except OSError:
            pass
        with open(path, "w") as f:
            f.write(text)
        recreate(info, image, icon, webui)
        print(name, "ok")
    if dry:
        return
    try:
        current = open(AUTOSTART).read().split("\n")
    except OSError:
        current = []
    names = [argv[i] for i in range(0, len(argv), 3)]
    known = {line.split()[0] for line in current if line.strip()}
    with open(AUTOSTART, "a") as f:
        for name in names:
            if name not in known:
                f.write(name + "\n")


if __name__ == "__main__":
    main()
