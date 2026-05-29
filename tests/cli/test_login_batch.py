"""Tests for the safe email-only `nlm login batch` command."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from notebooklm_tools.cli.main import _load_batch_login_emails, _next_numeric_profile_index, app
from notebooklm_tools.core.exceptions import BrowserClosedError


class FakeAuthManager:
    """Minimal AuthManager test double for login batch tests."""

    existing_profiles: set[str] = set()
    profile_emails: dict[str, str] = {}
    saved_profiles: list[tuple[str, dict]] = []

    def __init__(self, profile_name: str) -> None:
        self.profile_name = profile_name
        self.profile_dir = Path("/fake/profiles") / profile_name

    def profile_exists(self) -> bool:
        return self.profile_name in self.existing_profiles

    def save_profile(self, **kwargs):
        self.saved_profiles.append((self.profile_name, kwargs))
        self.existing_profiles.add(self.profile_name)
        if kwargs.get("email"):
            self.profile_emails[self.profile_name] = kwargs["email"]
        return None

    def load_profile(self):
        if self.profile_name not in self.existing_profiles:
            raise RuntimeError(f"Profile {self.profile_name} not found")
        return SimpleNamespace(email=self.profile_emails.get(self.profile_name, ""))

    @staticmethod
    def list_profiles() -> list[str]:
        return sorted(FakeAuthManager.existing_profiles)


@pytest.fixture(autouse=True)
def reset_fake_auth_manager():
    FakeAuthManager.existing_profiles = set()
    FakeAuthManager.profile_emails = {}
    FakeAuthManager.saved_profiles = []


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_load_batch_login_emails_accepts_email_only_file(tmp_path):
    accounts_file = tmp_path / "accounts.txt"
    accounts_file.write_text("\ufeff# comment\n\nfirst@gmail.com\nsecond@example.com\n", encoding="utf-8")

    assert _load_batch_login_emails(accounts_file) == ["first@gmail.com", "second@example.com"]


def test_load_batch_login_emails_rejects_password_columns(tmp_path):
    accounts_file = tmp_path / "accounts.txt"
    accounts_file.write_text("first@gmail.com:shared-password\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Password columns are not supported"):
        _load_batch_login_emails(accounts_file)


def test_next_numeric_profile_index_ignores_named_profiles():
    assert _next_numeric_profile_index(["default", "work", "1", "3"]) == 4


def test_login_all_profiles_refreshes_saved_profiles(runner):
    FakeAuthManager.existing_profiles = {"10", "2", "work"}
    FakeAuthManager.profile_emails = {
        "2": "two@gmail.com",
        "10": "ten@gmail.com",
        "work": "work@gmail.com",
    }

    extract_results = [
        {"cookies": {"SID": "two"}, "email": "two@gmail.com"},
        {"cookies": {"SID": "ten"}, "email": "ten@gmail.com"},
        {"cookies": {"SID": "work"}, "email": "work@gmail.com"},
    ]

    with (
        patch("notebooklm_tools.core.auth.AuthManager", FakeAuthManager),
        patch("notebooklm_tools.utils.cdp.extract_cookies_via_cdp", side_effect=extract_results) as extract,
        patch("notebooklm_tools.cli.main._close_launched_auth_browser") as close_browser,
    ):
        result = runner.invoke(app, ["login", "--all-profiles", "--close-delay", "0"])

    assert result.exit_code == 0
    assert [call.kwargs["profile_name"] for call in extract.call_args_list] == ["2", "10", "work"]
    assert [call.kwargs["login_hint"] for call in extract.call_args_list] == [
        "two@gmail.com",
        "ten@gmail.com",
        "work@gmail.com",
    ]
    assert [call.kwargs["clear_profile"] for call in extract.call_args_list] == [False, False, False]
    assert [profile for profile, _ in FakeAuthManager.saved_profiles] == ["2", "10", "work"]
    assert close_browser.call_count == 3


def test_login_all_profiles_can_start_from_numeric_profile(runner):
    FakeAuthManager.existing_profiles = {"1", "2", "5", "work"}
    FakeAuthManager.profile_emails = {
        "1": "one@gmail.com",
        "2": "two@gmail.com",
        "5": "five@gmail.com",
        "work": "work@gmail.com",
    }

    extract_results = [
        {"cookies": {"SID": "two"}, "email": "two@gmail.com"},
        {"cookies": {"SID": "five"}, "email": "five@gmail.com"},
    ]

    with (
        patch("notebooklm_tools.core.auth.AuthManager", FakeAuthManager),
        patch("notebooklm_tools.utils.cdp.extract_cookies_via_cdp", side_effect=extract_results) as extract,
        patch("notebooklm_tools.cli.main._close_launched_auth_browser"),
    ):
        result = runner.invoke(app, ["login", "--all", "--start-index", "2", "--close-delay", "0"])

    assert result.exit_code == 0
    assert [call.kwargs["profile_name"] for call in extract.call_args_list] == ["2", "5"]
    assert [profile for profile, _ in FakeAuthManager.saved_profiles] == ["2", "5"]
    assert "Starting from numeric profile 2" in result.output


def test_login_all_profiles_rejects_empty_profile_list(runner):
    with patch("notebooklm_tools.core.auth.AuthManager", FakeAuthManager):
        result = runner.invoke(app, ["login", "--all-profiles"])

    assert result.exit_code == 1
    assert "No saved profiles found" in result.output


def test_login_start_index_requires_all_profiles(runner):
    result = runner.invoke(app, ["login", "--start-index", "2"])

    assert result.exit_code == 1
    assert "--start-index only works with --all-profiles" in result.output


def test_login_all_profiles_skips_closed_browser_and_continues(runner):
    FakeAuthManager.existing_profiles = {"1", "2"}
    FakeAuthManager.profile_emails = {
        "1": "one@gmail.com",
        "2": "two@gmail.com",
    }

    extract_results = [
        BrowserClosedError(),
        {"cookies": {"SID": "two"}, "email": "two@gmail.com"},
    ]

    with (
        patch("notebooklm_tools.core.auth.AuthManager", FakeAuthManager),
        patch("notebooklm_tools.utils.cdp.extract_cookies_via_cdp", side_effect=extract_results) as extract,
        patch("notebooklm_tools.cli.main._close_launched_auth_browser") as close_browser,
        patch("notebooklm_tools.utils.cdp.terminate_chrome") as terminate,
    ):
        result = runner.invoke(app, ["login", "--all", "--close-delay", "0"])

    assert result.exit_code == 0
    assert [call.kwargs["profile_name"] for call in extract.call_args_list] == ["1", "2"]
    assert [profile for profile, _ in FakeAuthManager.saved_profiles] == ["2"]
    assert "Skipped 1 profile(s): 1" in result.output
    terminate.assert_called_once()
    close_browser.assert_called_once()


def test_login_batch_creates_numeric_profiles(runner, tmp_path):
    accounts_file = tmp_path / "accounts.txt"
    accounts_file.write_text("first@gmail.com\nsecond@gmail.com\n", encoding="utf-8")

    extract_results = [
        {
            "cookies": {"SID": "one"},
            "csrf_token": "csrf-one",
            "session_id": "sid-one",
            "email": "first@gmail.com",
            "build_label": "build-one",
        },
        {
            "cookies": {"SID": "two"},
            "csrf_token": "csrf-two",
            "session_id": "sid-two",
            "email": "second@gmail.com",
            "build_label": "build-two",
        },
    ]

    with (
        patch("notebooklm_tools.core.auth.AuthManager", FakeAuthManager),
        patch("notebooklm_tools.utils.cdp.extract_cookies_via_cdp", side_effect=extract_results) as extract,
        patch("notebooklm_tools.cli.main._close_launched_auth_browser") as close_browser,
    ):
        result = runner.invoke(app, ["login", "batch", str(accounts_file), "--close-delay", "0"])

    assert result.exit_code == 0
    assert [profile for profile, _ in FakeAuthManager.saved_profiles] == ["1", "2"]
    assert FakeAuthManager.saved_profiles[0][1]["email"] == "first@gmail.com"
    assert FakeAuthManager.saved_profiles[1][1]["email"] == "second@gmail.com"
    assert extract.call_args_list[0].kwargs["profile_name"] == "1"
    assert extract.call_args_list[0].kwargs["login_hint"] == "first@gmail.com"
    assert extract.call_args_list[0].kwargs["clear_profile"] is True
    assert extract.call_args_list[1].kwargs["profile_name"] == "2"
    assert extract.call_args_list[1].kwargs["login_hint"] == "second@gmail.com"
    assert close_browser.call_count == 2


def test_login_batch_starts_after_highest_existing_numeric_profile(runner, tmp_path):
    accounts_file = tmp_path / "accounts.txt"
    accounts_file.write_text("third@gmail.com\n", encoding="utf-8")
    FakeAuthManager.existing_profiles = {"1", "2", "work"}

    with (
        patch("notebooklm_tools.core.auth.AuthManager", FakeAuthManager),
        patch(
            "notebooklm_tools.utils.cdp.extract_cookies_via_cdp",
            return_value={"cookies": {"SID": "three"}, "email": "third@gmail.com"},
        ) as extract,
        patch("notebooklm_tools.cli.main._close_launched_auth_browser"),
    ):
        result = runner.invoke(app, ["login", "batch", str(accounts_file), "--close-delay", "0"])

    assert result.exit_code == 0
    assert [profile for profile, _ in FakeAuthManager.saved_profiles] == ["3"]
    assert extract.call_args.kwargs["profile_name"] == "3"


def test_login_batch_rejects_existing_profile_without_force(runner, tmp_path):
    accounts_file = tmp_path / "accounts.txt"
    accounts_file.write_text("first@gmail.com\n", encoding="utf-8")
    FakeAuthManager.existing_profiles = {"1"}

    with (
        patch("notebooklm_tools.core.auth.AuthManager", FakeAuthManager),
        patch("notebooklm_tools.utils.cdp.extract_cookies_via_cdp") as extract,
    ):
        result = runner.invoke(app, ["login", "batch", str(accounts_file), "--start-index", "1"])

    assert result.exit_code == 1
    assert "already exists" in result.output
    extract.assert_not_called()
    assert FakeAuthManager.saved_profiles == []


def test_login_batch_rejects_mismatched_logged_in_account(runner, tmp_path):
    accounts_file = tmp_path / "accounts.txt"
    accounts_file.write_text("first@gmail.com\n", encoding="utf-8")

    with (
        patch("notebooklm_tools.core.auth.AuthManager", FakeAuthManager),
        patch(
            "notebooklm_tools.utils.cdp.extract_cookies_via_cdp",
            return_value={"cookies": {"SID": "one"}, "email": "other@gmail.com"},
        ),
        patch("notebooklm_tools.utils.cdp.terminate_chrome") as terminate,
    ):
        result = runner.invoke(app, ["login", "batch", str(accounts_file)])

    assert result.exit_code == 1
    assert "does not match" in result.output
    terminate.assert_called_once()
    assert FakeAuthManager.saved_profiles == []
