"""Copy a Homebrew binary and the libraries it needs into an application bundle."""

import os
import shutil
import subprocess
import sys

HOMEBREW_PREFIXES = ("/opt/homebrew", "/usr/local")


def linked_libraries(path, origin):
    """Return (name as linked, file on disk) for each Homebrew library that path loads."""
    output = subprocess.check_output(["otool", "-L", path], text=True)
    libraries = []
    for line in output.splitlines()[1:]:
        name = line.strip().split(" (")[0]
        source = resolve(name, origin)
        if source:
            libraries.append((name, source))
    return libraries


def resolve(name, origin):
    if name.startswith(tuple(prefix + "/" for prefix in HOMEBREW_PREFIXES)):
        return name
    base = os.path.basename(name)
    folders = {os.path.dirname(origin), os.path.dirname(os.path.realpath(origin))}
    if name.startswith("@loader_path/"):
        candidates = [os.path.join(folder, name[len("@loader_path/"):]) for folder in folders]
    elif name.startswith("@rpath/"):
        candidates = [os.path.join(folder, base) for folder in folders]
        candidates += [os.path.join(folder, "..", "lib", base) for folder in folders]
        candidates += [os.path.join(prefix, "lib", base) for prefix in HOMEBREW_PREFIXES]
    else:
        return None
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.realpath(candidate)
    return None


def copy_binary(source, destination):
    shutil.copy2(source, destination)
    os.chmod(destination, 0o755)


def sign(path):
    subprocess.check_call(["codesign", "--force", "--sign", "-", path])


def bundle(binary, destination_dir, frameworks_dir):
    os.makedirs(destination_dir, exist_ok=True)
    os.makedirs(frameworks_dir, exist_ok=True)
    target = os.path.join(destination_dir, os.path.basename(binary))
    copy_binary(binary, target)
    pending = [(target, binary)]
    seen = set()
    while pending:
        current, origin = pending.pop()
        for name, source in linked_libraries(current, origin):
            base = os.path.basename(name)
            bundled = os.path.join(frameworks_dir, base)
            if base not in seen:
                seen.add(base)
                if not os.path.exists(bundled):
                    copy_binary(source, bundled)
                pending.append((bundled, source))
            rewrite(current, name, f"@rpath/{base}")
    for path in [target, *seen_paths(frameworks_dir)]:
        if path.endswith(".dylib"):
            subprocess.check_call(["install_name_tool", "-id", f"@rpath/{os.path.basename(path)}", path])
            ensure_rpath(path, "@loader_path")
        else:
            ensure_rpath(path, "@executable_path/../Frameworks")
        sign(path)


def seen_paths(frameworks_dir):
    return [
        os.path.join(frameworks_dir, name)
        for name in os.listdir(frameworks_dir)
        if name.endswith(".dylib")
    ]


def rewrite(path, old, new):
    if old == new:
        return
    subprocess.check_call(["install_name_tool", "-change", old, new, path])


def ensure_rpath(path, rpath):
    listed = subprocess.check_output(["otool", "-l", path], text=True)
    if rpath in listed:
        return
    subprocess.check_call(["install_name_tool", "-add_rpath", rpath, path])


if __name__ == "__main__":
    bundle(sys.argv[1], sys.argv[2], sys.argv[3])
