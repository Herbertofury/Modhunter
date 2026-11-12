// Test-only C++-created QObject. Never packaged with the production plugin.
#include <QtCore/QObject>
#include <QtCore/QVariant>
#if defined(_WIN32)
#define FIXTURE_EXPORT extern "C" __declspec(dllexport)
#else
#define FIXTURE_EXPORT extern "C" __attribute__((visibility("default")))
#endif
class FixtureRepositoryBridge final : public QObject {
    Q_OBJECT
signals:
    void descriptionAvailable(QString, int, QVariant, QVariant);
    void requestFailed(QString, int, int, QVariant, int, QString);
};
FIXTURE_EXPORT void* modhunter_qt_fixture_create() { return new FixtureRepositoryBridge(); }
FIXTURE_EXPORT void modhunter_qt_fixture_destroy(void* object) { delete static_cast<FixtureRepositoryBridge*>(object); }
#include "qt_bridge_fixture.moc"
