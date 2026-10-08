"""Rebuild the pinned Windows cct fork; requires Go 1.27.1 on the build machine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

SOURCE_URL = "https://codeload.github.com/ahmojo/codex-claude-transfer/zip/refs/tags/v2.0.0"
SOURCE_SHA256 = "577e668b780d35c5aa2f9fdba084ba5ccacac0ac1486ec06f0bc6e089c2ac0e4"
VERSION = "v2.0.0+large512.1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default=shutil.which("go"))
    parser.add_argument("--work", required=True, help="A local build/cache directory outside the skill")
    parser.add_argument("--output", required=True, help="New executable output path")
    args = parser.parse_args()
    if not args.go:
        raise RuntimeError("Install Go 1.27.1 or provide --go")
    version = subprocess.check_output([args.go, "version"], text=True)
    if "go1.27.1 " not in version:
        raise RuntimeError("The published build uses Go 1.27.1; use that version to reproduce its hash")
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "cct-v2.0.0-source.zip"
    if not archive.exists():
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Upstream source archive hash mismatch")
    source = work / "codex-claude-transfer-2.0.0"
    if not source.exists():
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                if not (work / entry.filename).resolve().is_relative_to(work):
                    raise RuntimeError("Unsafe source archive path")
            package.extractall(work)
    limits = source / "internal/bundle/limits.go"
    text = limits.read_text(encoding="utf-8")
    old = "MaxSessionBytes = 100 << 20 // 100 MiB"
    new = "MaxSessionBytes = 512 << 20 // 512 MiB; local large-session compatibility build"
    if old not in text and new not in text:
        raise RuntimeError("Unexpected upstream limits.go")
    limits.write_text(text.replace(old, new), encoding="utf-8")
    environment = os.environ.copy()
    environment.update({"GOTOOLCHAIN": "local", "CGO_ENABLED": "0", "GOOS": "windows", "GOARCH": "amd64",
                        "GOCACHE": str(work / "build-cache"), "GOMODCACHE": str(work / "module-cache"), "GOPATH": str(work / "gopath")})
    subprocess.run([args.go, "test", "./internal/bundle", "-run", "Test.*(Limit|Oversize|Checksum|ManifestBinding)", "-count=1"], cwd=source, env=environment, check=True)
    output = Path(args.output).resolve()
    if output.exists():
        raise RuntimeError("Output already exists; use a fresh filename and review before installation")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([args.go, "build", "-trimpath", "-ldflags=-s -w -X github.com/ahmojo/codex-claude-transfer/internal/cli.Version=" + VERSION,
                    "-o", str(output), "./cmd/cct"], cwd=source, env=environment, check=True)
    print(json.dumps({"Version": VERSION, "Sha256": hashlib.sha256(output.read_bytes()).hexdigest().upper()}))


if __name__ == "__main__":
    main()
