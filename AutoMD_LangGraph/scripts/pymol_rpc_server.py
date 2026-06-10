"""
PyMOL XML-RPC server plugin for AutoMD.

Loaded inside PyMOL as a plugin. Listens on 0.0.0.0:9123 so Docker
containers (and other network clients) can drive PyMOL remotely.

Methods exposed:
  - load(path, name)        : cmd.load(path, name)
  - delete(name)            : cmd.delete(name)
  - do(cmd_str)             : cmd.do(cmd_str)
  - zoom(name)              : cmd.zoom(name)
  - get_view()              : return current view as a dict
  - get_docking_box()       : return {center_x,y,z, size_x,y,z}
                              priority: 1) named "sele" selection
                                        2) current view extent
                              pad 6 A on each side
  - get_sele_residues()     : return list of (chain, resi, resn) in "sele"
  - screenshot(path)        : cmd.png(path, dpi=150)
  - ping()                  : return "pong" (health check)

How to use (in PyMOL command line or script):
  1. Copy this file to anywhere (e.g. D:\pymol\pymol_rpc_server.py)
  2. In PyMOL, run:
        run D:\pymol\pymol_rpc_server.py
        pymol_rpc_start 0.0.0.0 9123
  3. To stop:
        pymol_rpc_stop

Or via command line:
  PyMOLWin.exe -xkrQ pymol_rpc_server.py

Author: AutoMD project (generated 2026-06-06).
"""

from __future__ import annotations

import os
import sys
import time
import threading
from xmlrpc.server import SimpleXMLRPCServer, SimpleXMLRPCRequestHandler
from socketserver import ThreadingMixIn

try:
    from pymol import cmd
except ImportError:
    # Not running inside PyMOL — fail loud, but allow import for syntax check
    cmd = None
    print("[pymol_rpc] WARNING: pymol.cmd not available; this script must run inside PyMOL",
          file=sys.stderr)


# ============================================================================
# Threaded server so multiple RPC calls don't block each other
# ============================================================================

class _ThreadedXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True
    allow_reuse_address = True


_rpc_server: _ThreadedXMLRPCServer | None = None
_rpc_thread: threading.Thread | None = None


# ============================================================================
# RPC method implementations
# ============================================================================

def _safe_call(fn, *args, **kwargs):
    """Run a pymol cmd and return a serializable dict result."""
    try:
        result = fn(*args, **kwargs)
        return {"ok": True, "data": result}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def load(path: str, name: str) -> dict:
    if cmd is None:
        return {"ok": False, "error": "pymol.cmd not available"}
    # PyMOL needs forward slashes in Windows paths
    path = path.replace("\\", "/")
    return _safe_call(cmd.load, path, name)


def delete(name: str) -> dict:
    if cmd is None:
        return {"ok": False, "error": "pymol.cmd not available"}
    return _safe_call(cmd.delete, name)


def do(cmd_str: str) -> dict:
    if cmd is None:
        return {"ok": False, "error": "pymol.cmd not available"}
    return _safe_call(cmd.do, cmd_str)


def zoom(name: str) -> dict:
    if cmd is None:
        return {"ok": False, "error": "pymol.cmd not available"}
    return _safe_call(cmd.zoom, name)


def get_view() -> dict:
    if cmd is None:
        return {"ok": False, "error": "pymol.cmd not available"}
    return _safe_call(cmd.get_view)


def screenshot(path: str, dpi: int = 150) -> dict:
    if cmd is None:
        return {"ok": False, "error": "pymol.cmd not available"}
    path = path.replace("\\", "/")
    return _safe_call(cmd.png, path, dpi)


def ping() -> str:
    return "pong"


# ----- methods used by chat.py pymol_* tools -----

def get_object_list() -> list[str]:
    """Return list of loaded PyMOL object names.
    Used by chat.py pymol_start / pymol_status tools.
    Equivalent to cmd.get_names("objects") but as a flat list of strings."""
    if cmd is None:
        return []
    return list(cmd.get_names("objects") or [])


def get_session_info() -> dict:
    """Return session info dict with at least 'objects' and 'names' keys.
    Used by chat.py pymol_status tool.

    'objects' = list of loaded object names
    'names'   = list of named selections
    """
    if cmd is None:
        return {"objects": [], "names": []}
    return {
        "objects": list(cmd.get_names("objects") or []),
        "names": list(cmd.get_names("selections") or []),
    }


