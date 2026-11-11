from __future__ import annotations
import json
import os
import tempfile
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any, cast
from bcsfe.cli import color, tui
from bcsfe.cli.tui import strings as tui_strings
from bcsfe import core

# ServerHandler.from_codes puts the transfer code into the URL path
# .../v2/transfers/{transfer_code}/reception: with one of these characters the
# request goes to another path (or loses its end as query / fragment).
URL_BREAKING_CHARS = "/?#"

# <data dir>/fork/state.json: small state the fork keeps between runs
STATE_DOWNLOAD_REGION = "download_region"
DEFAULT_DOWNLOAD_REGION = "jp"


def normalize_code(text: str) -> str:
    """A code as typed: NFKC (full-width ａｂｃ１２３ from an IME becomes
    abc123) without any whitespace."""
    return "".join(unicodedata.normalize("NFKC", text).split())


def transfer_code_error(code: str) -> str | None:
    """Why ``code`` (normalized) cannot be a transfer code, None if it can.
    Only what the request itself rules out: empty, or a character that changes
    the URL (see URL_BREAKING_CHARS)."""
    if not code:
        return tui_strings.tr("tui_error_empty")
    bad = [char for char in URL_BREAKING_CHARS if char in code]
    if bad:
        return core.localize("io_transfer_code_bad_chars", chars=" ".join(bad))
    return None


def confirmation_code_error(code: str) -> str | None:
    """The confirmation code is sent as a JSON string; only empty is ruled out."""
    if not code:
        return tui_strings.tr("tui_error_empty")
    return None


def state_path() -> core.Path:
    return core.Path.get_data_folder().add("fork", "state.json")


def load_state() -> dict[str, Any]:
    """The fork's state; empty when the file is missing, unreadable or corrupt."""
    try:
        with open(state_path().to_str(), encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError, RecursionError):
        return {}
    if not isinstance(data, dict):
        return {}
    return cast(dict[str, Any], data)


def save_state(values: Mapping[str, Any]) -> None:
    """Update keys of the fork's state (others are kept). The file is replaced
    atomically, so it is never left half written."""
    path = state_path()
    folder = path.parent().generate_dirs()
    state = load_state()
    state.update(values)
    fd, temp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=folder.to_str())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path.to_str())
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def last_download_region() -> str:
    """Region of the last successful download, else jp."""
    code = load_state().get(STATE_DOWNLOAD_REGION)
    if isinstance(code, str) and code in core.CountryCode.get_all_str():
        return code
    return DEFAULT_DOWNLOAD_REGION


def remember_download_region(cc: core.CountryCode) -> None:
    try:
        save_state({STATE_DOWNLOAD_REGION: cc.get_code()})
    except OSError as e:  # only a convenience: never fail the download for it
        core.core_data.logger.log_exception(e, "could not save fork/state.json")


class ServerCLI:
    def __init__(self):
        pass

    @staticmethod
    def input_code(dialog_key: str, error: Callable[[str], str | None]) -> str | None:
        """Ask for a code; the answer is normalized (see normalize_code)."""
        value = tui.text(
            core.localize(dialog_key),
            validate=lambda text: error(normalize_code(text)),
        )
        if value is None:
            return None
        return normalize_code(value)

    def download_save(
        self,
    ) -> tuple[core.Path, core.CountryCode] | None:
        transfer_code = ServerCLI.input_code("enter_transfer_code", transfer_code_error)
        if transfer_code is None:
            return None
        confirmation_code = ServerCLI.input_code(
            "enter_confirmation_code", confirmation_code_error
        )
        if confirmation_code is None:
            return None
        cc = core.CountryCode.select(default=last_download_region())
        if cc is None:
            return None
        gv = core.GameVersion(120200)  # not important

        color.color_print_key(
            "downloading_save_file",
            transfer_code=transfer_code,
            confirmation_code=confirmation_code,
            country_code=cc,
        )

        server_handler, result = core.ServerHandler.from_codes(
            transfer_code,
            confirmation_code,
            cc,
            gv,
        )
        if server_handler is None and result is not None:
            color.color_print_key("invalid_codes_error")
            if cc == "jp":
                color.color_print_key("jp_tw_mixup")
            # if dialog_creator.yes_no_key("display_response_debug_info_q"):
            #     if result.response is not None:
            #         color.color_print_key(
            #             "response_text_display",
            #             url=result.url,
            #             request_headers=result.headers,
            #             request_body=result.data,
            #             response_headers=result.response.headers,
            #             response_body=result.response.text,
            #         )
            return
        if server_handler is None:
            return

        save_file = server_handler.save_file
        # the standard place, also offered as "open the last save" next time;
        # no save-as dialog (it popped up a tkinter window on every download)
        path = core.SaveFile.get_saves_path().add("SAVE_DATA")

        try:
            save_file.to_file(path)
        except OSError as e:
            print(
                f"failed to write save file to: {path} due to: {e}. Skipping writing the save file to disk"
            )
            input("press enter to continue anyway")

        remember_download_region(cc)
        color.color_print_key("save_downloaded", path=path.to_str())

        return path, cc
