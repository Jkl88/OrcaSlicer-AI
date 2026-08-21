#ifndef slic3r_AIAssist_PrintMonitor_hpp_
#define slic3r_AIAssist_PrintMonitor_hpp_

#include <atomic>
#include <memory>
#include <thread>

#include "libslic3r/AppConfig.hpp"

namespace Slic3r {
namespace GUI {
namespace AIAssist {

// Фоновый цикл: snapshot → Gemini Vision → Telegram (фото + Stop) → poll callback.
class PrintMonitor
{
public:
    static PrintMonitor &instance();

    void start(AppConfig *cfg);
    void stop();
    bool running() const { return m_running.load(); }
    void reload_config(AppConfig *cfg);

private:
    PrintMonitor() = default;
    ~PrintMonitor();
    PrintMonitor(const PrintMonitor &) = delete;
    PrintMonitor &operator=(const PrintMonitor &) = delete;

    void thread_main();

    AppConfig *             m_cfg{nullptr};
    std::atomic<bool>       m_running{false};
    std::atomic<bool>       m_stop_flag{false};
    std::thread             m_thread;
    long                    m_tg_offset{0};
    std::atomic<int64_t>    m_last_alert_ms{0};
};

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r

#endif
