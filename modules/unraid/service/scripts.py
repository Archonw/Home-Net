"""User Scripts (the plugin by Squid) seen from the container.

Reading needs only the plugin's folders, mounted by module.json:
  - /boot/config/plugins/user.scripts: the scripts, their name and description files and schedule.json
  - /tmp/user.scripts: which scripts run right now (running/<dir>), their copies and logs (tmpScripts/<dir>/log.txt)

Starting and stopping must happen on the server itself. For that the module puts its own small script, the
"Home-Net Starter", into User Scripts. Once started (schedule "At Startup of Array") it waits for jobs in
/tmp/home-net-unraid and hands them to the plugin exactly like its own buttons do.
"""
import json
import os
import re
import tempfile
import time

US_DIR = os.environ.get("USERSCRIPTS_DIR", "/host/userscripts")
US_RUN = os.environ.get("USERSCRIPTS_RUN", "/host/userscripts-run")
QUEUE = os.environ.get("STARTER_QUEUE", "/host/requests")

HOST_BASE = "/boot/config/plugins/user.scripts"
STARTER = "home-net-starter"
STARTER_VERSION = 1
FREQUENCIES = ("disabled", "hourly", "daily", "weekly", "monthly", "start", "stop", "boot", "custom")
CRON_FIELD = r"[0-9A-Za-z*/,\-]+"
CRON_RE = re.compile(rf"^(@(hourly|daily|weekly|monthly|yearly|annually|midnight)|{CRON_FIELD}( {CRON_FIELD}){{4}})$")
BOOLEANS = ("foregroundOnly", "backgroundOnly", "arrayStarted", "clearLog", "noParity", "directPHP")
LOG_LIMIT = 64 * 1024

STARTER_SCRIPT = r'''#!/bin/bash
#name=Home-Net Starter
#description=Nimmt Aufträge aus Home-Net an (Skripte starten, abbrechen, Zeitplan). Zeitplan: At Startup of Array.
#backgroundOnly=true
#clearLog=true
# Angelegt vom Home-Net-Modul unraid, das diese Datei bei Bedarf neu schreibt. Bitte nicht von Hand ändern.
VERSION=@VERSION@
QUEUE=/tmp/home-net-unraid
BASE=/boot/config/plugins/user.scripts
PLUGIN=/usr/local/emhttp/plugins/user.scripts
RUN=/tmp/user.scripts
SELF="$BASE/scripts/home-net-starter/script"

say() { echo "$(date '+%d.%m.%Y %H:%M:%S') $*"; }

exec 9>/var/run/home-net-starter.lock
if ! flock -w 30 9; then say "Der Starter läuft schon."; exit 0; fi
mkdir -p "$QUEUE" && chmod 700 "$QUEUE"
say "Home-Net Starter $VERSION bereit."

valid() {
  case "$1" in ""|*/*|.*|*\'*|*\"*|*'`'*|*'$'*|*'\'*) return 1;; esac
  [ -f "$BASE/scripts/$1/script" ] && [ "$1" != "home-net-starter" ]
}

start_script() {
  setsid nohup "$PLUGIN/startCustom.php" "$BASE/scripts/$1/script" >/dev/null 2>&1 </dev/null &
  say "Gestartet: $1"
}

abort_script() {
  local pid
  pid=$(cat "$RUN/running/$1" 2>/dev/null)
  if [ -n "$pid" ]; then
    pkill -TERM -P "$pid"
    kill -9 "$pid" 2>/dev/null
  fi
  ps aux | grep -F "/tmp/user.scripts/tmpScripts/$1/" | grep -v grep | awk '{print $2}' | xargs -r kill -9
  rm -f "$RUN/running/$1"
  mkdir -p "$RUN/finished" && echo aborted > "$RUN/finished/$1"
  echo -e "Execution was aborted by user\n" >> "$RUN/tmpScripts/$1/log.txt"
  say "Abgebrochen: $1"
}

while true; do
  for job in "$QUEUE"/*.job; do
    [ -f "$job" ] || continue
    if [ "$(stat -c %u "$job")" != 0 ]; then rm -f "$job"; continue; fi
    action=$(sed -n 1p "$job")
    name=$(sed -n 2p "$job")
    rm -f "$job"
    case "$action" in
      run)   if valid "$name"; then start_script "$name"; else say "Unbekanntes Skript: $name"; fi ;;
      abort) if valid "$name"; then abort_script "$name"; else say "Unbekanntes Skript: $name"; fi ;;
      cron)  /usr/local/sbin/update_cron; say "Zeitplan übernommen." ;;
      *)     say "Unbekannter Auftrag: $action" ;;
    esac
  done
  echo "$VERSION" > "$QUEUE/alive.tmp" && mv -f "$QUEUE/alive.tmp" "$QUEUE/alive"
  if [ -f "$SELF" ] && ! grep -qx "VERSION=$VERSION" "$SELF"; then
    say "Neue Fassung gefunden, starte neu."
    setsid nohup bash -c "sleep 5; '$PLUGIN/startCustom.php' '$SELF'" >/dev/null 2>&1 </dev/null &
    exit 0
  fi
  sleep 2
done
'''.replace("@VERSION@", str(STARTER_VERSION))

