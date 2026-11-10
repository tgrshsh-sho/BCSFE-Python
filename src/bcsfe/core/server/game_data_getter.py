from __future__ import annotations
from io import BytesIO
import threading
from typing import Any, Callable

from bcsfe.cli import color, dialog_creator

import tarfile
import json

from bcsfe import core
from bcsfe.core.server import game_data_builder

# Versions already offered for building from the official app in this run (accepted,
# declined or failed). Getters are recreated after every feature, so this is module
# state; the lock keeps getters created at the same time from asking twice.
_app_build_offered: set[str] = set()
_app_build_lock = threading.RLock()


class GameDataGetter:
    @staticmethod
    def repo_url() -> str:
        return core.core_data.config.get_game_data_repo()

    def __init__(
        self, cc: core.CountryCode, gv: core.GameVersion, do_print: bool = True
    ):
        self.print = do_print
        self.lang = core.core_data.config.get_str(core.ConfigKey.LOCALE)
        self.cc = cc.get_cc_lang()
        self.real_cc = cc
        self.gv = gv
        self.cc = self.cc if not self.cc.is_lang() else self.real_cc
        self.version, exact_match = self.find_gv(self.cc, gv)

        self.all_versions = None
        self.url = None
        self.filepath = None

        if exact_match:
            return

        self.metadata = GameDataGetter.get_metadata()
        if self.metadata is not None:
            self.all_versions = GameDataGetter.get_versions(self.metadata)
            self.url = self.metadata.get("base_url")

        if self.build_missing_version():
            return

        if self.all_versions is None:
            return
        self.version, self.filepath = self.get_version(self.all_versions, self.cc)

    def build_missing_version(self) -> bool:
        """Offer to create the save's exact game version from the official app.

        Called when no local directory has that version. Only for regions the builder
        supports (jp), only when BCData does not offer the exact version (or could not
        be reached), and at most once per version per run. Returns True when the data
        was created; this getter then uses it instead of the fallback version.
        """
        cc_code = self.cc.get_code()
        version = self.gv.to_string()
        if not self.print or not game_data_builder.is_supported(cc_code):
            return False
        if self.all_versions is not None and version in self.all_versions.get(
            cc_code, {}
        ):
            return False

        with _app_build_lock:
            key = f"{cc_code}/{version}"
            if key in _app_build_offered:
                return False
            _app_build_offered.add(key)
            if not dialog_creator.yes_no_key("gdb_build_offer", version=version):
                color.color_print_key("gdb_build_declined")
                return False
            if not GameDataGetter.build_from_app(self.cc, version):
                return False

        self.version = version
        self.url = None
        self.filepath = None
        return True

    @staticmethod
    def can_build_from_app(cc: core.CountryCode) -> bool:
        return game_data_builder.is_supported(cc.get_code())

    @staticmethod
    def build_from_app(
        cc: core.CountryCode,
        version: str,
        packages: list[game_data_builder.PackageInfo] | None = None,
    ) -> bool:
        """Create game data for ``version`` from the official app, showing progress.

        On any failure prints a one-line reason and returns False.
        """
        color.color_print_key("gdb_build_start", version=version)
        try:
            path = game_data_builder.build_version(
                version,
                GameDataGetter.get_game_data_dir().to_str(),
                cc.get_code(),
                status=GameDataGetter.print_build_status,
                packages=packages,
            )
        except game_data_builder.BuildError as e:
            GameDataGetter.print_build_failure(e.key, **e.kwargs)
            return False
        except Exception as e:  # an optional step must never take the editor down
            core.core_data.logger.log_exception(e, "building game data from the app")
            GameDataGetter.print_build_failure(
                "gdb_reason_unexpected", error=type(e).__name__
            )
            return False
        color.color_print_key("gdb_build_done", version=version, path=path)
        return True

    @staticmethod
    def build_from_app_menu(cc: core.CountryCode) -> core.GameVersion | None:
        """Action of "Manage game data": create a chosen version (default: newest)."""
        if not GameDataGetter.can_build_from_app(cc):
            GameDataGetter.print_build_failure(
                "gdb_reason_unsupported", cc=cc.get_code()
            )
            return None

        packages: list[game_data_builder.PackageInfo] | None = None
        override = game_data_builder.get_package_override()
        try:
            if override is None:
                color.color_print_key("gdb_status_index")
                packages = game_data_builder.fetch_index()
                versions = game_data_builder.available_versions(packages)
                color.color_print_key(
                    "gdb_available_versions", versions=", ".join(versions[:10])
                )
                default = versions[0] if versions else None
            else:
                default = game_data_builder.package_version(override)
        except game_data_builder.BuildError as e:
            GameDataGetter.print_build_failure(e.key, **e.kwargs)
            return None

        if default is None:
            answer = dialog_creator.str_input_key("gdb_version_q_no_default")
        else:
            answer = dialog_creator.str_input_key("gdb_version_q", default=default)
        if answer is None:
            return None
        version = answer.strip() or default
        if not version:
            return None
        if not game_data_builder.is_valid_version(version):
            GameDataGetter.print_build_failure("gdb_reason_bad_version", version=version)
            return None

        downloaded = (
            GameDataGetter.get_game_data_dir()
            .add(cc.get_code())
            .add(version)
            .add("downloaded")
        )
        if downloaded.exists():
            color.color_print_key("gdb_already_present", version=version)
        elif not GameDataGetter.build_from_app(cc, version, packages):
            return None
        return core.GameVersion.from_string(version)

    @staticmethod
    def print_build_status(key: str, /, **kwargs: Any) -> None:
        color.color_print_key(key, **kwargs)

    @staticmethod
    def print_build_failure(reason_key: str, **kwargs: Any) -> None:
        reason = core.localize(reason_key, **kwargs)
        color.color_print_key("gdb_build_failed", escape=False, reason=reason)

    def find_gv(
        self, cc: core.CountryCode, gv: core.GameVersion
    ) -> tuple[str | None, bool]:
        versions = GameDataGetter.get_all_downloaded_versions().get(cc)
        if versions is None:
            return None, False

        versions.sort()

        for version in versions:
            if version >= gv:
                return version.to_string(), version == gv
        return None, False

    def does_save_version_match(self, save_file: core.SaveFile) -> bool:
        if self.version is None:
            return False

        return save_file.game_version == self.version

    def get_version(
        self, versions: dict[str, dict[str, str]], cc: core.CountryCode
    ) -> tuple[str | None, str | None]:
        cc_versions = versions.get(cc.get_code())
        if cc_versions is None:
            return None, None
        if not cc_versions:
            return None, None
        gv_string = self.gv.to_string()
        if gv_string not in cc_versions:
            cc_version_keys = list(cc_versions.keys())
            cc_version_keys.sort()
            for version in cc_version_keys:
                if (
                    core.GameVersion.from_string(version).game_version
                    >= self.gv.game_version
                ):
                    return version, cc_versions[version]
            return cc_version_keys[-1], cc_versions[cc_version_keys[-1]]
        return gv_string, cc_versions[gv_string]

    @staticmethod
    def possible_versions(cc: core.CountryCode) -> list[str] | None:
        metadata = GameDataGetter.get_metadata()

        if metadata is None:
            return None

        return [
            gv for gv in GameDataGetter.get_versions(metadata).get(cc.get_code(), {})
        ]

    @staticmethod
    def get_metadata(show_alt: bool = True) -> dict[str, Any] | None:
        response = core.RequestHandler(GameDataGetter.repo_url()).get()
        if response is None:
            if (
                GameDataGetter.repo_url()
                == core.core_data.config.get_default(core.ConfigKey.GAME_DATA_REPO)
                and show_alt
            ):
                alt = "https://gitlab.com/fieryhenry/bcdata/-/raw/main/metadata.json"
                res = dialog_creator.yes_no_key("use_alternative_repo", repo=alt)
                if res:
                    core.core_data.config.set(core.ConfigKey.GAME_DATA_REPO, alt)
                    return GameDataGetter.get_metadata(show_alt=False)

            return None
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            print(e, f"Data:\n{response.text}")
            return None
        return data

    @staticmethod
    def get_versions(metdata: dict[str, Any]) -> dict[str, dict[str, str]]:
        return metdata.get("versions", {})

    def get_packname(self, packname: str) -> str:
        if packname != "resLocal":
            return packname
        if self.cc != core.CountryCodeType.EN:
            return packname
        langs = core.CountryCode.get_langs()
        if self.lang in langs:
            return f"{packname}_{self.lang}"
        return packname

    @staticmethod
    def get_game_data_dir() -> core.Path:
        path = core.get_game_data_path()
        if path is None:
            return core.Path.get_data_folder().add("game_data")
        return path

    def get_file_path(self, pack_name: str, file_name: str) -> core.Path | None:
        pack_name = self.get_packname(pack_name)
        path = self.get_version_path()
        if path is None:
            return None
        return path.add(pack_name).generate_dirs().add(file_name)

    def download_version_data(self):
        if self.url is None or self.filepath is None or self.version is None:
            return None
        url = self.url + self.filepath

        if self.print:
            color.color_print_key("downloading_compressed_data", url=url)

        downloaded_data = core.RequestHandler(url).get()
        if downloaded_data is None:
            if self.print:
                color.color_print_key("no_internet")
            return None

        archive = tarfile.open(
            name=None, mode="r:xz", fileobj=BytesIO(downloaded_data.content)
        )

        outdir = (
            GameDataGetter.get_game_data_dir().add(self.cc.get_code()).add(self.version)
        ).generate_dirs()

        archive.extractall(outdir.path)

        outdir.add("downloaded").write(core.Data())

        return True

    def get_version_path(self) -> core.Path | None:
        if self.version is None:
            return None
        return (
            GameDataGetter.get_game_data_dir().add(self.cc.get_code()).add(self.version)
        ).generate_dirs()

    def has_downloaded(self) -> bool:
        path = self.get_version_path()
        if path is None:
            return False
        return path.add("downloaded").exists()

    def get_file(self, pack_name: str, file_name: str) -> core.Data | bool:
        path = self.get_file_path(pack_name, file_name)
        if path is None:
            return False

        if path.exists():
            return path.read()
        else:
            res = self.try_download()
            if res is not None:
                return res
            path = self.get_file_path(pack_name, file_name)
            if path is None:
                return False

            if path.exists():
                return path.read()
            return self.has_downloaded()

    def try_download(self) -> bool | None:
        if self.has_downloaded():
            return True
        if self.download_version_data() is None:
            return False

    def save_file(self, pack_name: str, file_name: str) -> core.Data | bool:
        pack_name = self.get_packname(pack_name)
        data = self.get_file(pack_name, file_name)
        if isinstance(data, bool):
            return data

        path = self.get_file_path(pack_name, file_name)
        if path is None:
            return False
        data.to_file(path)
        return data

    def save_file_data(
        self, pack_name: str, file_name: str, data: core.Data
    ) -> core.Data | None:
        pack_name = self.get_packname(pack_name)

        path = self.get_file_path(pack_name, file_name)
        if path is None:
            return None
        data.to_file(path)
        return data

    def is_downloaded(self, pack_name: str, file_name: str) -> bool:
        pack_name = self.get_packname(pack_name)
        path = self.get_file_path(pack_name, file_name)
        if path is None:
            return False
        return path.exists()

    def download_from_path(
        self, path: str, retries: int = 2, display_text: bool = True
    ) -> core.Data | None:
        pack_name, file_name = path.split("/")
        pack_name = self.get_packname(pack_name)
        return self.download(pack_name, file_name, retries, display_text)

    def download(
        self,
        pack_name: str,
        file_name: str,
        retries: int = 2,
        display_text: bool = True,
    ) -> core.Data | None:
        retries -= 1
        pack_name = self.get_packname(pack_name)

        if self.is_downloaded(pack_name, file_name):
            path = self.get_file_path(pack_name, file_name)
            if path is None:
                return None
            try:
                return path.read()
            except FileNotFoundError:
                return None

        if retries == 0:
            return None

        version = self.version

        if version is None:
            if display_text:
                self.print_no_file(pack_name, file_name)
            return None

        if display_text and not self.has_downloaded():
            color.color_print_key(
                "downloading",
                file_name=file_name,
                pack_name=pack_name,
                country_code=self.cc.get_code(),
                version=version,
            )
        data = self.save_file(pack_name, file_name)
        if isinstance(data, bool):
            if not data and display_text:
                self.print_no_file(pack_name, file_name)
            return None

        data = self.download(pack_name, file_name, retries, display_text)
        if data is None:
            if display_text:
                self.print_no_file(pack_name, file_name)
            return None
        return data

    def download_all(
        self,
        pack_name: str,
        file_names: list[str],
        display_text: bool = True,
    ) -> list[tuple[str, core.Data] | None]:
        pack_name = self.get_packname(pack_name)

        callables: list[Callable[..., Any]] = []
        args: list[tuple[str, str, int, bool]] = []
        for file_name in file_names:
            callables.append(self.download)
            args.append((pack_name, file_name, 2, display_text))
        core.thread_run_many(callables, args)
        data_list: list[tuple[str, core.Data] | None] = []
        for file_name in file_names:
            path = self.get_file_path(pack_name, file_name)
            if path is None:
                data_list.append(None)
            elif not path.exists():
                data_list.append(None)
            else:
                data_list.append((file_name, path.read()))
        return data_list

    @staticmethod
    def get_downloaded_versions_region(cc: core.CountryCode) -> list[core.GameVersion]:
        versions: list[core.GameVersion] = []
        cc_str = cc.get_code()
        dir = GameDataGetter.get_game_data_dir().add(cc_str)
        if not dir.exists():
            return versions
        for version in GameDataGetter.get_game_data_dir().add(cc_str).get_dirs():
            if not version.exists():
                continue
            if not version.add("downloaded").exists():
                continue
            versions.append(core.GameVersion.from_string(version.basename()))

        return versions

    @staticmethod
    def get_all_downloaded_versions() -> dict[core.CountryCode, list[core.GameVersion]]:
        versions: dict[core.CountryCode, list[core.GameVersion]] = {}
        for cc in core.CountryCode.get_all():
            versions[cc] = GameDataGetter.get_downloaded_versions_region(cc)

        return versions

    @staticmethod
    def delete(cc: core.CountryCode, gv: core.GameVersion):
        path = GameDataGetter.get_game_data_dir().add(cc.get_code()).add(gv.to_string())
        path.remove()

    @staticmethod
    def delete_region(cc: core.CountryCode):
        path = GameDataGetter.get_game_data_dir().add(cc.get_code())
        path.remove()

    @staticmethod
    def delete_old_versions(to_keep: int) -> None:
        versions = GameDataGetter.get_all_downloaded_versions()
        for cc, cc_versions in versions.items():
            cc_versions.sort(reverse=True)
            for version in cc_versions[to_keep:]:
                GameDataGetter.delete(cc, version)

    def print_no_file(self, packname: str, file_name: str) -> None:
        if self.version is None:
            color.color_print_key("failed_to_get_game_versions")
        else:
            color.color_print_key(
                "failed_to_download_game_data",
                file_name=file_name,
                pack_name=packname,
                country_code=self.cc.get_code(),
                version=self.version,
                url=self.url,
            )
