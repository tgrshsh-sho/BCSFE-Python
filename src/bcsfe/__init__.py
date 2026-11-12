from __future__ import annotations

# Upstream version this fork is based on. Keep upstream's format: packaging and the
# editor_version field of exported saves rely on it.
__version__ = "3.6.0"
# Revision of the jp fork on top of __version__; bump it for every fork release.
__fork__ = "jp.2"

__app_name__ = "bcsfe"


def get_full_version() -> str:
    """Version shown to users: upstream base plus fork revision, e.g. ``3.6.0+jp.1``."""
    return f"{__version__}+{__fork__}"


try:
    from importlib.resources.abc import Traversable
except ImportError:
    pass

from bcsfe import core, cli

__all__ = ["core", "cli"]


def copy_to_data_dir(base_path: "Traversable", path: "Traversable"):
    if path.is_dir():
        for item in path.iterdir():
            copy_to_data_dir(base_path, item)
    else:
        to_add = str(path)
        base_s = str(base_path)
        if to_add.startswith(base_s):
            to_add = to_add[len(base_s) :]
        if to_add.startswith("/") or to_add.startswith("\\"):
            to_add = to_add[1:]

        new_path = core.Path.get_data_folder().add(to_add)
        data = path.read_bytes()

        new_path.parent().generate_dirs()

        new_path.write(core.Data(data))


def run():
    from bcsfe import __main__

    __main__.main()
