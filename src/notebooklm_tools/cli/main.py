"""Main CLI application for NotebookLM Tools."""

import contextlib
import logging
import re
import time
from pathlib import Path

import typer

from notebooklm_tools import __version__
from notebooklm_tools.cli.commands.alias import app as alias_app
from notebooklm_tools.cli.commands.batch import app as batch_app
from notebooklm_tools.cli.commands.chat import app as chat_app
from notebooklm_tools.cli.commands.config import app as config_app
from notebooklm_tools.cli.commands.cross import app as cross_app
from notebooklm_tools.cli.commands.doctor import app as doctor_app
from notebooklm_tools.cli.commands.download import app as download_app
from notebooklm_tools.cli.commands.export import app as export_app
from notebooklm_tools.cli.commands.label import app as label_app
from notebooklm_tools.cli.commands.note import app as note_app
from notebooklm_tools.cli.commands.notebook import app as notebook_app
from notebooklm_tools.cli.commands.pipeline import app as pipeline_app
from notebooklm_tools.cli.commands.research import app as research_app
from notebooklm_tools.cli.commands.setup import app as setup_app
from notebooklm_tools.cli.commands.share import app as share_app
from notebooklm_tools.cli.commands.skill import app as skill_app
from notebooklm_tools.cli.commands.source import app as source_app
from notebooklm_tools.cli.commands.studio import (
    app as studio_app,
)
from notebooklm_tools.cli.commands.studio import (
    audio_app,
    data_table_app,
    flashcards_app,
    infographic_app,
    mindmap_app,
    quiz_app,
    report_app,
    slides_app,
    video_app,
)
from notebooklm_tools.cli.commands.tag import app as tag_app
from notebooklm_tools.cli.commands.verbs import (
    add_app,
    configure_app,
    content_app,
    create_app,
    delete_app,
    describe_app,
    get_app,
    install_app,
    list_app,
    query_app,
    rename_app,
    set_app,
    show_app,
    stale_app,
    status_app,
    sync_app,
    uninstall_app,
    update_app,
)
from notebooklm_tools.cli.utils import make_console

console = make_console()

AUTH_BROWSER_CLOSE_DELAY_SECONDS = 10
BATCH_EMAIL_RE = re.compile(r"^[^@\s:;,|]+@[^@\s:;,|]+\.[^@\s:;,|]+$")


def _close_launched_auth_browser(delay_seconds: int = AUTH_BROWSER_CLOSE_DELAY_SECONDS) -> None:
    """Keep the auth browser open briefly before closing it."""
    from notebooklm_tools.utils.cdp import get_browser_display_name, terminate_chrome

    browser_name = get_browser_display_name()
    if delay_seconds > 0:
        console.print(
            f"[dim]Keeping {browser_name} open for "
            f"{delay_seconds} seconds before closing...[/dim]"
        )
        time.sleep(delay_seconds)
    console.print(f"[dim]Closing {browser_name}...[/dim]")
    terminate_chrome()


def _load_batch_login_emails(accounts_file: Path) -> list[str]:
    """Load an email-only accounts file for safe batch login."""
    try:
        lines = accounts_file.read_text(encoding="utf-8-sig").splitlines()
    except OSError as e:
        raise ValueError(f"Could not read accounts file: {e}") from e

    emails: list[str] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if any(separator in line for separator in (":", ",", ";", "|")) or len(line.split()) != 1:
            raise ValueError(
                f"Line {line_number}: expected one email address only. "
                "Password columns are not supported."
            )

        if not BATCH_EMAIL_RE.fullmatch(line):
            raise ValueError(f"Line {line_number}: expected a valid email address")

        emails.append(line)

    if not emails:
        raise ValueError("No email addresses found in accounts file")

    return emails


def _next_numeric_profile_index(profile_names: list[str]) -> int:
    """Return the next profile number after the highest numeric profile name."""
    numeric_profiles = [int(name) for name in profile_names if name.isdigit()]
    return max(numeric_profiles, default=0) + 1


def _profile_sort_key(profile_name: str) -> tuple[int, int | str]:
    """Sort numeric profile names naturally before named profiles."""
    if profile_name.isdigit():
        return (0, int(profile_name))
    return (1, profile_name.casefold())


def _looks_like_browser_closed_error(error: Exception) -> bool:
    """Best-effort detection for browser/CDP connection closure errors."""
    message = str(error).lower()
    return any(
        fragment in message
        for fragment in (
            "browser window or tab was closed",
            "browser closed",
            "connection refused",
            "connection reset",
            "connection to remote host was lost",
            "websocket connection closed",
            "socket is already closed",
        )
    )


