from __future__ import annotations
import traceback

from bcsfe import cli, copy_to_data_dir

from bcsfe import core, __app_name__

import bcsfe
import argparse

from importlib import resources


def migrate(force: bool) -> str | None:
    """Copy the packaged files/ into the data folder when the version changed.

    Returns the version the data folder was last used with (None for a new folder).
    """
    # the full version, so that a new fork revision copies files/ (e.g. locales) again
    version = bcsfe.get_full_version()
    v_path = core.Path.get_data_folder().add("version.txt")
    if not v_path.exists():
        try:
            v_path.write(core.Data(version))
        except FileNotFoundError:
            print(
                "Failed to create data folder. Something is wrong with your user's permissions or anti-virus software"
            )
            print("Please set the --data-dir flag to something the editor can write to")
            pass
        vers = None
    else:
        vers = v_path.read().to_str().strip()
    if vers != version or force:
        p = resources.files(__app_name__).joinpath("files")
        copy_to_data_dir(p, p)
        v_path.write(core.Data(version))
    return vers


def apply_fork_defaults(previous_version: str | None) -> None:
    """First start of the fork on a data folder the stock editor used before.

    The stock editor writes every default into the config, so ``locale: en`` is
    there even if nobody chose it; the fork is Japanese first and switches it to
    ``ja`` once. A language chosen later is kept: from then on the folder
    records a fork version.
    """
    if previous_version is None or "+jp" in previous_version:
        return
    config = core.core_data.config
    if config.get_str(core.ConfigKey.LOCALE) != "en":
        return
    config.set(core.ConfigKey.LOCALE, "ja")
    core.core_data.local_manager = core.LocalManager()
    cli.color.color_print_key("fork_locale_switched")


def main():
    parser = argparse.ArgumentParser(bcsfe.__app_name__)

    parser.add_argument(
        "--version", "-v", action="store_true", help="display the version and exit"
    )
    parser.add_argument(
        "--input-path", "-i", type=str, help="input path to save file to edit"
    )
    parser.add_argument(
        "--game-data-dir", "-g", type=str, help="path to store the game data to"
    )
    parser.add_argument(
        "--data-dir", "-d", type=str, help="path to store editor data to"
    )
    parser.add_argument(
        "--transfer-backup-path",
        type=str,
        help="path to save the backup SAVE_DATA after transfering to",
    )
    parser.add_argument(
        "--config-path",
        "-c",
        type=str,
        default=None,
        help=f"path to the config file. If unspecified defaults to {core.Config.get_config_path()}",
    )
    parser.add_argument(
        "--log-path",
        "-l",
        type=str,
        default=None,
        help=f"path to the log file. If unspecified defaults to {core.Logger.get_log_path()}",
    )
    parser.add_argument(
        "--force-migrate",
        action="store_true",
        help=f"copy all data from bcsfe/src/files to {core.Path.get_data_folder()}",
    )

    args = parser.parse_args()
    if args.version:
        print(bcsfe.get_full_version())
        exit()

    if args.config_path is not None:
        core.set_config_path(core.Path(args.config_path))

    if args.log_path is not None:
        core.set_log_path(core.Path(args.log_path))

    if args.transfer_backup_path is not None:
        core.set_transfer_backup_path(core.Path(args.transfer_backup_path))

    if args.game_data_dir is not None:
        core.set_game_data_path(core.Path(args.game_data_dir))

    if args.data_dir is not None:
        core.set_data_dir_path(core.Path(args.data_dir))

    previous_version = migrate(args.force_migrate)

    core.core_data.init_data()
    apply_fork_defaults(previous_version)

    try:
        cli.main.Main().main(args.input_path)
    except KeyboardInterrupt:
        cli.main.Main.leave()
    except Exception as e:
        tb = traceback.format_exc()
        cli.color.color_print_key(
            "error", error=e, version=bcsfe.get_full_version(), traceback=tb
        )
        try:
            cli.main.Main.exit_editor()
        except Exception:
            pass
        except KeyboardInterrupt:
            pass


# guarded so that importing this module (bcsfe.run, tests) does not start the editor
if __name__ == "__main__":
    main()
