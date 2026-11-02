"""Unreal projects only (ledger_config.json "unreal": true). Dump the Blueprint graphs a review subtask covers, node
by node, for the department agent to read.

A department agent has no editor tools, and an offline reader sees property values, not graph wiring. So before
`ledger.py dispatch` sends out a review that covers Blueprints, the hub - which owns the editor - runs this INSIDE
the running editor (its Python console in the Output Log, or over Unreal's remote execution). dispatch refuses such
a review until the dump exists, and prints what to run:

    exec(open(r'<repo>/Docs/Ledger/review_graph_dump.py', encoding='utf-8').read()); run('PR-Enn.mm')

Outside the editor, --plan lists what would be dumped and where, and touches nothing:

    python Docs/Ledger/review_graph_dump.py --plan PR-Enn.mm

Graphs are read node by node with the editor toolset's find_nodes and get_node_infos (Unreal 5.8's EditorToolset;
adapt dump_graph() for another reader). One text file per graph, with full node names so a finding can cite them
and literal values up to 120 characters, plus _index.txt. Read-only: nothing is changed, compiled or saved. The
output goes under Saved/Ledger/, which version control ignores: a dump is a working copy for one review, and a
finding points at the asset and node, not at the dump file.

_index.txt is written last and its first line names the review and exactly the tasks it covers; dispatch accepts a
dump only when that line matches, so an unfinished, empty or stale dump is refused. Assets that fail to load are
listed as not reviewed. A map's level Blueprint cannot be reached from Python without opening the map in the
editor, so maps are listed as not dumped, and the dispatch prompt tells the agent to give them the verdict
'not read'.
"""
import os
import sys

MAX_ASSETS = 300  # a review covering more is too wide to dump in one go: narrow the covered tasks' files
VALUE_CHARS = 120


def asset_candidates(repo: str, files: list) -> list:
    """(/Game/ package path, is it a map) for every .uasset and .umap under the covered files: a file itself, or
    everything under a Content/ folder entry. Not every .uasset is a Blueprint; the editor half skips the rest."""
    out = {}
    for f in files:
        f = f.replace("\\", "/")
        if not f.startswith("Content/"):
            continue
        if f.endswith((".uasset", ".umap")):
            paths = [f]
        elif f.endswith("/"):
            paths = []
            for root, _, names in os.walk(os.path.join(repo, f)):
                for n in names:
                    if n.endswith((".uasset", ".umap")):
                        paths.append(os.path.relpath(os.path.join(root, n), repo).replace("\\", "/"))
        else:
            continue
        for p in sorted(paths):
            out["/Game/" + os.path.splitext(p[len("Content/"):])[0]] = p.endswith(".umap")
    return list(out.items())


def plan(ledger, L: dict, ident: str) -> dict:
    """What a dump for this review subtask would read and where it would write. Pure: no editor."""
    t = ledger.index(L)[1].get(ident)
    if not t:
        return {"error": f"unknown task {ident}"}
    if t.get("kind") != "review":
        return {"error": f"{ident} is not a review subtask"}
    covers = ledger.covered_ids(L, t)
    files = ledger.covered_files(L, covers)
    found = asset_candidates(str(ledger.REPO), files)
    if not found:
        return {"error": f"{ident}: the tasks it covers ({', '.join(covers) or 'none'}) hold no .uasset or .umap under "
                         f"Content/; no dump is needed"}
    if len(found) > MAX_ASSETS:
        return {"error": f"{ident}: {len(found)} assets under the covered files, more than {MAX_ASSETS}; narrow the "
                         f"covered tasks' files before dumping"}
    return {"error": None, "covers": covers, "assets": [a for a, is_map in found if not is_map],
            "maps": [a for a, is_map in found if is_map], "folder": ledger.dump_folder(ident),
            "index": ledger.dump_index_line(ident, covers)}


def dump_graph(T, g) -> str:
    """One graph as text, node by node: [node] type @x,y | inputs (pin<-node or pin=value) | outputs (pin->node)."""
    lines = []
    for i in T.get_node_infos(T.find_nodes(g, "")):
        ins = []
        for p in i.input_pins:
            if p.connected_pins:
                ins.append("%s<-%s" % (p.name, ",".join(c.node.get_name() for c in p.connected_pins)))
            elif p.value not in ("", "None") and p.name not in ("self", "execute"):
                ins.append("%s=%s" % (p.name, str(p.value)[:VALUE_CHARS]))
        outs = ["%s->%s" % (p.name, ",".join(c.node.get_name() for c in p.connected_pins))
                for p in i.output_pins if p.connected_pins]
        lines.append("[%s] %s @%d,%d | %s | %s" % (i.node.get_name(), i.type_id, i.position.x, i.position.y,
                                                  "; ".join(ins), "; ".join(outs)))
    return "\n".join(lines)


