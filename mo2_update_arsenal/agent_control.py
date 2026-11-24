"""Native host-side authorization and compare-and-set for one MO2 mod."""
import json


def set_enabled(plugin, payload, active_bit):
    required = {"profileId", "revision", "itemId", "name", "expectedEnabled", "enabled"}
    if set(payload) != required or any(type(payload[key]) is not bool for key in ("expectedEnabled", "enabled")):
        raise ValueError("An exact mod, revision, expected state and desired state are required")
    if any(not isinstance(payload[key], str) or not payload[key] for key in ("profileId", "revision", "itemId", "name")):
        raise ValueError("Invalid mod identity")
    scope = json.loads(str(plugin.org.pluginSetting(plugin.name(), "agent_mod_control_scope") or "{}"))
    names = scope.get(payload["profileId"]) if isinstance(scope, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names) or payload["name"] not in names:
        raise ValueError("This profile and mod have no host authorization for agent state changes")
    server = plugin.server
    def check_revision():
        with server._guard:
            refreshing = (plugin._background_dirty or plugin._pending_refresh is not None
                          or plugin._background_refresh is not None and plugin._background_refresh.busy)
            if (refreshing or server._stopped or server._snapshot.get("profileId") != payload["profileId"]
                    or str(plugin.org.profileName()) != server._snapshot.get("profileName")
                    or server.agent_revision() != payload["revision"]):
                raise ValueError("MO2 inventory changed or is refreshing. Read a fresh inventory page")
            row = server._items_by_id.get(payload["itemId"])
            if row is None or row.get("mo2InternalName") != payload["name"]:
                raise ValueError("The exact mod no longer belongs to this inventory")
    check_revision()
    if server.content._lease:
        raise ValueError("A native content operation is active")
    context = plugin._content_native.capture({"itemId":payload["itemId"]})
    plugin._content_native.check(context)
    if context["modName"] != payload["name"]:
        raise ValueError("Native mod identity changed")
    check_revision()
    modlist = plugin.org.modList()
    name = payload["name"]
    before = bool(int(modlist.state(name)) & active_bit)
    if before != payload["expectedEnabled"]:
        raise ValueError("The native enabled state changed before the operation")
    wanted = payload["enabled"]
    if before != wanted and not modlist.setActive(name, wanted):
        raise ValueError("MO2 did not accept the state change. Inspect the current state")
    after = bool(int(modlist.state(name)) & active_bit)
    if after != wanted:
        raise ValueError("MO2 did not confirm the requested state. Inspect the current state")
    if before != wanted:
        plugin._schedule_refresh()
    return {"ok":True, "profileId":payload["profileId"], "itemId":payload["itemId"],
            "name":name, "previousEnabled":before, "enabled":after, "changed":before != after}
