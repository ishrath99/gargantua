"""Allow ``python -m gargantua <subcommand>`` as an alternative to the entry point."""

from gargantua.admin import app

if __name__ == "__main__":
    app()