def quit_pymol() -> str:
    """Close PyMOL gracefully. Used by chat.py pymol_quit tool."""
    if cmd is None:
        return "pymol.cmd not available"
    try:
        cmd.do("quit")
        return "quit requested"
    except Exception as e:
        return f"quit error: {e}"


# ----- get_docking_box: the main method visual_docking.py calls -----

def _get_extent_of(name_or_sele: str) -> dict | None:
    """Return {center_x, center_y, center_z, size_x, size_y, size_z} for
    the given object/selection, or None if it has no atoms."""
    if cmd is None or not cmd.count_atoms(f"({name_or_sele})"):
        return None
    # PyMOL's get_extent returns [(xmin, ymin, zmin), (xmax, ymax, zmax)]
    extent = cmd.get_extent(name_or_sele)
    if not extent or len(extent) != 2:
        return None
    (xmin, ymin, zmin), (xmax, ymax, zmax) = extent
    cx = (xmin + xmax) / 2.0
    cy = (ymin + ymax) / 2.0
    cz = (zmin + zmax) / 2.0
    sx = (xmax - xmin)
    sy = (ymax - ymin)
    sz = (zmax - zmin)
    return {
        "center_x": cx, "center_y": cy, "center_z": cz,
        "size_x": sx, "size_y": sy, "size_z": sz,
    }


def get_docking_box(padding: float = 6.0) -> dict:
    """Return docking box.

    Priority:
      1) Named selection "sele"  (user clicked atoms → made a selection)
      2) Current viewport extent (what the user is zoomed in on)
      3) Full receptor extent (fallback)
    """
    if cmd is None:
        return {"ok": False, "error": "pymol.cmd not available"}

    # Try "sele" first
    box = _get_extent_of("sele")
    source = "sele"
    if box is None:
        # Try the current viewport: cmd.get_view() returns a 18-float list
        # where indices 0..8 + 12..14 encode the rotation/translation, and
        # index 18 = the front-plane half-distance (clip). We don't have a
        # direct "viewport extent" in PyMOL, so we approximate by getting
        # the extent of all visible objects.
        try:
            box = _get_extent_of("visible")
            source = "visible"
        except Exception:
            pass
    if box is None:
        # Final fallback: any loaded object
        objs = cmd.get_names("objects")
        for name in objs:
            box = _get_extent_of(name)
            if box is not None:
                source = name
                break
    if box is None:
        return {"ok": False, "error": "no atoms loaded to define box"}

    # Add padding
    box = {
        "center_x": box["center_x"],
        "center_y": box["center_y"],
        "center_z": box["center_z"],
        "size_x": box["size_x"] + 2 * padding,
        "size_y": box["size_y"] + 2 * padding,
        "size_z": box["size_z"] + 2 * padding,
    }
    return {"ok": True, "data": box, "source": source, "padding": padding}


def get_sele_residues() -> dict:
    """Return list of (chain, resi, resn) tuples in the 'sele' selection."""
    if cmd is None:
        return {"ok": False, "error": "pymol.cmd not available"}
    if not cmd.count_atoms("sele"):
        return {"ok": True, "data": []}
    # Iterate atoms in sele and group by residue
    residues = {}
    cmd.iterate("sele", "chain_resi = (chain, resi, resn)", space={"chain_resi": None})
    atoms = cmd.get_model("sele")
    for atom in atoms.atom:
        key = (atom.chain, atom.resi, atom.resn)
        residues[key] = True
    return {"ok": True, "data": [{"chain": c, "resi": r, "resn": n} for (c, r, n) in residues.keys()]}


# ============================================================================
# Lifecycle: pymol_rpc_start / pymol_rpc_stop (PyMOL plugin entry points)
# ============================================================================

