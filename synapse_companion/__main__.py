"""``python synapse_companion.pyz``, ``python -m synapse_companion`` or ``python synapse_companion``."""

import sys

if not __package__:  # run as a folder or a .pyz: make the package importable
    from pathlib import Path

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here if (here / "synapse_companion").is_dir() else here.parent))

from synapse_companion.cli import main

sys.exit(main())
