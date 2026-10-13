"""Exercise release policy in disposable repositories (Python 3.12, uv, git).

Run: python scripts/check_release.py [--build]
--build also builds/validates a real 0.1.7 release without publishing anything.
"""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PSR = ["uvx", "--from", "python-semantic-release==10.7.0", "semantic-release"]


def run(args, cwd, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{args}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def check(name, commits, expected, build=False):
    with tempfile.TemporaryDirectory(prefix="fastbuild-release-") as temp:
        repo = Path(temp) / "repo"
        run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(repo)], ROOT)
        run(["git", "switch", "-C", "main", "v0.1.6"], repo)
        run(["git", "remote", "set-url", "origin", "https://github.com/DevonPeroutky/FastBuild123d.git"], repo)
        run(["git", "config", "user.name", "Release test"], repo)
        run(["git", "config", "user.email", "release-test@example.invalid"], repo)
        for file in ("pyproject.toml", "scripts/release.py"):
            (repo / file).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / file, repo / file)
        run(["git", "add", "."], repo)
        run(["git", "commit", "-m", "chore: configure automated releases"], repo)
        for commit in commits:
            run(["git", "commit", "--allow-empty", "-m", commit], repo)
        before = run(["git", "rev-parse", "HEAD"], repo)
        output = Path(temp) / "outputs"
        env = {**os.environ, "GITHUB_OUTPUT": str(output)}
        # Never use a real credential in these local checks.
        env.pop("GH_TOKEN", None)
        args = [*PSR, "version", "--no-push", "--no-vcs-release"]
        if not build:
            args.append("--skip-build")
        run(args, repo, env)
        fields = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
        if expected is None:
            assert fields["released"] == "false", fields
            assert run(["git", "rev-parse", "HEAD"], repo) == before
        else:
            assert fields["released"] == "true", fields
            assert fields["tag"] == f"v{expected}", fields
            assert run(["git", "describe", "--exact-match", "--tags"], repo) == f"v{expected}"
            assert (repo / "CHANGELOG.md").exists()
            if build:
                run(["python", "scripts/release.py", "verify", "--tag", fields["tag"]], repo)
                changed = run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], repo).splitlines()
                assert "uv.lock" in changed, changed
                # Notes can be regenerated at the release commit, with no version step.
                run([*PSR, "--noop", "changelog", "--post-to-release-tag", fields["tag"]], repo, env)
                # Model two publish attempts using the retained files, not a rebuild.
                retained = Path(temp) / "retained"
                shutil.copytree(repo / "dist", retained)
                hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in retained.iterdir()}
                for _ in range(2):
                    shutil.rmtree(repo / "dist")
                    shutil.copytree(retained, repo / "dist")
                    run(["python", "scripts/release.py", "verify", "--tag", fields["tag"]], repo)
                    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (repo / "dist").iterdir()}
            output.write_text("")
            run(args, repo, env)
            assert "released=false" in output.read_text(), output.read_text()
        assert not run(["git", "status", "--porcelain"], repo)
        print(f"PASS {name}: {expected or 'no release'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    cases = [
        ("setup only", [], None),
        ("patch", ["fix: repair cache"], "0.1.7"),
        ("performance", ["perf: speed up build"], "0.1.7"),
        ("feature", ["feat: add control"], "0.2.0"),
        ("breaking subject", ["feat!: replace control"], "0.2.0"),
        ("breaking footer", ["fix: change behavior\n\nBREAKING CHANGE: new input format"], "0.2.0"),
        ("documentation", ["docs: describe usage"], None),
        ("maintenance", ["test: cover cache", "chore: tidy config"], None),
        ("mixed", ["fix: repair cache", "docs: describe usage", "feat: add control"], "0.2.0"),
    ]
    for name, commits, expected in cases:
        check(name, commits, expected, build=args.build and name == "patch")
