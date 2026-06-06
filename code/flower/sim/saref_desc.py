"""
Re-exports core/saref_desc so there is a single implementation.
Uses importlib to load the core module by file path under a different
internal name (_saref_core) to avoid the circular import that would
occur if we did a plain 'from saref_desc import ...' — Python would
find this file itself in sys.modules since it shares the same module name.
"""

import importlib.util
import os
import sys

_core_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "core", "saref_desc.py"
)
_spec = importlib.util.spec_from_file_location("_saref_core", _core_path)
_mod  = importlib.util.module_from_spec(_spec)
sys.modules["_saref_core"] = _mod
_spec.loader.exec_module(_mod)

create_in_acme    = _mod.create_in_acme
SD_CONTAINER_NAME = _mod.SD_CONTAINER_NAME
