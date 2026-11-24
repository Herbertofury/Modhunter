"""Exercise a single host-authorized fixture mod through the real stdio MCP client."""
import json
import os
import subprocess
import time


def check_scoped_toggle(cli, endpoint, profile_id, by_name):
    allowed = "Smoke Enabled"
    forbidden = "Smoke Disabled"
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}}
    def call(name, arguments, *, permitted=allowed):
        frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params":{"_meta":meta, "name":name, "arguments":arguments}}
        rsp = subprocess.run([os.fspath(cli),"mcp-stdio","--mo2",profile_id,endpoint,
                              "--allow-mod",profile_id,permitted], input=json.dumps(frame)+"\n",
                             capture_output=True, text=True, encoding="utf-8", timeout=10,
                             creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0), check=True)
        return json.loads(rsp.stdout)["result"]

    def view():
        return call("mo2_mods",{})["structuredContent"]

    def mutation(who,rev,original,wanted):
        return {"name":who, "itemId":by_name[who]["id"], "revision":rev,
                "expectedEnabled":original,"enabled":wanted}

    # A launcher scope that the host did not grant must fail before MO2 can mutate anything.
    revision = view()["revision"]
    denied = call("mo2_set_enabled",mutation(forbidden,revision,False,True),permitted=forbidden)
    if not denied.get("isError") or "host authorization" not in denied["content"][0]["text"]:
        raise AssertionError("MO2 did not reject the out-of-scope fixture target")
    for before, wanted in ((True, False), (False, True)):
        deadline = time.monotonic()+12
        while True:
            page=view()
            seen=next(row for row in page["items"] if row["mo2InternalName"]==allowed)
            if seen["enabled"] is before:
                args=mutation(allowed,page["revision"],before,wanted)
                response=call("mo2_set_enabled",args)
                if not response.get("isError"):
                    actual=response["structuredContent"]
                    if actual["previousEnabled"] is not before or actual["enabled"] is not wanted or not actual["changed"]:
                        raise AssertionError("Native compare-and-set returned the wrong transition")
                    break
                # Retry only definite pre-dispatch stale-inventory refusals, never an ambiguous mutation.
                reason = response["content"][0]["text"]
                if not any(message in reason for message in ("inventory changed or is refreshing", "MO2 profile is refreshing; review the operation again")):
                    raise AssertionError(response["content"])
            if time.monotonic() >= deadline: raise AssertionError("Native fixture inventory did not converge")
            time.sleep(0.1)
    deadline=time.monotonic()+12
    while True:
        page=view()
        states={item["mo2InternalName"]:item["enabled"] for item in page["items"]}
        if states == {name:item["enabled"] for name,item in by_name.items()}:
            return {"nativeToggle":True,"restoredOriginalStates":True,"hostRejectedUnapprovedMod":True}
        if time.monotonic() >= deadline: raise AssertionError("Native fixture state did not return to baseline")
        time.sleep(0.1)
