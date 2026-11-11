"""Android detection: macOS has /System on a case-insensitive file system."""

from __future__ import annotations

import pytest

from bcsfe import core
from bcsfe.core.io import root_handler
from bcsfe.core.io.root_handler import RootHandler


@pytest.fixture
def plain_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(root_handler.sys, "platform", "darwin")
    monkeypatch.delenv("ANDROID_ROOT", raising=False)
    monkeypatch.delenv("ANDROID_DATA", raising=False)


def test_macos_is_not_android(plain_desktop: None) -> None:
    # On a Mac "/system" resolves to "/System"; only the Android binary counts.
    assert not core.Path.get_root().add("system").add("bin").add("app_process").exists()
    assert RootHandler().is_android() is False


def test_android_environment_variables(
    plain_desktop: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANDROID_ROOT", "/system")
    assert RootHandler().is_android() is False  # both are set on Android
    monkeypatch.setenv("ANDROID_DATA", "/data")
    assert RootHandler().is_android() is True


def test_android_platform(plain_desktop: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(root_handler.sys, "platform", "android")
    assert RootHandler().is_android() is True


def test_android_system_binary(
    plain_desktop: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def exists(path: core.Path) -> bool:
        seen.append(path.to_str())
        return path.to_str().endswith("app_process")

    monkeypatch.setattr(core.Path, "exists", exists)
    assert RootHandler().is_android() is True
    assert seen and seen[-1].replace("\\", "/").endswith("system/bin/app_process")
