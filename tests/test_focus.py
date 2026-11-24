"""Non-invasive native MO2 mod focus uses exact visible name, not transient row."""
import unittest
from types import SimpleNamespace
from mo2_update_arsenal.focus import name_column, find_exact_indices, navigate


class Index:
 def __init__(self,model,row=-1,col=0):self.model=model;self.row=row;self.col=col
 def isValid(self):return self.row>=0
 def parent(self):return Index(self.model)
 def siblingAtColumn(self,col):return self if col==self.col else Index(self.model,self.row,col)


class MockModel:
 def __init__(self,rows):self.rows=rows
 def rowCount(self,parent=None):return len(self.rows) if not parent or not parent.isValid() else 0
 def columnCount(self):return 3
 def headerData(self,c,*_):return ['Conflicts','Mod Name','Priority'][c]
 def index(self,row,col,parent=None):return Index(self,row,col)
 def data(self,index,*_):return self.rows[index.row] if index.col==1 else ''


class Select:
 def __init__(self):self.index=None
 def select(self,index,flags):self.index=index
 def isSelected(self,index):return self.index is index


class View:
 def __init__(self,model):self._model=model;self.selector=Select();self.scrolled=None;self.selected=None
 def model(self):return self._model
 def selectionModel(self):return self.selector
 def expand(self,index):pass
 def setCurrentIndex(self,index):self.selected=index
 def currentIndex(self):return self.selected or Index(self._model)
 def scrollTo(self,index,*_):self.scrolled=index
 def setFocus(self,*_):pass


class Window:
 def __init__(self,view):self.view=view
 def objectName(self):return 'MainWindow'
 def findChild(self,kind,name):return self.view if name=='modList' else None
 def isMinimized(self):return False
 def raise_(self):pass
 def activateWindow(self):pass


class Organizer:
 def __init__(self,names):self.names=names
 def modList(self):return self
 def getMod(self,name):return name if name in self.names else None
 def displayName(self,name):return self.names.get(name,name)


class SelectionFlag:
 ClearAndSelect=1
 Rows=2


class QT:
 QModelIndex=lambda :Index(None)
 Qt=SimpleNamespace(ItemDataRole=SimpleNamespace(DisplayRole=0),Orientation=SimpleNamespace(Horizontal=1),FocusReason=SimpleNamespace(OtherFocusReason=2))
 QItemSelectionModel=SimpleNamespace(SelectionFlag=SelectionFlag)


def widgets(window):
 return SimpleNamespace(QApplication=SimpleNamespace(topLevelWidgets=lambda :[window]),QTreeView=View,QLineEdit=object,QAbstractItemView=SimpleNamespace(ScrollHint=SimpleNamespace(PositionAtCenter=7)))


class FocusTests(unittest.TestCase):
 def test_filtered_namesake_cannot_be_selected_instead_of_exact_native_priority(self):
  org=Organizer({'native':'Same label'});org.priority=lambda name: 104
  qt=SimpleNamespace(QModelIndex=QT.QModelIndex,QItemSelectionModel=QT.QItemSelectionModel,
    Qt=SimpleNamespace(**{**vars(QT.Qt),'ItemDataRole':SimpleNamespace(DisplayRole=0,UserRole=256)}))
  for priority,expected in [(52,False),(None,False),(104,True)]:
   model=MockModel(['Same label']);view=View(model);read=model.data
   model.data=lambda index,role:priority if role==260 else read(index,role)
   result=navigate(org,'native',qt,widgets(Window(view)))
   self.assertEqual(result['ok'],expected)
   self.assertEqual(view.selected is not None,expected)

 def test_find_exact_ignores_priority_and_filtered_row_number(self):
  model=MockModel(['Iron Armor','SkyUI','Unofficial Patch'])
  self.assertEqual(name_column(model,QT),1)
  indices=find_exact_indices(model,'SkyUI',1,QT)
  self.assertEqual([i.row for i in indices],[1])

 def test_exact_navigation_selects_and_scrolls(self):
  view=View(MockModel(['Unofficial Patch','SkyUI','SkyUI - Patch']))
  result=navigate(Organizer({'internal_skyui':'SkyUI'}),'internal_skyui',QT,widgets(Window(view)))
  self.assertTrue(result['ok'],result)
  self.assertEqual(view.selected.row,1)
  self.assertEqual(view.scrolled.row,1)
  self.assertEqual(view.selector.index.row,1)

 def test_duplicate_name_never_selects_arbitrarily(self):
  view=View(MockModel(['Same Name','Same Name']))
  result=navigate(Organizer({'a':'Same Name'}),'a',QT,widgets(Window(view)))
  self.assertFalse(result['ok']);self.assertEqual(result['matchedRows'],2)
  self.assertIsNone(view.selected)

 def test_rejects_model_reset_during_native_reveal(self):
  view=View(MockModel(['SkyUI','Other']))
  view.setFocus=lambda *_: view.setCurrentIndex(Index(view.model()))
  result=navigate(Organizer({'a':'SkyUI'}),'a',QT,widgets(Window(view)))
  self.assertFalse(result['ok'])

 def test_missing_mod_fails_instead_of_wrong_selection(self):
  view=View(MockModel(['SkyUI']))
  result=navigate(Organizer({'a':'SkyUI'}),'missing',QT,widgets(Window(view)))
  self.assertFalse(result['ok']);self.assertIsNone(view.selected)

if __name__=='__main__':unittest.main()
