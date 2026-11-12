# MO2 Qt sender bridge

This small Qt Core helper closes one specific PyQt binding gap. MO2 creates its
repository bridge in C++, so PyQt refuses a direct call to the protected
`QObject::sender()` member. The helper invokes that ordinary Qt member through
a legal pointer-to-member expression; it does not downcast the object to a
fabricated subclass or inspect QObject's private memory layout.

The production Python gateway calls it only while handling an owned metadata or
failure callback on MO2's GUI thread. It verifies the resulting `NexusInterface`
class and raw `nxmFilesAvailable(QString,int,QVariant,QVariant,int)` signal before
connecting. Exact ticket, game, mod, operation and request lifetime checks remain
in the gateway. The helper does not read credentials or issue network requests.

MO2 2.5.2's `filesAvailable(QList<ModRepositoryFileInfo*>)` path is deliberately
unused. Apart from lacking a PyQt converter, that version's implementation builds
the list from addresses of loop-local records. The original JSON signal avoids
both problems.

## Build

Use the Qt SDK matching the MO2 runtime's major and minor version. Release CI
uses the official Qt 6.7.1 MSVC desktop SDK for MO2 2.5.2.

```sh
cmake -S native/qt_bridge -B native/target/qt-bridge -DCMAKE_PREFIX_PATH=/path/to/Qt/6.7.1/msvc2019_64
cmake --build native/target/qt-bridge --config Release
ctest --test-dir native/target/qt-bridge -C Release --output-on-failure
```

Ship only `modhunter-qt-bridge.dll` in `mo2_update_arsenal/bin`. MO2 supplies the Qt
runtime. The test executable and `modhunter-qt-bridge-fixture` library are test
artifacts and must not enter the portable package or installer.

The C ABI is version 1. Python checks the ABI, the helper's build/runtime Qt
versions, the active PyQt runtime, pointer width, live QObject and GUI thread.
A missing or mismatched helper leaves metadata available and returns an
installation/fallback-key explanation for file requests.

## Evidence scope

CTest exercises C++-created source and receiver objects, nested signal delivery,
null/outside-callback results, and a receiver assigned to another thread.
`tests/test_native_qt_bridge.py` additionally exercises the production gateway
using a C++-created QObject wrapped by PyQt. Set
`MODHUNTER_QT_BRIDGE_TEST_DIR` to the directory containing the helper and fixture
libraries and use a matching PyQt6 runtime to run its compiled cases.

```sh
python -m unittest tests.test_native_qt_bridge -v
```

The Windows native MO2 harness separately forwards synthetic QVariant signals
through the actual MO2-created repository QObject. These are native binding and
request-lifetime tests with synthetic data; they do not establish authenticated
Nexus API responses or real account download permissions.

Primary implementation references:

- [Qt QObject::sender documentation](https://doc.qt.io/qt-6/qobject.html#sender)
- [MO2 2.5.2 Nexus bridge implementation](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/nexusinterface.cpp)
- [MO2 Python repository bridge binding](https://github.com/ModOrganizer2/modorganizer-plugin_python/blob/master/src/mobase/wrappers/basic_classes.cpp)
