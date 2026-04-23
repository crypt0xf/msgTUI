"""Client entry point."""
from client.tui.app import MsgTuiApp


def main():
    app = MsgTuiApp()
    app.run()


if __name__ == "__main__":
    main()
