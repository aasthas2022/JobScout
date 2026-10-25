"""Backward-compatible Greenhouse queue builder: python -m application.extract."""
from .greenhouse.build_queue import *  # noqa: F401,F403
from .greenhouse.build_queue import main

if __name__ == "__main__":
    main()