# Main application
app = typer.Typer(
    name="nlm",
    help="NotebookLM Tools - Unified CLI for Google NotebookLM",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# =============================================================================
# LOGIN app with nested profile commands
# =============================================================================

login_app = typer.Typer(
    help="Authentication and profile management",
    rich_markup_mode="rich",
)

# Profile management subcommands
profile_app = typer.Typer(
    help="Manage authentication profiles",
    rich_markup_mode="rich",
    no_args_is_help=True,
)


@login_app.callback(invoke_without_command=True)
def login_callback(
    ctx: typer.Context,
    manual: bool = typer.Option(
        False,
        "--manual",
        "-m",
        help="Manually provide cookies from a file",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Only check if current auth is valid",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Profile name (uses config default if not specified)",
    ),
    cookie_file: str | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Path to file containing cookies (for manual mode)",
    ),
    provider: str = typer.Option(
        "builtin",
        "--provider",
        help="Auth provider: builtin (default) or openclaw",
    ),
    cdp_url: str = typer.Option(
        "http://127.0.0.1:18800",
        "--cdp-url",
        help="CDP endpoint URL for external provider mode",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force overwrite even if profile has credentials for a different account",
    ),
    all_profiles: bool = typer.Option(
        False,
        "--all-profiles",
        "--all",
        help="Authenticate every saved profile in sequence",
    ),
    start_index: int | None = typer.Option(
        None,
        "--start-index",
        "--start",
        help="With --all-profiles, start from this numeric profile and skip lower numbers",
    ),
    stop_on_error: bool = typer.Option(
        False,
        "--stop-on-error",
        help="Stop --all-profiles at the first failed profile",
    ),
    close_delay: int = typer.Option(
        AUTH_BROWSER_CLOSE_DELAY_SECONDS,
        "--close-delay",
        help="Seconds to keep the browser open after each successful login",
    ),
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Delete the localized Chrome profile data before logging in, to switch Google accounts",
    ),
    wsl: bool = typer.Option(
        False,
        "--wsl",
        help="Launch Windows Chrome from WSL (fixes terminal corruption on WSL2)",
    ),
) -> None:
    """
    Authenticate with NotebookLM.

    Default: Uses Chrome DevTools Protocol to extract cookies automatically.
    Use --manual to import cookies from a file.
    Use --check to validate existing credentials.
    Use --provider openclaw --cdp-url <url> to read auth from an existing
    OpenClaw-managed browser CDP endpoint.
    Use --wsl on WSL2 to launch Windows Chrome and avoid terminal corruption.

    To switch active accounts, run `nlm login switch <profile>`.
    """
    from notebooklm_tools.core.auth import AuthManager
    from notebooklm_tools.core.exceptions import AccountMismatchError, NLMError
    from notebooklm_tools.utils.config import get_config

    # If a subcommand is invoked, don't run login logic
    if ctx.invoked_subcommand is not None:
        return

    if close_delay < 0:
        console.print("[red]Error:[/red] --close-delay cannot be negative")
        raise typer.Exit(1)
    if start_index is not None and start_index < 1:
        console.print("[red]Error:[/red] --start-index must be 1 or greater")
        raise typer.Exit(1)

    if all_profiles:
        if check or manual:
            console.print("[red]Error:[/red] --all-profiles cannot be combined with --check or --manual")
            raise typer.Exit(1)
        if provider.strip().lower() != "builtin":
            console.print("[red]Error:[/red] --all-profiles only supports the builtin auth provider")
            raise typer.Exit(1)
        if cdp_url != "http://127.0.0.1:18800":
            console.print("[red]Error:[/red] --all-profiles requires the default builtin CDP endpoint")
            console.print("[dim]Profile isolation is not available with an external CDP endpoint.[/dim]")
            raise typer.Exit(1)
        _login_all_saved_profiles(
            force=force,
            clear_profile=clear,
            close_delay=close_delay,
            stop_on_error=stop_on_error,
            start_index=start_index,
        )
        return

    if start_index is not None:
        console.print("[red]Error:[/red] --start-index only works with --all-profiles")
        console.print("[dim]For batch login, put --start-index after the batch subcommand.[/dim]")
        raise typer.Exit(1)

    # Use config default if no profile specified
    if profile is None:
        profile = get_config().auth.default_profile

    auth = AuthManager(profile)

    # Show which profile is being authenticated
    if not check and ctx.invoked_subcommand is None:
        try:
            existing = auth.load_profile()
            console.print(f"[dim]Authenticating profile: {profile} ({existing.email})[/dim]")
        except Exception:
            console.print(f"[dim]Authenticating profile: {profile}[/dim]")

    if check:
        # Check existing auth by making a real API call
        try:
            from notebooklm_tools.core.client import NotebookLMClient

            p = auth.load_profile()
            console.print(f"[dim]Checking credentials for profile: {p.name}...[/dim]")

            # Actually test the API using profile's credentials
            with NotebookLMClient(
                cookies=p.cookies,
                csrf_token=p.csrf_token or "",
                session_id=p.session_id or "",
                build_label=p.build_label or "",
            ) as client:
                notebooks = client.list_notebooks()

            # Success! Update last validated
            auth.save_profile(
                cookies=p.cookies,
                csrf_token=p.csrf_token,
                session_id=p.session_id,
                email=p.email,
                build_label=p.build_label,
            )

            console.print("[green]✓[/green] Authentication valid!")
            console.print(f"  Profile: {p.name}")
            console.print(f"  Notebooks found: {len(notebooks)}")
            if p.email:
                console.print(f"  Account: {p.email}")
        except NLMError as e:
            console.print(f"[red]✗[/red] Authentication failed: {e.message}")
            if e.hint:
                console.print(f"[dim]{e.hint}[/dim]")
            raise typer.Exit(2) from e
        return

    if manual:
        # Manual mode - read from file
        if not cookie_file:
            cookie_file = typer.prompt(
                "Enter path to file containing cookies",
                default="~/.nlm/cookies.txt",
            )
        try:
            auth.login_with_file(cookie_file)
            console.print("[green]✓[/green] Successfully authenticated!")
            console.print(f"  Profile saved: {profile}")
            console.print(f"  Credentials saved to: {auth.profile_dir}")
        except NLMError as e:
            console.print(f"[red]Error:[/red] {e.message}")
            if e.hint:
                console.print(f"\n[dim]Hint: {e.hint}[/dim]")
            raise typer.Exit(1) from e
        return

    provider = (provider or "builtin").strip().lower()
    if provider not in {"builtin", "openclaw"}:
        console.print(f"[red]Error:[/red] Unsupported provider '{provider}'")
        console.print("[dim]Supported values: builtin, openclaw[/dim]")
        raise typer.Exit(1)

    try:
        from notebooklm_tools.utils.cdp import (
            extract_cookies_via_cdp,
            extract_cookies_via_existing_cdp,
            get_browser_display_name,
            terminate_chrome,
        )

        launched_local_chrome = False

        # Default cdp_url for the builtin provider — used to detect when the
        # user explicitly passes their own --cdp-url value.
        _BUILTIN_CDP_DEFAULT = "http://127.0.0.1:18800"

        if wsl:
            # WSL mode: Launch Windows Chrome from WSL to avoid terminal corruption
            from notebooklm_tools.utils.wsl import (
                check_firewall_rule,
                get_windows_host_ip,
                is_wsl,
                launch_windows_chrome,
                terminate_windows_chrome,
                wait_for_cdp,
            )

            if not is_wsl():
                console.print(
                    "[yellow]Warning:[/yellow] --wsl flag used but not in WSL environment. Ignoring."
                )
            else:
                from notebooklm_tools.utils.wsl import DEFAULT_WSL_CDP_PORT

                wsl_port = DEFAULT_WSL_CDP_PORT
                # Chrome binds to localhost only (newer Chrome ignores
                # --remote-debugging-address=0.0.0.0), so we launch it
                # on a different port and rely on a netsh portproxy rule
                # (listenport=wsl_port -> connectport=chrome_port) to
                # bridge WSL traffic to localhost.
                chrome_port = wsl_port + 1
                windows_ip = get_windows_host_ip()

                if not windows_ip:
                    console.print("[red]Error:[/red] Could not determine Windows host IP.")
                    console.print("[dim]Hint: Check /etc/resolv.conf in WSL[/dim]")
                    raise typer.Exit(1)

                wsl_cdp_url = f"http://{windows_ip}:{wsl_port}"

                console.print("[bold]WSL2 detected - launching Windows Chrome[/bold]")
                console.print(
                    f"[dim]Windows host: {windows_ip}:{wsl_port} (proxy) -> localhost:{chrome_port} (Chrome)[/dim]"
                )
                console.print("[dim]Chrome binds to localhost; netsh portproxy bridges WSL[/dim]")

                # Check Windows Firewall

                if not check_firewall_rule(wsl_port):
                    console.print("\n[yellow]Windows Firewall Setup Required[/yellow]")
                    console.print(
                        f"\nA firewall rule is needed to allow WSL to connect to Windows Chrome on port {wsl_port}."
                    )
                    console.print(
                        "\n[bold]Step 1:[/bold] Open [cyan]Windows PowerShell as Administrator[/cyan] and run:"
                    )
                    console.print(
                        f'\n  New-NetFirewallRule -DisplayName "NotebookLM-CDP-{wsl_port}" -Direction Inbound -Action Allow -Protocol TCP -LocalPort {wsl_port} -RemoteAddress LocalSubnet\n'
                    )
                    console.print(
                        "[bold]Step 2:[/bold] After running the command above, press [bold]Enter[/bold] here to continue..."
                    )

                    # Simple wait for Enter
                    input()

                    # Re-check if rule was created
                    if check_firewall_rule(wsl_port):
                        console.print("[green]✓[/green] Firewall rule detected!")
                    else:
                        console.print(
                            "[yellow]Warning:[/yellow] Rule not yet detected, but will attempt to continue..."
                        )
                    console.print()
                else:
                    console.print("[dim]Windows Firewall: rule exists[/dim]")
                console.print()

                try:
                    chrome_process = launch_windows_chrome(chrome_port)
                    console.print(f"[dim]Chrome PID: {chrome_process.pid}[/dim]")
                except RuntimeError as e:
                    console.print(f"[red]Error:[/red] {e}")
                    console.print("[dim]Hint: Ensure Chrome is installed on Windows side[/dim]")
                    raise typer.Exit(1) from e

                console.print("[dim]Waiting for Chrome DevTools Protocol...[/dim]")
                if not wait_for_cdp(wsl_cdp_url, timeout=30):
                    console.print("[red]Error:[/red] Chrome did not start within 30 seconds.")
                    console.print("\n[yellow]Troubleshooting:[/yellow]")
                    console.print("  1. Ensure the Windows Firewall rule was created (step above)")
                    console.print("  2. If Chrome is still running, close it and retry")
                    console.print("  3. Or use manual mode: nlm login --manual --file <path>")
                    terminate_windows_chrome(chrome_process)
                    raise typer.Exit(1)

                console.print("[green]✓[/green] Chrome ready, connecting...\n")

                try:
                    result = extract_cookies_via_existing_cdp(
                        cdp_url=wsl_cdp_url,
                        wait_for_login=True,
                        login_timeout=300,
                    )
                finally:
                    # Always terminate Windows Chrome
                    terminate_windows_chrome(chrome_process)

                launched_local_chrome = True

        elif provider == "openclaw" or (provider == "builtin" and cdp_url != _BUILTIN_CDP_DEFAULT):
            # External CDP path: connect to an already-running browser.
            # Triggered by --provider openclaw OR when the user explicitly
            # passes a --cdp-url (indicating they have a running Chrome).
            label = "openclaw" if provider == "openclaw" else "builtin (external CDP)"
            console.print("[bold]Using external CDP authentication[/bold]")
            console.print(f"[dim]Provider: {label} | CDP: {cdp_url}[/dim]\n")

            result = extract_cookies_via_existing_cdp(
                cdp_url=cdp_url,
                wait_for_login=True,
                login_timeout=300,
            )
        else:
            # Default: builtin CDP mode - managed Chrome profile
            from notebooklm_tools.utils.cdp import get_browser_display_name, get_chrome_path

            # Detect browser early so messages show the correct name
            get_chrome_path()
            browser_name = get_browser_display_name()
            console.print(f"[bold]Launching {browser_name} for authentication...[/bold]")
            console.print("[dim]Using Chrome DevTools Protocol[/dim]\n")

            from notebooklm_tools.utils.config import (
                check_migration_sources,
                get_storage_dir,
                run_migration,
            )

            # Check if we need to migrate from legacy packages
            # IMPORTANT: Don't use get_chrome_profile_dir() here as it creates the directory,
            # which would prevent migration from running
            chrome_profile = get_storage_dir() / "chrome-profile"
            profile_exists = chrome_profile.exists() and (
                (chrome_profile / "Default").exists() or (chrome_profile / "Local State").exists()
            )

            if not profile_exists and not clear:
                sources = check_migration_sources()
                if sources["chrome_profiles"]:
                    console.print("[yellow]Found Chrome profile from legacy installation![/yellow]")
                    for src in sources["chrome_profiles"]:
                        console.print(f"  [dim]{src}[/dim]")
                    console.print("[dim]Migrating to new location...[/dim]")

                    actions = run_migration(dry_run=False)
                    for action in actions:
                        console.print(f"  [green]✓[/green] {action}")
                    console.print()

            console.print(f"Starting {browser_name}...")
            result = extract_cookies_via_cdp(
                auto_launch=True,
                wait_for_login=True,
                login_timeout=300,
                profile_name=profile,
                clear_profile=clear,
            )
            launched_local_chrome = True

        if result.get("reused_existing"):
            console.print(
                f"[yellow]Warning:[/yellow] Connected to an already-running {get_browser_display_name()} instance. "
                "Profile isolation may not apply — verify the account is correct."
            )

        cookies = result["cookies"]
        csrf_token = result.get("csrf_token", "")
        session_id = result.get("session_id", "")
        email = result.get("email", "")
        build_label = result.get("build_label", "")

        # Save to profile
        auth.save_profile(
            cookies=cookies,
            csrf_token=csrf_token,
            session_id=session_id,
            email=email,
            force=force,
            build_label=build_label,
        )

        console.print("\n[green]✓[/green] Successfully authenticated!")
        console.print(f"  Profile: {profile}")
        console.print(f"  Provider: {provider}")
        console.print(f"  Cookies: {len(cookies)} extracted")
        console.print(f"  CSRF Token: {'Yes' if csrf_token else 'No (will be auto-extracted)'}")
        if email:
            console.print(f"  Account: {email}")
        console.print(f"  Credentials saved to: {auth.profile_dir}")

        # Close builtin auth Chrome to release profile lock (enables headless auth later).
        # Keep it open briefly after auth so the user can see the completed login state.
        if launched_local_chrome:
            _close_launched_auth_browser(close_delay)

    except AccountMismatchError as e:
        if provider == "builtin" and not force:
            # The Chrome data dir has a stale Google login from a different
            # account.  Auto-retry: clear it and relaunch so the user can
            # log in with the correct account.
            console.print(
                f"\n[yellow]⚠[/yellow]  Wrong Google account detected "
                f"([bold]{result.get('email', '?')}[/bold] instead of "
                f"[bold]{e.stored_email}[/bold])."
            )
            console.print(
                f"[dim]Clearing stale browser session and relaunching {get_browser_display_name()}...[/dim]\n"
            )

            # Close the mismatch Chrome
            with contextlib.suppress(Exception):
                terminate_chrome()

            # Retry with cleared Chrome profile
            try:
                result = extract_cookies_via_cdp(
                    auto_launch=True,
                    wait_for_login=True,
                    login_timeout=300,
                    profile_name=profile,
                    clear_profile=True,
                )
                launched_local_chrome = True

                cookies = result["cookies"]
                csrf_token = result.get("csrf_token", "")
                session_id = result.get("session_id", "")
                email = result.get("email", "")
                build_label = result.get("build_label", "")

                auth.save_profile(
                    cookies=cookies,
                    csrf_token=csrf_token,
                    session_id=session_id,
                    email=email,
                    force=True,  # Allow overwrite on retry
                    build_label=build_label,
                )

                console.print("\n[green]✓[/green] Successfully authenticated!")
                console.print(f"  Profile: {profile}")
                console.print(f"  Provider: {provider}")
                console.print(f"  Cookies: {len(cookies)} extracted")
                console.print(
                    f"  CSRF Token: {'Yes' if csrf_token else 'No (will be auto-extracted)'}"
                )
                if email:
                    console.print(f"  Account: {email}")
                console.print(f"  Credentials saved to: {auth.profile_dir}")

                if launched_local_chrome:
                    _close_launched_auth_browser(close_delay)
            except NLMError as retry_err:
                console.print(f"\n[red]Error on retry:[/red] {retry_err.message}")
                if retry_err.hint:
                    console.print(f"\n[dim]Hint: {retry_err.hint}[/dim]")
                raise typer.Exit(1) from retry_err
        else:
            console.print(f"\n[red]Error:[/red] {e.message}")
            console.print(f"\n[yellow]Hint:[/yellow] {e.hint}")
            raise typer.Exit(1) from e
    except NLMError as e:
        console.print(f"\n[red]Error:[/red] {e.message}")
        if e.hint:
            console.print(f"\n[dim]Hint: {e.hint}[/dim]")
        raise typer.Exit(1) from e