STARTER_NAME = "Home-Net Starter"
STARTER_DESC = ("Nimmt Aufträge aus Home-Net an (Skripte starten, abbrechen, Zeitplan). "
                "Zeitplan bitte auf \"At Startup of Array\" stellen.")


def valid_dir(name):
    return (isinstance(name, str) and 0 < len(name) <= 100 and "/" not in name and not name.startswith(".")
            and not any(c in name for c in "'\"`$\\\n\r\t"))


def variables(path):
    """The #key=value lines at the top of a script, read like the plugin's getRawVariables()."""
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read(64 * 1024).splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip() or line.startswith("<?"):
            continue
        if not line.startswith("#"):
            break
        if "=" not in line[1:]:
            continue
        text = line.replace("#", "").replace("(", "").replace(")", "").strip()
        key, _, value = text.partition("=")
        out[key.strip()] = value
    for key in BOOLEANS:
        if key in out:
            out[key] = out[key].strip().lower() in ("1", "true", "yes", "on")
    return out


def read_text(path, limit=4096):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit).replace("\r", "").strip()
    except OSError:
        return ""


def write_atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


class UserScripts:
    def __init__(self, var_getter, base=None, run=None, queue=None):
        self.var = var_getter
        self.base = base or US_DIR
        self.run_dir = run or US_RUN
        self.queue = queue or QUEUE

    # -- reading --
    def installed(self):
        return os.path.isdir(os.path.join(self.base, "scripts"))

    def path(self, name):
        return os.path.join(self.base, "scripts", name, "script")

    def host_path(self, name):
        return f"{HOST_BASE}/scripts/{name}/script"

    def schedule(self):
        try:
            with open(os.path.join(self.base, "schedule.json")) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def starter(self):
        alive = os.path.join(self.queue, "alive")
        try:
            age = time.time() - os.path.getmtime(alive)
            version = int(read_text(alive) or 0)
        except (OSError, ValueError):
            age, version = None, 0
        running = age is not None and age < 20
        return {"installed": os.path.isfile(self.path(STARTER)), "running": running,
                "current": running and version == STARTER_VERSION}

    def ensure_starter(self):
        """Puts the starter into User Scripts or brings it up to date. Only if the plugin is installed."""
        if not self.installed():
            return False
        folder = os.path.join(self.base, "scripts", STARTER)
        wanted = {"script": STARTER_SCRIPT, "name": STARTER_NAME, "description": STARTER_DESC}
        changed = False
        os.makedirs(folder, exist_ok=True)
        for file, text in wanted.items():
            target = os.path.join(folder, file)
            try:
                with open(target, encoding="utf-8") as f:
                    if f.read() == text:
                        continue
            except OSError:
                pass
            write_atomic(target, text)
            changed = True
        return changed

    def names(self):
        try:
            entries = sorted(os.listdir(os.path.join(self.base, "scripts")), key=str.lower)
        except OSError:
            return []
        return [n for n in entries if valid_dir(n) and os.path.isfile(self.path(n))]

    def item(self, name):
        path = self.path(name)
        info = variables(path)
        title = read_text(os.path.join(self.base, "scripts", name, "name"), 200) or info.get("name") or name
        desc = read_text(os.path.join(self.base, "scripts", name, "description")) or info.get("description", "")
        entry = self.schedule().get(self.host_path(name), {})
        frequency = entry.get("frequency") if entry.get("frequency") in FREQUENCIES else "disabled"
        log = os.path.join(self.run_dir, "tmpScripts", name, "log.txt")
        try:
            last = int(os.path.getmtime(log))
        except OSError:
            last = 0
        return {"id": name, "name": title, "description": desc, "frequency": frequency,
                "custom": str(entry.get("custom") or "") if frequency == "custom" else "",
                "running": os.path.isfile(os.path.join(self.run_dir, "running", name)), "last": last,
                "foreground_only": bool(info.get("foregroundOnly")), "array_started": bool(info.get("arrayStarted")),
                "no_parity": bool(info.get("noParity")), "starter": name == STARTER,
                "argument": str(info.get("argumentDefault") or "")}

    def list(self):
        return [self.item(n) for n in self.names()]

    def get(self, name):
        if name not in self.names():
            raise LookupError(name)
        return self.item(name)

    def log(self, name):
        self.get(name)
        path = os.path.join(self.run_dir, "tmpScripts", name, "log.txt")
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                if size > LOG_LIMIT:
                    f.seek(size - LOG_LIMIT)
                text = f.read().decode("utf-8", "replace").replace("\r", "")
        except OSError:
            return {"log": "", "cut": False}
        if size > LOG_LIMIT:
            text = text.split("\n", 1)[-1]
        return {"log": text, "cut": size > LOG_LIMIT}

    # -- changing --
    def need_starter(self):
        state = self.starter()
        if not state["running"]:
            raise ValueError("Der Home-Net Starter läuft nicht. Bitte in Unraid unter Settings › User Scripts beim "
                             "„Home-Net Starter“ den Zeitplan auf „At Startup of Array“ stellen und einmal "
                             "„Run in background“ drücken.")

    def send(self, action, name="", wait=8.0):
        self.need_starter()
        fd, tmp = tempfile.mkstemp(dir=self.queue, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(f"{action}\n{name}\n")
        job = tmp[:-4] + ".job"
        os.rename(tmp, job)
        end = time.time() + wait
        while time.time() < end:
            if not os.path.exists(job):
                return True
            time.sleep(0.2)
        try:
            os.unlink(job)
        except OSError:
            return True
        raise ValueError("Der Home-Net Starter hat den Auftrag nicht angenommen. Bitte in User Scripts nachsehen, "
                         "ob er läuft.")

    def start(self, name):
        item = self.get(name)
        if item["starter"]:
            raise ValueError("Den Starter bitte in Unraid selbst starten.")
        if item["running"]:
            raise ValueError("Das Skript läuft schon.")
        if item["foreground_only"]:
            raise ValueError("Dieses Skript läuft nur im Vordergrund und lässt sich nur in Unraid starten.")
        var = self.var()
        if item["array_started"] and var.get("mdState") != "STARTED":
            raise ValueError("Dieses Skript läuft nur, wenn das Array gestartet ist.")
        if item["no_parity"] and str(var.get("mdResyncPos", "0")) not in ("", "0"):
            raise ValueError("Dieses Skript läuft nicht während einer Paritätsprüfung.")
        self.send("run", name)
        self.wait_running(name, True)
        return self.get(name)

    def abort(self, name):
        item = self.get(name)
        if item["starter"]:
            raise ValueError("Den Starter bitte in Unraid selbst anhalten.")
        if not item["running"]:
            raise ValueError("Das Skript läuft gerade nicht.")
        self.send("abort", name)
        self.wait_running(name, False)
        return self.get(name)

    def wait_running(self, name, state, seconds=5.0):
        end = time.time() + seconds
        flag = os.path.join(self.run_dir, "running", name)
        while time.time() < end and os.path.isfile(flag) != state:
            time.sleep(0.2)

    def set_schedule(self, name, frequency, custom=""):
        self.get(name)
        if frequency not in FREQUENCIES:
            raise ValueError("Unbekannter Zeitplan.")
        custom = " ".join(str(custom or "").split())
        if frequency == "custom" and not CRON_RE.match(custom):
            raise ValueError("Der eigene Zeitplan muss eine Cron-Angabe sein, zum Beispiel „30 3 * * *“.")
        if frequency != "custom":
            custom = ""
        self.need_starter()
        schedule = self.schedule()
        key = self.host_path(name)
        schedule[key] = {"script": key, "frequency": frequency,
                         "id": "schedule" + name.replace(".", "-").replace(" ", ""), "custom": custom}
        text = json.dumps(schedule, indent=4, ensure_ascii=False)
        cron = ""
        for entry in schedule.values():
            if isinstance(entry, dict) and entry.get("frequency") == "custom" and entry.get("custom"):
                cron += (f"{str(entry['custom']).strip()} /usr/local/emhttp/plugins/user.scripts/startCustom.php "
                         f"{entry.get('script')} > /dev/null 2>&1\n")
        write_atomic(os.path.join(self.base, "schedule.json"), text)
        if os.path.isdir(self.run_dir):
            write_atomic(os.path.join(self.run_dir, "schedule.json"), text)
        cron_file = os.path.join(self.base, "customSchedule.cron")
        if cron:
            write_atomic(cron_file, f"# Generated cron schedule for user.scripts\n{cron}\n")
        else:
            try:
                os.unlink(cron_file)
            except FileNotFoundError:
                pass
        self.send("cron")
        return self.get(name)

    def delete_log(self, name):
        item = self.get(name)
        if item["running"]:
            raise ValueError("Das Protokoll lässt sich erst löschen, wenn das Skript fertig ist.")
        try:
            os.unlink(os.path.join(self.run_dir, "tmpScripts", name, "log.txt"))
        except FileNotFoundError:
            pass
