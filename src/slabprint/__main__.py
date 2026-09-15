"""`python -m slabprint`.

Goes through run(), not main(), so that this entry point reports failures the
same way the installed console script does instead of printing a traceback.
"""

from .cli import run

raise SystemExit(run())
