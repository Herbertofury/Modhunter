"""Non-invasive left-pane MO2 navigation.

Only uses QTreeView's existing selection/scroll APIs. Never replaces an item
model, delegate, drag/drop mode, sort order or view event handler. Filter changes
are limited to the native text filter when needed to reveal an exact match.
"""
from __future__ import annotations


def find_exact_indices(model, label: str, name_column: int, qt, parent=None):
    """Search every model row, including collapsed/off-screen children (no cap)."""
    matches = []
    if parent is None:
        parent = qt.QModelIndex()
    try:
        count = model.rowCount(parent)
    except Exception:
        return matches
    for row in range(count):
        index = model.index(row, name_column, parent)
        if index.isValid():
            value = model.data(index, qt.Qt.ItemDataRole.DisplayRole)
            if str(value or "").strip() == label:
                matches.append(index)
        # Qt hierarchical list models may expose child rows via column 0 only.
        child_parent = model.index(row, 0, parent)
        if child_parent.isValid() and model.rowCount(child_parent):
            matches.extend(find_exact_indices(model, label, name_column, qt, child_parent))
    return matches


def name_column(model, qt):
    try:
        for c in range(model.columnCount()):
            title = str(model.headerData(c, qt.Qt.Orientation.Horizontal, qt.Qt.ItemDataRole.DisplayRole) or "").lower()
            if title in ("mod name", "name", "mod") or title.startswith("mod name"):
                return c
    except Exception:
        pass
    return 0


def find_mo2_view(qtwidgets):
    for window in qtwidgets.QApplication.topLevelWidgets():
        if window.objectName() != "MainWindow":
            continue
        view = window.findChild(qtwidgets.QTreeView, "modList")
        if view is not None:
            return window, view
    return None, None


def navigate(organizer, mod_name: str, qtcore, qtwidgets):
    if not organizer.modList().getMod(mod_name):
        return {"ok": False, "error": "The requested mod is no longer installed"}
    window, view = find_mo2_view(qtwidgets)
    if view is None or view.model() is None:
        return {"ok": False, "error": "MO2 left-pane list is unavailable; open the main MO2 window"}
    model = view.model()
    column = name_column(model, qtcore)
    display = str(organizer.modList().displayName(mod_name) or mod_name)
    matches = find_exact_indices(model, display, column, qtcore)
    cleared_filter = False
    if not matches:
        text_filter = window.findChild(qtwidgets.QLineEdit, "modFilterEdit")
        if text_filter is not None and text_filter.text():
            # A deliberate reveal request may clear MO2's text-only filter;
            # category filters and the user's sort/order remain intact.
            text_filter.clear()
            cleared_filter = True
            matches = find_exact_indices(model, display, column, qtcore)
    if len(matches) != 1:
        return {"ok": False, "error": "Mod is hidden by MO2's category filter or its display name is ambiguous. Clear the category filter or select this mod in MO2 manually." if matches == [] else "Several MO2 rows have this display name. Refusing to select the wrong mod.", "matchedRows": len(matches)}
    index = matches[0]
    ancestor = index.parent()
    while ancestor.isValid():
        view.expand(ancestor)
        ancestor = ancestor.parent()
    selection = view.selectionModel()
    if selection is None:
        return {"ok": False, "error": "MO2 selection model is unavailable"}
    flags = qtcore.QItemSelectionModel.SelectionFlag.ClearAndSelect | qtcore.QItemSelectionModel.SelectionFlag.Rows
    selection.select(index, flags)
    view.setCurrentIndex(index)
    view.scrollTo(index, qtwidgets.QAbstractItemView.ScrollHint.PositionAtCenter)
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
    view.setFocus(qtcore.Qt.FocusReason.OtherFocusReason)
    return {"ok": True, "selected": display, "clearedTextFilter": cleared_filter, "message": f"Revealed {display} in MO2's left pane"}
