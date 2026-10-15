#!/usr/bin/env python3
"""Original portable whitelist installer. No network, shell or overwrite."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

VERSION = "0.1.0"
NAME = "lookatshow-mail-plan"
WHITELIST = ("lookatshow_mail_plan.py", "LICENSE", "NOTICE.md", "examples/campaign.example.json",
             "examples/contacts.example.csv", "examples/suppression.example.csv")


class InstallError(Exception):
    pass


def safe_path(value):
    path = Path(os.path.abspath(str(value)))
    if path in {Path("/"), Path("/usr"), Path("/usr/local"), Path("/bin"), Path("/sbin"),
                Path("/etc"), Path("/var"), Path("/tmp"), Path("/opt"), Path.home()}:
        raise InstallError("PREFIX_TOO_BROAD")
    for component in (path,) + tuple(path.parents):
        try:
            mode = os.lstat(component).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise InstallError("SYMLINK_PATH_REFUSED")
    return path


def exclusive_write(path, content, mode):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)


def install(prefix):
    if sys.version_info < (3, 9):
        raise InstallError("PYTHON_VERSION")
    source = Path(__file__).resolve().parent
    content = {}
    for relative in WHITELIST:
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise InstallError("SOURCE_WHITELIST_INVALID")
        # An intermediate symlink must not turn an allowed relative path into a leak.
        for component in path.relative_to(source).parts[:-1]:
            if (source / component).is_symlink():
                raise InstallError("SOURCE_WHITELIST_INVALID")
        if source not in path.resolve().parents:
            raise InstallError("SOURCE_WHITELIST_INVALID")
        content[relative] = path.read_bytes()
    prefix = safe_path(prefix)
    if prefix.exists() and not prefix.is_dir():
        raise InstallError("PREFIX_NOT_DIRECTORY")
    payload = safe_path(prefix / "share" / NAME / VERSION)
    binary = safe_path(prefix / "bin" / NAME)
    if payload.exists() or binary.exists():
        raise InstallError("DESTINATION_EXISTS_NO_OVERWRITE")
    if prefix.exists():
        if any(prefix.iterdir()):
            raise InstallError("PREFIX_NOT_EMPTY")
    prefix.mkdir(parents=True, exist_ok=True)
    safe_path(prefix)
    if any(prefix.iterdir()):
        raise InstallError("PREFIX_NOT_EMPTY")
    payload.parent.mkdir(parents=True, exist_ok=True)
    safe_path(payload.parent)
    payload.mkdir()
    (payload / "examples").mkdir()
    for relative in WHITELIST:
        exclusive_write(payload / relative, content[relative], 0o644)
    binary.parent.mkdir(parents=True, exist_ok=True)
    safe_path(binary.parent)
    wrapper = ("#!/usr/bin/env python3\nimport runpy\nfrom pathlib import Path\n"
               "runpy.run_path(str(Path(__file__).resolve().parents[1] / 'share' / "
               f"'{NAME}' / '{VERSION}' / 'lookatshow_mail_plan.py'), run_name='__main__')\n")
    exclusive_write(binary, wrapper.encode("utf-8"), 0o755)
    return {"ok": True, "command": NAME, "version": VERSION,
            "installed_whitelist_files": len(WHITELIST), "network_used": False,
            "overwrite_supported": False, "path_setup_required": True}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InstallError("CLI_USAGE")


def main(argv=None):
    try:
        parser = Parser(description="Install only the offline CLI and synthetic fixtures; refuses overwrite and symlinks.")
        parser.add_argument("--prefix", required=True, help="Dedicated install prefix, for example a .local directory.")
        args = parser.parse_args(argv)
        result = install(args.prefix)
        print(json.dumps(result, sort_keys=True))
        return 0
    except InstallError as error:
        print(json.dumps({"ok": False, "error": {"code": str(error), "message": "Installation refused safely."}}, sort_keys=True))
        return 2
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"ok": False, "error": {"code": "INSTALL_FAILED", "message": "Local installation failed; no supplied paths are exposed."}}, sort_keys=True))
        return 3


if __name__ == "__main__":
    sys.exit(main())
