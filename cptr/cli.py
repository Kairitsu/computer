import click
import uvicorn


@click.group()
def cli():
    """Your computer, from anywhere."""
    pass


@cli.command()
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind to. Use 0.0.0.0 to allow access from other devices.",
)
@click.option("--port", default=8000, type=int, help="Port to bind to.")
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload.")
@click.option("--headless", is_flag=True, default=False, help="Don't open browser.")
def run(host: str, port: int, reload: bool, headless: bool):
    """Start the cptr server."""
    import os
    import secrets

    display_host = "localhost" if host == "0.0.0.0" else host

    token = secrets.token_hex(32)
    os.environ["CPTR_STARTUP_TOKEN"] = token
    os.environ["CPTR_PORT"] = str(port)
    _save_startup_token(token)

    # The token stays in the mode-600 file. It is not written to stdout, which
    # systemd would keep. A local browser open is the only place the address
    # includes it, and that address is not logged here.
    restarted = os.environ.pop("CPTR_RESTARTED", None)
    if not headless and not restarted:
        import threading
        import webbrowser

        url = f"http://{display_host}:{port}/?token={token}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    if reload:
        uvicorn.run("cptr.app:application", host=host, port=port, reload=True)
        return

    # Run the server directly (what uvicorn.run does without reload) so an in-app
    # update can stop it and start the updated code in this same process.
    from cptr.utils import updater

    server = uvicorn.Server(uvicorn.Config("cptr.app:application", host=host, port=port))
    updater.register_server(server)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    updater.restart_if_requested()
    if not server.started:
        raise SystemExit(3)


@cli.command("reset-2fa")
@click.argument("username")
def reset_2fa(username: str):
    """Turn off two-step sign-in for USERNAME (lost authenticator and recovery codes)."""
    import asyncio

    async def _reset() -> bool:
        from cptr.models import Auth

        auth = await Auth.get_by_username_any(username)
        if auth is None:
            return False
        await Auth.clear_totp(auth.user_id)
        return True

    if not asyncio.run(_reset()):
        raise click.ClickException(f"no user named {username!r}")
    click.echo(f"Two-step sign-in is off for {username}. Sign in with the password alone.")


def _save_startup_token(token: str) -> None:
    """Keep this run's setup token in a private file. Nothing prints it."""
    # Not cptr.env: importing it here would consume CPTR_STARTUP_TOKEN before the app starts.
    from cptr.utils.startup_token import write_startup_token

    write_startup_token(token)


def main():
    cli()


if __name__ == "__main__":
    main()
