"""The real editor on the fixture save, interactive and classic.

Interactive runs open features from the home menu (a filterable list: type a
name, wait for it to be highlighted, Enter); classic runs type the feature
name into the numbered menu's ``input()``.  The editor runs in a sandbox (see
conftest.py) with an unroutable proxy, so it never touches the user's data or
the network.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

import yaml

from tests.conftest import REPO, Sandbox

if TYPE_CHECKING:
    from tests.tui_harness import Terminal

StartTerminal = Callable[..., "Terminal"]
STARTUP = 60.0  # first start copies the editor files into the sandbox


HOME = "[Frequently used]"  # heading shown while the home menu waits (locale en)
CLASSIC_MENU = "Input:"


def start_editor(
    terminal: StartTerminal, sandbox: Sandbox, ready: str = HOME
) -> Terminal:
    term = terminal(sandbox.editor_command(sandbox.root / "SAVE_DATA"), env=sandbox.env)
    term.wait_for(ready, STARTUP)
    term.wait_idle(0.3)
    return term


def open_home_feature(term: Terminal, query: str, label: str, wait: str) -> None:
    """Filter the home menu by ``query``, check that ``label`` is the
    highlighted row and open it."""
    term.send_text(query)
    term.wait_for(f"❯ {label}", 30)
    term.send_key("enter")
    term.wait_for(wait, 30)


def open_feature(
    term: Terminal, name: str, wait: str, label: str | None = None
) -> None:
    """Type ``name`` into the classic feature menu; when the search lists
    several features, pick the one called ``label`` by its number."""
    term.send_text(name + "\r")
    if label is not None:
        term.wait_for(f". {label}", 30)
        term.wait_idle(0.3)
        number = next(
            m.group(1)
            for line in reversed(term.screen_lines())
            if (m := re.match(r"\s*(\d+)\. (.*)$", line)) and m.group(2) == label
        )
        term.send_text(number + "\r")
    term.wait_for(wait, 30)


def test_catfruit_multi_select_and_xp_integer(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start_editor(terminal, editor_sandbox)

    # Catfruit / Behemoth Stones: check two rows through filtering
    open_home_feature(
        term, "catfruit", "Catfruit / Behemoth Stones", "Ctrl-A check all shown"
    )
    screen = term.screen_text()
    assert "Select options for Catfruit / Behemoth Stones:" in screen
    assert "0 checked  29 / 29" in screen
    assert "❯ [ ] 紫マタタビの種 : 169" in screen
    term.send_text("紫")
    term.wait_for("4 / 29")
    term.send_key("tab")
    term.send_key("c-u")
    term.send_text("虹")
    term.wait_for("3 / 29")
    term.send_key("tab")
    term.wait_for("2 checked")
    assert "[x] 虹マタタビ : 165" in term.screen_text()
    term.send_key("enter")

    # individually / all at once: a select; "all at once" is highlighted
    # when several items are checked
    term.wait_for("individually or all at once")
    term.wait_for("❯ All at once")
    term.send_text("once")
    term.wait_for("1 / 2")
    term.send_key("enter")

    # one value for both: integer widget
    term.wait_for("range 0 - 998")
    term.send_text("abc")
    term.send_key("enter")
    term.wait_for("Please enter a whole number")
    term.send_key("c-u")
    term.send_text("300")
    term.send_key("enter")
    term.wait_for("Successfully changed 虹マタタビ to 300")
    screen = term.screen_text()
    assert "Successfully changed 紫マタタビの種 to 300" in screen
    assert (
        "✔ Select options for Catfruit / Behemoth Stones: 紫マタタビの種 : 169 / 虹マタタビ : 165"
        in screen
    )
    assert (
        "✔ Enter a value for Catfruit / Behemoth Stones (max value: 998): 300" in screen
    )
    term.wait_for(HOME)
    term.wait_idle(0.3)

    # XP: the home lists the prefix match first
    open_home_feature(term, "xp", "XP", "Empty Enter keeps 20,870,087")
    assert "range 0 - 99,999,999  current 20,870,087" in term.screen_text()
    term.send_text("12345")
    term.send_key("enter")
    term.wait_for("Successfully changed XP to 12345")
    assert (
        "✔ Enter a value for XP (current value: 20870087) (max value: 99999999): 12345"
        in (term.screen_text())
    )


def test_escape_cancels_back_to_menu(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start_editor(terminal, editor_sandbox)
    open_home_feature(
        term, "catfruit", "Catfruit / Behemoth Stones", "Ctrl-A check all shown"
    )
    term.send_key("esc")
    term.wait_for("✘ Select options for Catfruit / Behemoth Stones: (cancelled)")
    term.wait_for(HOME)


def test_config_ui_mode_classic_restores_numbered_prompts(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    editor_sandbox.write_config(ui_mode="classic")
    term = start_editor(terminal, editor_sandbox, ready=CLASSIC_MENU)
    open_feature(term, "catfruit", "separated by spaces")
    screen = term.screen_text()
    assert " 30. All" in screen
    assert "Input numbers between 1 and 30 separated by spaces:" in screen
    assert "check all shown" not in screen


def test_ui_mode_can_be_changed_in_config_menu(
    terminal: StartTerminal, editor_sandbox: Sandbox
) -> None:
    term = start_editor(terminal, editor_sandbox)
    open_home_feature(term, "config", "Config", "Select a config option to edit:")
    term.send_text("prompt style")
    term.wait_for("1 / 22")
    term.send_key("enter")
    term.wait_for("Select the prompt style:")
    assert "BCSFE_UI=classic" in term.screen_text()
    term.send_text("classic")
    term.wait_for("1 / 2")
    term.send_key("enter")
    term.wait_for("Successfully updated config")
    config = yaml.safe_load(editor_sandbox.config_path.read_text(encoding="utf-8"))
    assert config["ui_mode"] == "classic"
    # the home hands over to the classic numbered menu
    term.wait_for(CLASSIC_MENU)
    term.wait_idle(0.3)
    open_feature(term, "catfruit", "separated by spaces")


def test_classic_path_with_piped_stdin(editor_sandbox: Sandbox) -> None:
    env = {**_inherited_env(), **editor_sandbox.env}
    keys = "\n".join(["catfruit", "1 11", "2", "300", ""])
    proc = subprocess.run(
        editor_sandbox.editor_command(editor_sandbox.root / "SAVE_DATA"),
        input=keys,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=REPO,
        timeout=120,
    )
    out = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", proc.stdout)  # colours stay on
    assert "Input numbers between 1 and 30 separated by spaces:" in out
    assert " 30. All" in out
    assert "individually" in out and " 2. All at once" in out
    assert "Successfully changed 紫マタタビの種 to 300" in out
    assert "Successfully changed 虹マタタビ to 300" in out
    assert "✔" not in out and "check all shown" not in out


def _inherited_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("BCSFE_")}