def safe_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)


def run(ident: str) -> None:
    """Inside the editor: dump every graph of every Blueprint the review covers. Prints REVIEWDUMP| lines."""
    import importlib
    import traceback
    import unreal
    from editor_toolset.toolsets.blueprint import BlueprintTools as T
    try:
        repo = os.path.abspath(unreal.SystemLibrary.get_project_directory())
        led_dir = os.path.join(repo, "Docs", "Ledger")
        if led_dir not in sys.path:
            sys.path.insert(0, led_dir)
        import ledger
        importlib.reload(ledger)
        p = plan(ledger, ledger.load(track=False), ident)
        if p["error"]:
            print("REVIEWDUMP| REFUSED " + p["error"])
            return
        out = os.path.join(repo, p["folder"])
        os.makedirs(out, exist_ok=True)
        index_path = os.path.join(out, "_index.txt")
        if os.path.exists(index_path):  # an old index must not stand for a run that fails part way
            os.remove(index_path)
        index, skipped, failed, graphs = [], [], [], 0
        for pkg in p["assets"]:
            bp = unreal.load_asset(pkg + "." + pkg.rsplit("/", 1)[-1])
            if bp is None:
                failed.append(pkg)
                continue
            if not isinstance(bp, unreal.Blueprint):
                skipped.append(pkg)
                continue
            for g in T.list_graphs(bp):
                text = dump_graph(T, g)
                name = "%s__%s.txt" % (safe_name(pkg.strip("/").replace("/", "__")), safe_name(g.get_name()))
                with open(os.path.join(out, name), "w", encoding="utf-8") as f:
                    f.write("%s | graph %s\n%s\n" % (pkg, g.get_name(), text))
                index.append("%s: %s (%d nodes) -> %s" % (pkg, g.get_name(), text.count("\n") + 1 if text else 0, name))
                graphs += 1
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("%s Read with find_nodes and get_node_infos.\n" % p["index"])
            f.write("\n".join(index) + "\n")
            if failed:
                f.write("Failed to load (%d) - NOT reviewed: %s\n" % (len(failed), ", ".join(failed)))
            if p["maps"]:
                f.write("%s (%d) - a level Blueprint cannot be read from Python without opening its map: %s\n"
                        % (ledger.DUMP_MAPS_LINE, len(p["maps"]), ", ".join(p["maps"])))
            if skipped:
                f.write("Not Blueprints, skipped (%d): %s\n" % (len(skipped), ", ".join(skipped)))
        print("REVIEWDUMP| dumped %d graphs from %d Blueprints to %s; %d failed to load, %d maps not dumped, %d "
              "non-Blueprint assets skipped. Next: ledger.py dispatch %s --by \"<hub>\" --dump %s"
              % (graphs, len(p["assets"]) - len(skipped) - len(failed), p["folder"], len(failed), len(p["maps"]),
                 len(skipped), ident, p["folder"]))
    except Exception:
        print("REVIEWDUMP| FAILED " + traceback.format_exc()[-1500:])


if __name__ == "__main__" and "unreal" not in sys.modules:
    import argparse
    ap = argparse.ArgumentParser(description="Plan (outside the editor) the graph dump for a review subtask")
    ap.add_argument("--plan", required=True, metavar="TASK")
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ledger
    p = plan(ledger, ledger.load(track=False), a.plan)
    if p["error"]:
        print(p["error"])
        sys.exit(1)
    print(f"{a.plan} covers {', '.join(p['covers'])}: {len(p['assets'])} asset(s) to read (the editor half dumps the "
          f"Blueprints among them) into {p['folder']}/")
    for x in p["assets"]:
        print("  " + x)
    for x in p["maps"]:
        print("  " + x + "  (a map: its level Blueprint is listed as not dumped)")
    print("Run it inside the editor with:\n  " + ledger.dump_command(a.plan))
