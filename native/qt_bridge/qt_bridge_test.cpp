#include <QtCore/QCoreApplication>
#include <QtCore/QObject>
#include <QtCore/QThread>
#include <QtCore/QVariant>
#include <cstdint>
#include <cstring>
#include <type_traits>

extern "C" std::uint32_t modhunter_qt_bridge_abi() noexcept;
extern "C" const char* modhunter_qt_build_version() noexcept;
extern "C" const char* modhunter_qt_runtime_version() noexcept;
extern "C" void* modhunter_qt_callback_sender(void*) noexcept;

class NativeSource final : public QObject {
    Q_OBJECT
public:
    void fire() { emit payload(QStringLiteral("fixture"), 7, QVariant("owned-ticket"), QVariantMap{}); }
signals:
    void payload(QString, int, QVariant, QVariant);
};
class NativeBridge final : public QObject {
    Q_OBJECT
public slots:
    void relay(QString game, int mod, QVariant ticket, QVariant body) { emit forwarded(game, mod, ticket, body); }
signals:
    void forwarded(QString, int, QVariant, QVariant);
};
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    NativeSource source;
    NativeBridge bridge;
    bool captured = false;
    QObject::connect(&source, &NativeSource::payload, &bridge, &NativeBridge::relay);
    QObject::connect(&bridge, &NativeBridge::forwarded, &app, [&](QString, int, QVariant, QVariant) {
        captured = modhunter_qt_callback_sender(&bridge) == &source;
    });
    if (modhunter_qt_bridge_abi() != 1 || !modhunter_qt_build_version()
        || std::strcmp(modhunter_qt_runtime_version(), qVersion()) != 0
        || modhunter_qt_callback_sender(nullptr) || modhunter_qt_callback_sender(&bridge)) return 1;
    source.fire();
    if (!captured || modhunter_qt_callback_sender(&bridge)) return 2;
    QObject foreign;
    QThread worker;
    foreign.moveToThread(&worker);
    if (modhunter_qt_callback_sender(&foreign)) return 3;
    return 0;
}
#include "qt_bridge_test.moc"
