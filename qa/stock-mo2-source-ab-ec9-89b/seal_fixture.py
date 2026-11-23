"""Before/after immutable synthetic input witness, outside timing."""
import argparse
import hashlib
from pathlib import Path
import re
from mo2_smoke_plugin.modhunter_native_smoke.fixture import PROFILE, own_path, plan, require, write_new

def read(path,limit):
    item=own_path(path,directory=False)
    require(item.stat().st_size <= limit,"Oversize owned fixture input")
    return item.read_bytes()

def seal(root,rows=11207):
    own_path(root,directory=True)
    mods=own_path(root/'mods',directory=True)
    profile=own_path(own_path(root/'profiles',directory=True)/PROFILE,directory=True)
    expected=plan(rows)
    require(set(d.name for d in mods.iterdir())==expected.keys(),"Fixture inventory differs")
    h=hashlib.sha256()
    # Only comment/newline reformatting by native MO2 is ignored; compare all
    # ordered actual activation rows and native enabled/load-order state.
    for n in ("modlist.txt","plugins.txt","loadorder.txt","archives.txt","lockedorder.txt"):
        b=read(profile/n,1024*1024)
        items=[line for line in b.decode("utf-8-sig").splitlines() if line and not line.startswith("#")]
        want=[("+" if active else "-")+name for name,active in reversed(expected.items())] if n=="modlist.txt" else []
        require(items==want,"Generated profile order/activation/archives changed")
        h.update(n.encode()+b"\0"+"\n".join(items).encode()+b"\0")
    for i,n in enumerate(expected,1):
        parent=own_path(mods/n,directory=True)
        want=("meta.ini",f"perf-fixture-{i:05d}.txt")
        require(set(p.name for p in parent.iterdir())==set(want),"Unexpected data inside a seeded mod")
        for leaf in want:
            b=read(parent/leaf,4096)
            h.update(n.encode()+b"\0"+leaf.encode()+b"\0"+hashlib.sha256(b).digest())
    return h.hexdigest()

def run(action,root,rows=11207):
    digest=seal(root,rows)
    doc=root/"stock-ab-fixture.sha256"
    if action=="create":
        write_new(doc,digest+"\n")
    elif action=="verify":
        require(read(doc,70).decode()==digest+"\n","Seeded metadata/payload inputs changed")
    else:raise ValueError("Unknown seal action")
    return digest

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("create","verify"))
    p.add_argument("root",type=Path)
    a=p.parse_args()
    print(run(a.action,a.root))