def _login_all_saved_profiles(
    *,
    force: bool,
    clear_profile: bool,
    close_delay: int,
    stop_on_error: bool,
    start_index: int | None,
) -> None:
    """Refresh authentication for every saved profile using its isolated browser session."""
    from notebooklm_tools.core.auth import AuthManager
    from notebooklm_tools.core.exceptions import BrowserClosedError, NLMError
    from notebooklm_tools.utils.cdp import extract_cookies_via_cdp, terminate_chrome

    all_profiles = sorted(AuthManager.list_profiles(), key=_profile_sort_key)
    profiles = all_profiles
    if start_index is not None:
        profiles = [name for name in all_profiles if name.isdigit() and int(name) >= start_index]

    if not profiles:
        if start_index is not None and all_profiles:
            console.print(
                f"[red]Error:[/red] No numeric profiles found at or after {start_index}"
            )
        else:
            console.print("[red]Error:[/red] No saved profiles found")
            console.print("[dim]Run 'nlm login' or 'nlm login batch accounts.txt' first.[/dim]")
        raise typer.Exit(1)

    console.print(f"[bold]Login all profiles:[/bold] {len(profiles)} profile(s) found")
    console.print("[yellow]Passwords are never read or automated.[/yellow]")
    if start_index is not None:
        console.print(f"[dim]Starting from numeric profile {start_index}[/dim]")
    if clear_profile:
        console.print("[yellow]Browser sessions will be cleared before each login.[/yellow]")
    console.print()

    refreshed_profiles: list[str] = []
    failed_profiles: list[str] = []
    skipped_profiles: list[str] = []

    for profile in profiles:
        auth = AuthManager(profile)
        expected_email = ""

        console.rule(f"Profile {profile}")
        try:
            stored_profile = auth.load_profile()
            expected_email = (stored_profile.email or "").strip()
            if expected_email:
                console.print(f"Expected account: [cyan]{expected_email}[/cyan]")
        except NLMError as e:
            failed_profiles.append(profile)
            console.print(f"[red]Error:[/red] Could not load profile '{profile}': {e.message}")
            if e.hint:
                console.print(f"[dim]Hint: {e.hint}[/dim]")
            if stop_on_error:
                break
            console.print("[dim]Skipping profile and moving to the next one.[/dim]")
            continue

        logged_in = False
        try:
            result = extract_cookies_via_cdp(
                auto_launch=True,
                wait_for_login=True,
                login_timeout=300,
                profile_name=profile,
                clear_profile=clear_profile,
                login_hint=expected_email or None,
            )

            cookies = result["cookies"]
            csrf_token = result.get("csrf_token", "")
            session_id = result.get("session_id", "")
            actual_email = (result.get("email") or "").strip()
            build_label = result.get("build_label", "")

            if expected_email and actual_email and actual_email.lower() != expected_email.lower() and not force:
                console.print("[red]Error:[/red] Logged-in account does not match the saved profile")
                console.print(f"  Expected: {expected_email}")
                console.print(f"  Browser:  {actual_email}")
                console.print("[dim]No credentials were saved for this profile.[/dim]")
                failed_profiles.append(profile)
                if stop_on_error:
                    break
                console.print("[dim]Skipping profile and moving to the next one.[/dim]")
                continue

            auth.save_profile(
                cookies=cookies,
                csrf_token=csrf_token,
                session_id=session_id,
                email=actual_email or expected_email,
                force=force,
                build_label=build_label,
            )
            refreshed_profiles.append(profile)
            logged_in = True

            console.print("[green]✓[/green] Successfully authenticated")
            console.print(f"  Profile: {profile}")
            if actual_email or expected_email:
                console.print(f"  Account: {actual_email or expected_email}")
            console.print(f"  Credentials saved to: {auth.profile_dir}")
        except BrowserClosedError as e:
            skipped_profiles.append(profile)
            console.print(f"\n[yellow]Skipped:[/yellow] {e.message}")
            console.print("[dim]Moving to the next profile.[/dim]")
            if stop_on_error:
                break
        except NLMError as e:
            failed_profiles.append(profile)
            console.print(f"\n[red]Error:[/red] {e.message}")
            if e.hint:
                console.print(f"\n[dim]Hint: {e.hint}[/dim]")
            if stop_on_error:
                break
            console.print("[dim]Skipping profile and moving to the next one.[/dim]")
        except Exception as e:
            if _looks_like_browser_closed_error(e):
                skipped_profiles.append(profile)
                console.print("\n[yellow]Skipped:[/yellow] Browser was closed before login completed")
                console.print("[dim]Moving to the next profile.[/dim]")
            else:
                failed_profiles.append(profile)
                console.print(f"\n[red]Error:[/red] Unexpected authentication error: {e}")
                if not stop_on_error:
                    console.print("[dim]Skipping profile and moving to the next one.[/dim]")
            if stop_on_error:
                break
        finally:
            if logged_in:
                _close_launched_auth_browser(close_delay)
            else:
                terminate_chrome()

    console.rule("Login all profiles complete")
    console.print(f"[green]✓[/green] Refreshed {len(refreshed_profiles)} profile(s)")
    if skipped_profiles:
        console.print(f"[yellow]↷[/yellow] Skipped {len(skipped_profiles)} profile(s): {', '.join(skipped_profiles)}")
    if failed_profiles:
        console.print(f"[red]✗[/red] Failed {len(failed_profiles)} profile(s): {', '.join(failed_profiles)}")
        raise typer.Exit(1)


