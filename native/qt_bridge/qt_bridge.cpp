// A narrow Qt binding gap, not a general native-call surface. Python supplies
// only a live QObject obtained from mobase/SIP, on MO2's GUI thread.
#include <QtCore/QCoreApplication>
#include <QtCore/QObject>
#include <QtCore/QThread>
#include <QtCore/QtGlobal>
#include <cstdint>

#if defined(_WIN32)
#define MHQT_EXPORT extern "C" __declspec(dllexport)
#else
#define MHQT_EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {
class SenderAccess : public QObject {
public:
    // Protected access is checked at this member-pointer expression. Because
    // sender is declared by QObject, the pointer's member class remains QObject.
    // Calling it on the actual QObject requires no fabricated derived object,
    // reinterpret cast, private layout assumption, or invalid downcast.
    static constexpr auto sender_member() noexcept { return &SenderAccess::sender; }
};
}

MHQT_EXPORT std::uint32_t modhunter_qt_bridge_abi() noexcept { return 1; }
MHQT_EXPORT const char* modhunter_qt_build_version() noexcept { return QT_VERSION_STR; }
MHQT_EXPORT const char* modhunter_qt_runtime_version() noexcept { return qVersion(); }
MHQT_EXPORT void* modhunter_qt_callback_sender(void* receiver_address) noexcept {
    if (!receiver_address) return nullptr;
    auto* receiver = static_cast<QObject*>(receiver_address);
    auto* app = QCoreApplication::instance();
    auto* thread = QThread::currentThread();
    if (!app || app->thread() != thread || receiver->thread() != thread) return nullptr;
    QObject* sender = (receiver->*SenderAccess::sender_member())();
    if (!sender || sender->thread() != thread) return nullptr;
    return sender;  // Borrowed; caller connects while this signal stack is live.
}
