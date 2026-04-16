#!/usr/bin/env python3
"""Manage MeshCore firmware sources for the simulator.

Provides a registry (firmware.json) of firmware source trees and commands
to clone, update, build, and inspect them.

Usage:
    python3 tools/firmware.py list
    python3 tools/firmware.py add scope https://github.com/ripplebiz/MeshCore.git --branch default-scope
    python3 tools/firmware.py remove scope
    python3 tools/firmware.py init
    python3 tools/firmware.py update [name]
    python3 tools/firmware.py build [--build-dir DIR] [--clean] [--only X,Y]
    python3 tools/firmware.py status
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import re
import glob as globmod

REGISTRY_FILE = "firmware.json"
DEFAULT_NAME = "default"
DEFAULT_PATH = "MeshCore"


def project_root():
    """Find project root by looking for CMakeLists.txt."""
    d = os.path.dirname(os.path.abspath(__file__))
    while d != "/":
        if os.path.isfile(os.path.join(d, "CMakeLists.txt")):
            return d
        d = os.path.dirname(d)
    # Fallback: parent of tools/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = project_root()


def registry_path():
    return os.path.join(ROOT, REGISTRY_FILE)


def load_registry():
    """Load firmware.json, return dict. Creates empty if missing."""
    path = registry_path()
    if not os.path.isfile(path):
        return {"sources": {}}
    with open(path) as f:
        return json.load(f)


def save_registry(data):
    """Atomic write of firmware.json."""
    path = registry_path()
    fd, tmp = tempfile.mkstemp(dir=ROOT, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except:
        os.unlink(tmp)
        raise


def validate_name(name):
    """Name must be alphanumeric, hyphens, underscores."""
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', name):
        print(f"Error: invalid name '{name}' — use alphanumeric, hyphens, underscores", file=sys.stderr)
        sys.exit(1)


def derive_path(name):
    """Derive default on-disk path from source name."""
    if name == DEFAULT_NAME:
        return DEFAULT_PATH
    return f"MeshCore-{name}"


def abs_source_path(source):
    """Return absolute path for a source entry."""
    p = source["path"]
    if os.path.isabs(p):
        return p
    return os.path.join(ROOT, p)


def validate_meshcore_tree(path):
    """Check that path looks like a MeshCore source tree."""
    marker = os.path.join(path, "src", "Mesh.h")
    return os.path.isfile(marker)


def git_clone(repo, path, branch=None, tag=None):
    """Clone a git repo. Returns True on success."""
    cmd = ["git", "clone"]
    if branch:
        cmd += ["--branch", branch]
    elif tag:
        cmd += ["--branch", tag]
    cmd += [repo, path]
    print(f"  Cloning {repo} -> {path}", file=sys.stderr)
    if branch:
        print(f"  Branch: {branch}", file=sys.stderr)
    elif tag:
        print(f"  Tag: {tag}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: git clone failed:\n{result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def git_pull(path):
    """Pull latest changes (fast-forward only). Returns (success, new_head)."""
    result = subprocess.run(
        ["git", "-C", path, "pull", "--ff-only"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    head = git_info(path).get("commit", "unknown")
    return True, head


def git_info(path):
    """Return git info dict: branch, commit, dirty, tag."""
    info = {}
    if not os.path.isdir(os.path.join(path, ".git")):
        return {"error": "not a git repo"}

    # Branch
    r = subprocess.run(["git", "-C", path, "branch", "--show-current"],
                       capture_output=True, text=True)
    branch = r.stdout.strip()
    info["branch"] = branch if branch else None

    # Commit
    r = subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    info["commit"] = r.stdout.strip()

    # Tag (exact match)
    r = subprocess.run(["git", "-C", path, "describe", "--tags", "--exact-match"],
                       capture_output=True, text=True)
    info["tag"] = r.stdout.strip() if r.returncode == 0 else None

    # Dirty
    r = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                       capture_output=True, text=True)
    info["dirty"] = bool(r.stdout.strip())

    # Remote URL
    r = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    info["remote"] = r.stdout.strip() if r.returncode == 0 else None

    return info


def find_built_plugins(build_dir):
    """Scan build directory for fw_*.so files."""
    orch_dir = os.path.join(build_dir, "orchestrator")
    if not os.path.isdir(orch_dir):
        return {}
    plugins = {}
    for f in os.listdir(orch_dir):
        if f.startswith("fw_") and f.endswith(".so"):
            name = f[:-3]  # strip .so
            path = os.path.join(orch_dir, f)
            plugins[name] = {
                "path": path,
                "size": os.path.getsize(path),
                "mtime": os.path.getmtime(path),
            }
    return plugins


def scan_test_plugins(test_dir):
    """Parse _requires_plugins from test/*.json files."""
    required = {}  # plugin_name -> [test_file, ...]
    pattern = os.path.join(test_dir, "t*.json")
    for path in sorted(globmod.glob(pattern)):
        try:
            with open(path) as f:
                data = json.load(f)
            for p in data.get("_requires_plugins", []):
                required.setdefault(p, []).append(os.path.basename(path))
        except (json.JSONDecodeError, KeyError):
            pass
    return required


def build_cmake_args(registry, build_dir, build_type=None, only=None):
    """Construct cmake arguments from registry sources."""
    sources = registry.get("sources", {})
    cmake_args = []

    # Default source -> MESHCORE_DIR
    if DEFAULT_NAME in sources:
        default_path = abs_source_path(sources[DEFAULT_NAME])
        cmake_args.append(f"-DMESHCORE_DIR={default_path}")

    # Extra sources -> FIRMWARE_PLUGINS
    extras = {}
    for name, src in sources.items():
        if name == DEFAULT_NAME:
            continue
        if only and name not in only:
            continue
        extras[name] = abs_source_path(src)

    if extras:
        plugins = ";".join(f"{name}={path}" for name, path in extras.items())
        cmake_args.append(f"-DFIRMWARE_PLUGINS={plugins}")

    if build_type:
        cmake_args.append(f"-DCMAKE_BUILD_TYPE={build_type}")

    return cmake_args


# ---- Subcommands ----

def cmd_list(args):
    """List registered firmware sources."""
    reg = load_registry()
    sources = reg.get("sources", {})

    if not sources:
        print("No firmware sources registered. Run 'firmware.py add' or create firmware.json.", file=sys.stderr)
        sys.exit(0)

    if args.json_output:
        # Enrich with on-disk/build status
        result = {}
        for name, src in sources.items():
            entry = dict(src)
            apath = abs_source_path(src)
            entry["exists"] = os.path.isdir(apath)
            entry["valid"] = validate_meshcore_tree(apath) if entry["exists"] else False
            if entry["exists"]:
                entry["git"] = git_info(apath)
            result[name] = entry

        if args.build_dir:
            plugins = find_built_plugins(args.build_dir)
            for name in result:
                fw_name = "fw_default" if name == DEFAULT_NAME else f"fw_{name}"
                result[name]["built"] = fw_name in plugins
        json.dump(result, sys.stdout, indent=2)
        print()
        return

    # Table output
    build_plugins = {}
    if args.build_dir:
        build_plugins = find_built_plugins(args.build_dir)

    header = f"{'Name':<15} {'Plugin Name':<15} {'Path':<30} {'Branch/Tag':<20} {'On Disk':<10} {'Valid':<8}"
    if args.build_dir:
        header += f" {'Built':<8}"
    print(header)
    print("-" * len(header))

    for name, src in sorted(sources.items()):
        apath = abs_source_path(src)
        exists = os.path.isdir(apath)
        valid = validate_meshcore_tree(apath) if exists else False
        ref = src.get("tag") or src.get("branch", "-")
        if src.get("tag"):
            ref = f"tag:{ref}"

        fw_name = f"fw_{name}"
        row = f"{name:<15} {fw_name:<15} {src['path']:<30} {ref:<20} {'yes' if exists else 'NO':<10} {'yes' if valid else 'NO':<8}"
        if args.build_dir:
            built = fw_name in build_plugins
            row += f" {'yes' if built else 'no':<8}"
        print(row)


def cmd_add(args):
    """Register and optionally clone a firmware source."""
    validate_name(args.name)

    if args.branch and args.tag:
        print("Error: --branch and --tag are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    reg = load_registry()
    sources = reg.setdefault("sources", {})

    if args.name in sources:
        print(f"Error: source '{args.name}' already registered. Remove it first.", file=sys.stderr)
        sys.exit(1)

    path = args.path or derive_path(args.name)
    entry = {
        "repo": args.repo,
        "path": path,
    }
    if args.tag:
        entry["tag"] = args.tag
    else:
        entry["branch"] = args.branch or "main"

    apath = abs_source_path(entry)

    if not args.no_clone:
        if os.path.isdir(apath):
            print(f"Directory {apath} already exists.", file=sys.stderr)
            if validate_meshcore_tree(apath):
                print("  Looks like a valid MeshCore tree. Registering without cloning.", file=sys.stderr)
            else:
                print("  Warning: does not look like a valid MeshCore tree.", file=sys.stderr)
        else:
            ok = git_clone(args.repo, apath, branch=entry.get("branch"), tag=entry.get("tag"))
            if not ok:
                sys.exit(1)
            if not validate_meshcore_tree(apath):
                print(f"Warning: cloned tree at {apath} doesn't contain src/Mesh.h", file=sys.stderr)

    sources[args.name] = entry
    save_registry(reg)
    print(f"Registered '{args.name}' -> {path}", file=sys.stderr)


def cmd_remove(args):
    """Unregister a firmware source."""
    reg = load_registry()
    sources = reg.get("sources", {})

    if args.name not in sources:
        print(f"Error: source '{args.name}' not registered", file=sys.stderr)
        sys.exit(1)

    if args.name == DEFAULT_NAME and not args.force:
        print("Error: removing 'default' requires --force", file=sys.stderr)
        sys.exit(1)

    src = sources[args.name]
    apath = abs_source_path(src)

    if args.delete and os.path.isdir(apath):
        print(f"Deleting {apath}...", file=sys.stderr)
        shutil.rmtree(apath)

    del sources[args.name]
    save_registry(reg)
    print(f"Removed '{args.name}'", file=sys.stderr)


def cmd_init(args):
    """Clone all registered sources not present on disk."""
    reg = load_registry()
    sources = reg.get("sources", {})

    if not sources:
        print("No sources registered in firmware.json", file=sys.stderr)
        sys.exit(1)

    cloned = 0
    skipped = 0
    errors = 0

    for name, src in sorted(sources.items()):
        apath = abs_source_path(src)
        if os.path.isdir(apath):
            if validate_meshcore_tree(apath):
                print(f"  {name}: already exists at {src['path']} (valid)", file=sys.stderr)
            else:
                print(f"  {name}: directory exists at {src['path']} but NOT a valid MeshCore tree", file=sys.stderr)
            skipped += 1
            continue

        ok = git_clone(src["repo"], apath, branch=src.get("branch"), tag=src.get("tag"))
        if ok:
            if validate_meshcore_tree(apath):
                print(f"  {name}: cloned OK", file=sys.stderr)
                cloned += 1
            else:
                print(f"  {name}: cloned but src/Mesh.h not found — check the branch/tag", file=sys.stderr)
                errors += 1
        else:
            errors += 1

    print(f"\nInit complete: {cloned} cloned, {skipped} already present, {errors} errors", file=sys.stderr)
    if errors:
        sys.exit(1)


def cmd_update(args):
    """Pull latest changes for one or all sources."""
    reg = load_registry()
    sources = reg.get("sources", {})

    targets = {}
    if args.name:
        if args.name not in sources:
            print(f"Error: source '{args.name}' not registered", file=sys.stderr)
            sys.exit(1)
        targets[args.name] = sources[args.name]
    else:
        targets = sources

    errors = 0
    for name, src in sorted(targets.items()):
        apath = abs_source_path(src)
        if not os.path.isdir(apath):
            print(f"  {name}: not on disk, run 'init' first", file=sys.stderr)
            errors += 1
            continue

        if src.get("tag"):
            print(f"  {name}: pinned to tag '{src['tag']}' — skipping", file=sys.stderr)
            continue

        print(f"  {name}: pulling...", file=sys.stderr, end="")
        ok, info = git_pull(apath)
        if ok:
            print(f" OK ({info})", file=sys.stderr)
        else:
            print(f" FAILED: {info}", file=sys.stderr)
            errors += 1

    if errors:
        sys.exit(1)


def cmd_build(args):
    """Configure and build the orchestrator with firmware plugins."""
    reg = load_registry()
    sources = reg.get("sources", {})

    if not sources:
        print("No sources registered in firmware.json", file=sys.stderr)
        sys.exit(1)

    # Validate all sources exist on disk
    missing = []
    for name, src in sources.items():
        only_set = set(args.only.split(",")) if args.only else None
        if only_set and name != DEFAULT_NAME and name not in only_set:
            continue
        apath = abs_source_path(src)
        if not os.path.isdir(apath):
            missing.append(name)

    if missing:
        print(f"Error: sources not on disk: {', '.join(missing)}", file=sys.stderr)
        print("Run 'firmware.py init' first.", file=sys.stderr)
        sys.exit(1)

    build_dir = args.build_dir
    only_set = set(args.only.split(",")) if args.only else None

    cmake_args = build_cmake_args(reg, build_dir, build_type=args.build_type, only=only_set)

    if args.clean and os.path.isdir(build_dir):
        print(f"Cleaning {build_dir}...", file=sys.stderr)
        shutil.rmtree(build_dir)

    # Configure
    configure_cmd = ["cmake", "-S", ROOT, "-B", build_dir] + cmake_args
    print(f"Configuring: {' '.join(configure_cmd)}", file=sys.stderr)
    r = subprocess.run(configure_cmd)
    if r.returncode != 0:
        print("Error: cmake configure failed", file=sys.stderr)
        sys.exit(1)

    # Build
    build_cmd = ["cmake", "--build", build_dir]
    if args.jobs:
        build_cmd += ["-j", str(args.jobs)]
    print(f"Building: {' '.join(build_cmd)}", file=sys.stderr)
    r = subprocess.run(build_cmd)
    if r.returncode != 0:
        print("Error: build failed", file=sys.stderr)
        sys.exit(1)

    # Report results
    orch_bin = os.path.join(build_dir, "orchestrator", "orchestrator")
    if os.path.isfile(orch_bin):
        size_mb = os.path.getsize(orch_bin) / (1024 * 1024)
        print(f"\nOrchestrator: {orch_bin} ({size_mb:.1f} MB)", file=sys.stderr)

    plugins = find_built_plugins(build_dir)
    print(f"Plugins ({len(plugins)}):", file=sys.stderr)
    for name, info in sorted(plugins.items()):
        size_mb = info["size"] / (1024 * 1024)
        print(f"  {name}.so  ({size_mb:.1f} MB)", file=sys.stderr)


def cmd_status(args):
    """Show detailed status of sources, plugins, and test requirements."""
    reg = load_registry()
    sources = reg.get("sources", {})

    if not sources:
        print("No firmware sources registered.", file=sys.stderr)
        return

    build_dir = args.build_dir

    print("=== Firmware Sources ===")
    for name, src in sorted(sources.items()):
        apath = abs_source_path(src)
        exists = os.path.isdir(apath)
        marker = " (default)" if name == DEFAULT_NAME else ""

        print(f"\n  {name}{marker}:")
        print(f"    Path:   {src['path']}")
        print(f"    Repo:   {src['repo']}")
        ref_type = "tag" if src.get("tag") else "branch"
        ref_val = src.get("tag") or src.get("branch", "-")
        print(f"    {ref_type.capitalize()}: {ref_val}")

        if exists:
            info = git_info(apath)
            if "error" not in info:
                print(f"    Commit: {info.get('commit', '?')}")
                actual_branch = info.get("branch")
                if actual_branch:
                    print(f"    Actual branch: {actual_branch}")
                if info.get("tag"):
                    print(f"    Actual tag: {info['tag']}")
                if info.get("dirty"):
                    print(f"    Status: DIRTY (uncommitted changes)")
                else:
                    print(f"    Status: clean")
            else:
                print(f"    Git: {info['error']}")
            print(f"    Valid: {'yes' if validate_meshcore_tree(apath) else 'NO (src/Mesh.h missing)'}")
        else:
            print(f"    On disk: NO — run 'firmware.py init'")

    # Built plugins
    print("\n=== Built Plugins ===")
    plugins = find_built_plugins(build_dir)
    if plugins:
        for name, info in sorted(plugins.items()):
            size_mb = info["size"] / (1024 * 1024)
            from datetime import datetime
            mtime = datetime.fromtimestamp(info["mtime"]).strftime("%Y-%m-%d %H:%M")
            print(f"  {name}.so  {size_mb:.1f} MB  built {mtime}")
    else:
        print(f"  No plugins found in {build_dir}/orchestrator/")

    # Test requirements
    test_dir = os.path.join(ROOT, "test")
    print("\n=== Test Plugin Requirements ===")
    test_reqs = scan_test_plugins(test_dir)
    if test_reqs:
        for plugin, tests in sorted(test_reqs.items()):
            available = plugin in plugins
            status = "available" if available else "MISSING"
            print(f"  {plugin}: {status} (needed by: {', '.join(tests)})")
    else:
        print("  No tests require specific plugins.")

    # Summary
    missing_plugins = [p for p in test_reqs if p not in plugins]
    if missing_plugins:
        print(f"\n  Note: {len(missing_plugins)} required plugin(s) not built: {', '.join(missing_plugins)}")
        print("  Some tests will be skipped. Add sources and rebuild to enable them.")


def main():
    parser = argparse.ArgumentParser(
        prog="firmware.py",
        description="Manage MeshCore firmware sources for the simulator.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")

    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="list registered firmware sources")
    p_list.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    p_list.add_argument("--build-dir", default="build", help="build directory to check for .so files (default: build)")

    # add
    p_add = sub.add_parser("add", help="register and clone a firmware source")
    p_add.add_argument("name", help="source name (e.g. 'scope', 'v113')")
    p_add.add_argument("repo", help="git repository URL")
    p_add.add_argument("--branch", "-b", help="git branch (default: main)")
    p_add.add_argument("--tag", "-t", help="git tag (pinned, skipped by update)")
    p_add.add_argument("--path", "-p", help="on-disk path (default: auto-derived)")
    p_add.add_argument("--no-clone", action="store_true", help="register only, don't clone")

    # remove
    p_rm = sub.add_parser("remove", help="unregister a firmware source")
    p_rm.add_argument("name", help="source name to remove")
    p_rm.add_argument("--delete", action="store_true", help="also delete the directory")
    p_rm.add_argument("--force", action="store_true", help="required to remove 'default'")

    # init
    sub.add_parser("init", help="clone all registered sources not present on disk")

    # update
    p_up = sub.add_parser("update", help="pull latest changes for sources")
    p_up.add_argument("name", nargs="?", help="specific source to update (default: all)")

    # build
    p_build = sub.add_parser("build", help="configure and build orchestrator with plugins")
    p_build.add_argument("--build-dir", default="build", help="build directory (default: build)")
    p_build.add_argument("--build-type", help="CMake build type (e.g. Release, NativeRelease)")
    p_build.add_argument("--clean", action="store_true", help="remove build dir before configuring")
    p_build.add_argument("--only", help="comma-separated list of non-default sources to include")
    p_build.add_argument("-j", "--jobs", type=int, help="parallel build jobs")

    # status
    p_status = sub.add_parser("status", help="detailed source, plugin, and test status")
    p_status.add_argument("--build-dir", default="build", help="build directory (default: build)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    {
        "list": cmd_list,
        "add": cmd_add,
        "remove": cmd_remove,
        "init": cmd_init,
        "update": cmd_update,
        "build": cmd_build,
        "status": cmd_status,
    }[args.command](args)


if __name__ == "__main__":
    main()
