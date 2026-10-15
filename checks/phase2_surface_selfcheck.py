"""Surface registration checks after Engine removal."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mecha.errors import MechaError
from mecha.surface import EntryKind, ExecutionContext, Surface

def main() -> int:
    out=[]
    s=Surface()
    seen=[]
    dispose=s.register(EntryKind.QUERY,"echo",lambda word="": f"echo:{word}",called_by="check")
    if s.query("echo",word="x")!="echo:x": out.append("query dispatch")
    try: s.register(EntryKind.QUERY,"echo",lambda:None,called_by="check"); out.append("duplicate")
    except MechaError: pass
    dispose()
    try: s.query("echo"); out.append("dispose")
    except MechaError: pass
    ctx=ExecutionContext(); ctx.report_progress(.5,"half"); ctx.request_cancel()
    if not (ctx.cancelled and ctx.progress()==[(.5,"half")]): out.append("context")
    if out:
        print("phase2_surface_selfcheck: FAIL",out); return 1
    print("phase2_surface_selfcheck: PASS"); return 0
if __name__=="__main__": raise SystemExit(main())