@login_app.command("batch")
def login_batch(
    accounts_file: Path = typer.Argument(  # noqa: B008
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Text file containing one email address per line",
    ),
    start_index: int | None = typer.Option(
        None,
        "--start-index",
        "--start",
        help="First numeric profile name to create (default: next after highest existing number)",
    ),
    timeout: int = typer.Option(
        300,
        "--timeout",
        help="Seconds to wait for each interactive Google sign-in",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing numeric profiles",
    ),
    close_delay: int = typer.Option(
        AUTH_BROWSER_CLOSE_DELAY_SECONDS,
        "--close-delay",
        help="Seconds to keep the browser open after each successful login",
    ),
) -> None:
    """Interactively authenticate many accounts from an email-only file.

    Passwords are never read, stored, or typed. The browser opens for each
    account and the user completes Google sign-in manually.
    """
    from notebooklm_tools.core.auth import AuthManager
    from notebooklm_tools.core.exceptions import NLMError
    from notebooklm_tools.utils.cdp import extract_cookies_via_cdp, terminate_chrome

    existing_profiles = AuthManager.list_profiles()
    if start_index is None:
        start_index = _next_numeric_profile_index(existing_profiles)
    elif start_index < 1:
        console.print("[red]Error:[/red] --start-index must be 1 or greater")
        raise typer.Exit(1)
    if timeout <= 0:
        console.print("[red]Error:[/red] --timeout must be greater than 0")
        raise typer.Exit(1)
    if close_delay < 0:
        console.print("[red]Error:[/red] --close-delay cannot be negative")
        raise typer.Exit(1)

    try:
        emails = _load_batch_login_emails(accounts_file)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("[dim]Use one email per line. Do not include passwords.[/dim]")
        raise typer.Exit(1) from e

    console.print(f"[bold]Batch login:[/bold] {len(emails)} account(s) loaded")
    console.print("[yellow]Passwords are never read or automated.[/yellow]")
    console.print(
        f"[dim]Profiles will be saved as {start_index}, {start_index + 1}, "
        f"{start_index + 2}, ...[/dim]\n"
    )

    saved_profiles: list[str] = []
    for offset, expected_email in enumerate(emails):
        profile = str(start_index + offset)
        auth = AuthManager(profile)

        console.rule(f"Profile {profile}")
        console.print(f"Expected account: [cyan]{expected_email}[/cyan]")

        if auth.profile_exists() and not force:
            console.print(f"[red]Error:[/red] Profile '{profile}' already exists")
            console.print("[dim]Use --force to overwrite it, or choose another --start-index.[/dim]")
            raise typer.Exit(1)

        logged_in = False
        try:
            result = extract_cookies_via_cdp(
                auto_launch=True,
                wait_for_login=True,
                login_timeout=timeout,
                profile_name=profile,
                clear_profile=True,
                login_hint=expected_email,
            )

            cookies = result["cookies"]
            csrf_token = result.get("csrf_token", "")
            session_id = result.get("session_id", "")
            actual_email = (result.get("email") or "").strip()
            build_label = result.get("build_label", "")

            if actual_email and actual_email.lower() != expected_email.lower():
                console.print("[red]Error:[/red] Logged-in account does not match the file")
                console.print(f"  Expected: {expected_email}")
                console.print(f"  Browser:  {actual_email}")
                console.print("[dim]No credentials were saved for this profile.[/dim]")
                raise typer.Exit(1)

            auth.save_profile(
                cookies=cookies,
                csrf_token=csrf_token,
                session_id=session_id,
                email=actual_email or expected_email,
                force=force,
                build_label=build_label,
            )
            saved_profiles.append(profile)
            logged_in = True

            console.print("[green]✓[/green] Successfully authenticated")
            console.print(f"  Profile: {profile}")
            console.print(f"  Account: {actual_email or expected_email}")
            console.print(f"  Credentials saved to: {auth.profile_dir}")
        except typer.Exit:
            raise
        except NLMError as e:
            console.print(f"\n[red]Error:[/red] {e.message}")
            if e.hint:
                console.print(f"\n[dim]Hint: {e.hint}[/dim]")
            raise typer.Exit(1) from e
        finally:
            if logged_in:
                _close_launched_auth_browser(close_delay)
            else:
                terminate_chrome()

    console.rule("Batch login complete")
    console.print(f"[green]✓[/green] Saved {len(saved_profiles)} profile(s): {', '.join(saved_profiles)}")
    if saved_profiles:
        console.print(f"[dim]Switch default profile with: nlm login switch {saved_profiles[0]}[/dim]")


