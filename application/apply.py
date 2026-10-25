"""Backward-compatible Greenhouse runner: python -m application.apply."""
from .greenhouse.run import *  # noqa: F401,F403
from .greenhouse.run import main

if __name__ == "__main__":
    main()
