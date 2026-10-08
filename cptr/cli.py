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
    url = f"http://{display_host}:{port}/?token={token}"
    _save_startup_token(token)

    print(f"\n  ➜  {url}\n")
    # An in-app update restarts the server; the browser is already open then.
    restarted = os.environ.pop("CPTR_RESTARTED", None)
    if not headless and not restarted:
        import threading
        import webbrowser

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


def _save_startup_token(token: str) -> None:
    """Keep this run's setup token where install.sh can find it for the first-run URL."""
    import os
    from pathlib import Path

    # Not cptr.env: importing it here would consume CPTR_STARTUP_TOKEN before the app starts.
    data_dir = Path(os.environ.get("CPTR_DATA_DIR", str(Path.home() / ".cptr")))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / "startup-token"
        path.touch(mode=0o600, exist_ok=True)
        path.write_text(token)
    except OSError:
        pass


def main():
    cli()


if __name__ == "__main__":
    main()
