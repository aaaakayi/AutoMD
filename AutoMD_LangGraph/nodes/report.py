from __future__ import annotations

import os
import sys

nodes_dir = os.path.dirname(__file__)
package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
project_root = os.path.abspath(os.path.join(nodes_dir, "..", ".."))
if package_root not in sys.path:
    sys.path.insert(0, package_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.graph import END
from langgraph.types import Command

from State import AutoMDState

from .common import final_report_from_state


def report(state: AutoMDState) -> Command:
    final_report = state.get("final_report") or final_report_from_state(state)
    return Command(update={"final_report": final_report}, goto=END)