@profile_app.command("list")
def profile_list() -> None:
    """List all authentication profiles."""
    from notebooklm_tools.core.auth import AuthManager

    profiles = AuthManager.list_profiles()

    if not profiles:
        console.print("[dim]No profiles found.[/dim]")
        console.print("\nRun 'nlm login' to create a profile.")
        return

    console.print("[bold]Available profiles:[/bold]")
    for name in profiles:
        try:
            auth = AuthManager(name)
            p = auth.load_profile()
            email = p.email or "Unknown"
            console.print(f"  [cyan]{name}[/cyan]: {email}")
        except Exception:
            console.print(f"  [cyan]{name}[/cyan]: [dim](invalid)[/dim]")


@profile_app.command("delete")
def profile_delete(
    profile: str = typer.Argument(..., help="Profile name to delete"),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Delete a profile and its credentials."""
    from notebooklm_tools.core.auth import AuthManager

    auth = AuthManager(profile)

    # Allow deleting invalid profiles (check if name is in list_profiles)
    if profile not in AuthManager.list_profiles():
        console.print(f"[red]Error:[/red] Profile '{profile}' not found")
        raise typer.Exit(1)

    if not confirm:
        typer.confirm(
            f"Are you sure you want to delete profile '{profile}'?",
            abort=True,
        )

    auth.delete_profile()
    console.print(f"[green]✓[/green] Deleted profile: {profile}")


@profile_app.command("rename")
def profile_rename(
    old_name: str = typer.Argument(..., help="Current profile name"),
    new_name: str = typer.Argument(..., help="New profile name"),
) -> None:
    """Rename an authentication profile."""
    from notebooklm_tools.core.auth import AuthManager
    from notebooklm_tools.core.exceptions import NLMError

    # Check if old profile exists
    old_auth = AuthManager(old_name)
    if not old_auth.profile_exists():
        console.print(f"[red]Error:[/red] Profile '{old_name}' not found")
        raise typer.Exit(1)

    # Check if new profile name already exists
    new_auth = AuthManager(new_name)
    if new_auth.profile_exists():
        console.print(f"[red]Error:[/red] Profile '{new_name}' already exists")
        raise typer.Exit(1)

    try:
        # Load old profile data
        profile_data = old_auth.load_profile()

        # Save with new name
        new_auth.save_profile(
            cookies=profile_data.cookies,
            csrf_token=profile_data.csrf_token,
            session_id=profile_data.session_id,
            email=profile_data.email,
            build_label=profile_data.build_label,
        )

        # Delete old profile
        old_auth.delete_profile()

        console.print(f"[green]✓[/green] Renamed profile from '{old_name}' to '{new_name}'")
    except NLMError as e:
        console.print(f"[red]Error:[/red] {e.message}")
        if e.hint:
            console.print(f"\n[dim]Hint: {e.hint}[/dim]")
        raise typer.Exit(1) from e


@login_app.command("switch")
def login_switch(
    profile: str = typer.Argument(..., help="Profile name to switch to"),
) -> None:
    """Switch the default profile for all commands."""
    from notebooklm_tools.core.auth import AuthManager
    from notebooklm_tools.utils.config import get_config, save_config

    # Check if profile exists
    auth = AuthManager(profile)
    if not auth.profile_exists():
        console.print(f"[red]Error:[/red] Profile '{profile}' not found")
        console.print("\nAvailable profiles:")
        for name in AuthManager.list_profiles():
            console.print(f"  [cyan]{name}[/cyan]")
        raise typer.Exit(1)

    # Update config
    config = get_config()
    old_profile = config.auth.default_profile
    config.auth.default_profile = profile
    save_config(config)

    # Show confirmation with account info
    try:
        p = auth.load_profile()
        email = p.email or "Unknown"
        console.print(f"[green]✓[/green] Switched default profile to [cyan]{profile}[/cyan]")
        console.print(f"  Account: {email}")
        if old_profile != profile:
            console.print(f"  [dim]Previous: {old_profile}[/dim]")
    except Exception:
        console.print(f"[green]✓[/green] Switched default profile to [cyan]{profile}[/cyan]")


# Register profile commands under login
login_app.add_typer(profile_app, name="profile")

# Register login app with nested profile commands
app.add_typer(login_app, name="login")

# Register noun-first subcommands (existing structure)
app.add_typer(notebook_app, name="notebook", help="Manage notebooks")
app.add_typer(label_app, name="label", help="Manage source labels")
app.add_typer(note_app, name="note", help="Manage notes")
app.add_typer(source_app, name="source", help="Manage sources")
app.add_typer(chat_app, name="chat", help="Configure chat settings")
app.add_typer(studio_app, name="studio", help="Manage studio artifacts")
app.add_typer(research_app, name="research", help="Research and discover sources")
app.add_typer(alias_app, name="alias", help="Manage ID aliases")
app.add_typer(config_app, name="config", help="Manage configuration")
app.add_typer(download_app, name="download", help="Download artifacts (audio, video, etc)")
app.add_typer(share_app, name="share", help="Manage notebook sharing")
app.add_typer(export_app, name="export", help="Export artifacts to Google Docs/Sheets")
app.add_typer(skill_app, name="skill", help="Install skills for AI tools")
app.add_typer(setup_app, name="setup", help="Configure MCP server for AI tools")
app.add_typer(doctor_app, name="doctor", help="Diagnose installation and configuration")
app.add_typer(batch_app, name="batch", help="Batch operations across notebooks")
app.add_typer(cross_app, name="cross", help="Cross-notebook queries")
app.add_typer(pipeline_app, name="pipeline", help="Run multi-step pipelines")
app.add_typer(tag_app, name="tag", help="Manage notebook tags")

# Generation commands as top-level
app.add_typer(audio_app, name="audio", help="Create audio overviews")
app.add_typer(report_app, name="report", help="Create reports")
app.add_typer(quiz_app, name="quiz", help="Create quizzes")
app.add_typer(flashcards_app, name="flashcards", help="Create flashcards")
app.add_typer(mindmap_app, name="mindmap", help="Create and manage mind maps")
app.add_typer(slides_app, name="slides", help="Create slide decks")
app.add_typer(infographic_app, name="infographic", help="Create infographics")
app.add_typer(video_app, name="video", help="Create video overviews")
app.add_typer(data_table_app, name="data-table", help="Create data tables")

# Auth is now under login (removed auth_app registration)

# Register verb-first subcommands (alternative structure)
app.add_typer(create_app, name="create", help="Create resources (notebooks, audio, video, etc)")
app.add_typer(list_app, name="list", help="List resources (notebooks, sources, artifacts)")
app.add_typer(get_app, name="get", help="Get details about resources")
app.add_typer(delete_app, name="delete", help="Delete resources (notebooks, sources, artifacts)")
app.add_typer(add_app, name="add", help="Add resources (sources to notebooks)")
app.add_typer(rename_app, name="rename", help="Rename resources")
app.add_typer(status_app, name="status", help="Check status of resources")
app.add_typer(describe_app, name="describe", help="Get AI-generated descriptions and summaries")
app.add_typer(query_app, name="query", help="Chat with notebook sources")
app.add_typer(sync_app, name="sync", help="Sync resources (Drive sources)")
app.add_typer(content_app, name="content", help="Get raw content from sources")
app.add_typer(stale_app, name="stale", help="List stale resources that need syncing")
app.add_typer(configure_app, name="configure", help="Configure settings")
app.add_typer(set_app, name="set", help="Set values (aliases, config)")
app.add_typer(show_app, name="show", help="Show information")
app.add_typer(install_app, name="install", help="Install resources (skills)")
app.add_typer(uninstall_app, name="uninstall", help="Uninstall resources (skills)")
app.add_typer(update_app, name="update", help="Update resources (skills)")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit",
    ),
    ai: bool = typer.Option(
        False,
        "--ai",
        help="Output AI-friendly documentation for this CLI",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug logging (shows raw API responses)",
    ),
) -> None:
    """
    NLM - Command-line interface for Google NotebookLM.

    Use 'nlm <command> --help' for help on specific commands.
    """
    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s %(levelname)s: %(message)s",
        )
        logging.getLogger("notebooklm_mcp.api").setLevel(logging.DEBUG)

    if version:
        from notebooklm_tools.cli.utils import check_for_updates

        console.print(f"nlm version {__version__}")

        # Check for updates when showing version
        update_available, latest = check_for_updates()
        if not (update_available and latest):
            console.print("[dim]You are on the latest version.[/dim]")
        raise typer.Exit()

    if ai:
        from notebooklm_tools.cli.ai_docs import print_ai_docs

        print_ai_docs()
        raise typer.Exit()

    # Show help if no command provided
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


def cli_main():
    """Main CLI entry point with error handling."""
    import sys

    from notebooklm_tools.utils.io_encoding import configure_stdio_utf8_on_windows

    configure_stdio_utf8_on_windows()

    try:
        app()
    except Exception as e:
        # Import here to avoid circular dependencies
        from notebooklm_tools.core.errors import ClientAuthenticationError
        from notebooklm_tools.core.exceptions import (
            AuthenticationError,
            NLMError,
        )

        # Handle authentication errors cleanly
        if isinstance(e, (AuthenticationError, ClientAuthenticationError)):
            console.print("\n[red]✗ Authentication Error[/red]")
            console.print(f"  {str(e)}")
            console.print("\n[yellow]→[/yellow] Run [cyan]nlm login[/cyan] to re-authenticate\n")
            sys.exit(1)

        # Handle other NLM errors cleanly
        elif isinstance(e, NLMError):
            console.print(f"\n[red]✗ Error:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]{e.hint}[/dim]\n")
            sys.exit(1)

        # For unexpected errors, show the traceback
        else:
            raise
    finally:
        # Check for updates after command execution (runs even on typer.Exit)
        from notebooklm_tools.cli.utils import print_update_notification

        print_update_notification()


if __name__ == "__main__":
    cli_main()
