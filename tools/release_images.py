#!/usr/bin/env python3
"""Lists every container Home-Net builds itself, as the matrix for .github/workflows/release.yml.

Each entry: name (container and image name), context, dockerfile and platforms. A module whose module.json
requires certain architectures (requires.arch) is only built for those."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLATFORMS = {"amd64": "linux/amd64", "arm64": "linux/arm64"}


def images(repo=REPO):
    out = [{"name": "home-net-gateway", "context": ".", "dockerfile": "core/gateway/Dockerfile",
            "platforms": ",".join(PLATFORMS.values())}]
    for path in sorted((repo / "modules").glob("*/module.json")):
        module = json.loads(path.read_text())
        arch = module.get("requires", {}).get("arch") or list(PLATFORMS)
        for name, spec in module["services"].items():
            build = spec.get("build")
            if build is None:
                continue
            if isinstance(build, str):
                build = {"context": build}
            context = build["context"]
            out.append({"name": name, "context": context,
                        "dockerfile": f"{context}/{build.get('dockerfile', 'Dockerfile')}",
                        "platforms": ",".join(PLATFORMS[a] for a in arch)})
    return out


if __name__ == "__main__":
    json.dump(images(), sys.stdout)
    print()