def pymol_rpc_start(*args, **kwargs):
    """Start the XML-RPC server. Blocks until pymol_rpc_stop() is called.

    Usage from PyMOL command line (no args = use defaults 0.0.0.0:9123):
        pymol_rpc_start
        pymol_rpc_start 0.0.0.0
        pymol_rpc_start 0.0.0.0 9123

    Notes:
    - PyMOL's cmd.extend injects `_self=cmd` as a kwarg, so we accept **kwargs
      and ignore unknown ones.
    - All positional args arrive as strings, so we coerce port to int.
    - The function is also callable directly from Python with proper types
      (e.g. from a wrapper script).
    """
    global _rpc_server, _rpc_thread

    # Parse args flexibly: 0, 1, or 2 args, all as strings
    if len(args) == 0:
        host = "0.0.0.0"
        port = 9123
    elif len(args) == 1:
        # Could be "host" or "host,port" (PyMOL sometimes passes comma-joined)
        arg = str(args[0])
        if "," in arg:
            parts = arg.split(",")
            host = parts[0].strip().strip('"').strip("'")
            port = int(parts[1].strip().strip('"').strip("'"))
        else:
            host = arg.strip().strip('"').strip("'")
            port = 9123
    else:
        host = str(args[0]).strip().strip('"').strip("'")
        port = int(str(args[1]).strip().strip('"').strip("'"))

    if _rpc_server is not None:
        print(f"[pymol_rpc] already running on port {_rpc_server.server_address[1]}")
        return

    # Register all functions under their bare names
    server = _ThreadedXMLRPCServer((host, port), SimpleXMLRPCRequestHandler, allow_none=True)
    server.register_function(load, "load")
    server.register_function(delete, "delete")
    server.register_function(do, "do")
    server.register_function(zoom, "zoom")
    server.register_function(get_view, "get_view")
    server.register_function(get_docking_box, "get_docking_box")
    server.register_function(get_sele_residues, "get_sele_residues")
    server.register_function(screenshot, "screenshot")
    server.register_function(ping, "ping")
    # Methods used by chat.py pymol_* tools
    server.register_function(get_object_list, "get_object_list")
    server.register_function(get_session_info, "get_session_info")
    server.register_function(quit_pymol, "quit_pymol")

    _rpc_server = server

    # Run serve_forever in a background thread so we don't BLOCK PyMOL's main
    # thread. Without this, PyMOL becomes unresponsive as soon as pymol_rpc_start
    # is called from the command line, and the RPC methods become unreachable.
    def _serve():
        try:
            server.serve_forever()
        except Exception as e:
            print(f"[pymol_rpc] server crashed: {e}", file=sys.stderr)
        finally:
            server.server_close()

    _rpc_thread = threading.Thread(target=_serve, daemon=True, name="pymol_rpc_server")
    _rpc_thread.start()

    # Give the server a moment to bind and start listening
    time.sleep(0.2)
    print(f"[pymol_rpc] listening on {host}:{port} (in background thread; PyMOL is free)")


def pymol_rpc_stop(*args, **kwargs):
    """Stop the XML-RPC server (if running). Accepts **kwargs because PyMOL
    cmd.extend injects _self=cmd as a keyword arg when called from PyMOL's
    command line."""
    global _rpc_server, _rpc_thread
    if _rpc_server is not None:
        _rpc_server.shutdown()  # stops serve_forever() in the background thread
        _rpc_server = None
        _rpc_thread = None
        print("[pymol_rpc] stop requested (background thread exiting)")
    else:
        print("[pymol_rpc] not running")


# PyMOL plugin convention: register entry points so `pymol_rpc_start`
# is callable from the PyMOL command line after `run pymol_rpc_server.py`.
if cmd is not None:
    try:
        cmd.extend("pymol_rpc_start", pymol_rpc_start)
        cmd.extend("pymol_rpc_stop", pymol_rpc_stop)
    except Exception:
        pass  # extend may not be available in all PyMOL versions


# ============================================================================
# Standalone test (if run outside PyMOL)
# ============================================================================

if __name__ == "__main__":
    if cmd is None:
        print("This script must run inside PyMOL. Try:")
        print('  PyMOLWin.exe -xkrQ pymol_rpc_server.py')
        print("Or inside PyMOL:")
        print("  run pymol_rpc_server.py")
        print('  pymol_rpc_start "0.0.0.0", 9123')
        sys.exit(1)
    # Auto-start when run via `pymol -xkrQ`
    import threading
    threading.Thread(target=pymol_rpc_start, kwargs={"host": "0.0.0.0", "port": 9123}, daemon=True).start()
    print("[pymol_rpc] started in background thread; PyMOL is ready for RPC")
