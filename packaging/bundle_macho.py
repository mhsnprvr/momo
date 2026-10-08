"""Copy a Homebrew binary and the libraries it needs into an application bundle."""

import os
import shutil
import subprocess
import sys


def linked_libraries(path):
    output = subprocess.check_output(["otool", "-L", path], text=True)
    libraries = []
    for line in output.splitlines()[1:]:
        name = line.strip().split(" (")[0]
        if name.startswith("/opt/homebrew/") or name.startswith("/usr/local/"):
            libraries.append(name)
    return libraries


def copy_unsigned(source, destination):
    shutil.copy2(source, destination)
    os.chmod(destination, 0o755)
    subprocess.run(["codesign", "--remove-signature", destination], check=False)


def bundle(binary, destination_dir, frameworks_dir):
    os.makedirs(destination_dir, exist_ok=True)
    os.makedirs(frameworks_dir, exist_ok=True)
    target = os.path.join(destination_dir, os.path.basename(binary))
    copy_unsigned(binary, target)
    pending = [target]
    seen = set()
    while pending:
        current = pending.pop()
        for library in linked_libraries(current):
            bundled = os.path.join(frameworks_dir, os.path.basename(library))
            if library not in seen:
                seen.add(library)
                if not os.path.exists(bundled):
                    copy_unsigned(library, bundled)
                pending.append(bundled)
            rewrite(current, library, f"@rpath/{os.path.basename(library)}")
    for path in [target, *seen_paths(frameworks_dir)]:
        if path.endswith(".dylib"):
            subprocess.check_call(["install_name_tool", "-id", f"@rpath/{os.path.basename(path)}", path])
            ensure_rpath(path, "@loader_path")
        else:
            ensure_rpath(path, "@executable_path/../Frameworks")


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
