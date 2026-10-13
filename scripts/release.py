"""Build and validate release artifacts; run with Python 3.12 in release CI."""

import argparse
from email.parser import BytesParser
from pathlib import Path
import shutil
import subprocess
import tarfile
import tomllib
import zipfile


def run(*args):
    subprocess.run(args, check=True)


def read_toml(path):
    return tomllib.loads(Path(path).read_text())


def dependencies(lock):
    """Ignore only the local project's version when comparing lockfiles."""
    for package in lock["package"]:
        if package["name"] == "fastbuild123":
            package.pop("version")
    return lock


def verify(tag=None):
    version = read_toml("pyproject.toml")["project"]["version"]
    package = next(p for p in read_toml("uv.lock")["package"] if p["name"] == "fastbuild123")
    if package["version"] != version:
        raise ValueError("Project and lockfile versions differ")
    if tag:
        if tag != f"v{version}":
            raise ValueError("Tag and project versions differ")
        tagged = subprocess.check_output(["git", "rev-parse", f"{tag}^{{commit}}"], text=True).strip()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if tagged != head:
            raise ValueError("Checkout does not match the release tag")
    wheels = list(Path("dist").glob("*.whl"))
    sdists = list(Path("dist").glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one wheel and one source archive")
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Expected one wheel metadata file")
        wheel_metadata = archive.read(metadata[0])
    with tarfile.open(sdists[0]) as archive:
        metadata = [m for m in archive.getmembers() if m.name.count("/") == 1 and m.name.endswith("/PKG-INFO")]
        if len(metadata) != 1:
            raise ValueError("Expected one source archive metadata file")
        sdist_metadata = archive.extractfile(metadata[0]).read()
    for raw in (wheel_metadata, sdist_metadata):
        metadata = BytesParser().parsebytes(raw)
        if metadata["Name"] != "fastbuild123" or metadata["Version"] != version:
            raise ValueError("Distribution metadata does not match the project")
    print(f"Verified project, lockfile, distributions{', and tag' if tag else ''}: {version}")


def build():
    before = dependencies(read_toml("uv.lock"))
    # PSR has already updated pyproject.toml. Only refresh this package's version.
    run("uv", "lock", "--upgrade-package", "fastbuild123")
    if dependencies(read_toml("uv.lock")) != before:
        raise ValueError("Release build changed locked dependencies")
    shutil.rmtree("dist", ignore_errors=True)
    run("uv", "build")
    verify()
    archives = [*Path("dist").glob("*.whl"), *Path("dist").glob("*.tar.gz")]
    run("uvx", "--from", "twine==7.0.0", "twine", "check", "--strict", *map(str, archives))
    run("git", "add", "uv.lock")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--tag")
    args = parser.parse_args()
    if args.command == "build":
        build()
    else:
        verify(args.tag)
