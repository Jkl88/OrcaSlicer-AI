#include "PrintMonitor.hpp"

#include "AIConfig.hpp"
#include "GeminiClient.hpp"
#include "PresetAnalyzer.hpp"
#include "TelegramClient.hpp"
#include "WebcamCapture.hpp"

#include <boost/beast/core/detail/base64.hpp>
#include <boost/log/trivial.hpp>
#include <nlohmann/json.hpp>

#include <chrono>
#include <thread>

namespace Slic3r {
namespace GUI {
namespace AIAssist {

using json = nlohmann::json;

PrintMonitor &PrintMonitor::instance()
{
    static PrintMonitor inst;
    return inst;
}

PrintMonitor::~PrintMonitor()
{
    stop();
}

void PrintMonitor::reload_config(AppConfig *cfg)
{
    m_cfg = cfg;
}

void PrintMonitor::start(AppConfig *cfg)
{
    m_cfg = cfg;
    if (m_running.load())
        return;
    if (!get_bool(cfg, KEY_MONITOR_ENABLED, false)) {
        BOOST_LOG_TRIVIAL(info) << "AIAssist PrintMonitor not started (disabled)";
        return;
    }
    m_stop_flag = false;
    m_running = true;
    m_thread = std::thread([this] { thread_main(); });
    BOOST_LOG_TRIVIAL(info) << "AIAssist PrintMonitor started";
}

void PrintMonitor::stop()
{
    if (!m_running.load())
        return;
    m_stop_flag = true;
    if (m_thread.joinable())
        m_thread.join();
    m_running = false;
    BOOST_LOG_TRIVIAL(info) << "AIAssist PrintMonitor stopped";
}

void PrintMonitor::thread_main()
{
    using clock = std::chrono::steady_clock;
    auto next_vision = clock::now();

    while (!m_stop_flag.load()) {
        AppConfig *cfg = m_cfg;
        if (!cfg || !get_bool(cfg, KEY_MONITOR_ENABLED, false)) {
            std::this_thread::sleep_for(std::chrono::seconds(2));
            continue;
        }

        const int interval = monitor_interval_sec(cfg);
        TelegramClient tg(cfg);
        WebcamCapture cam(cfg);

        if (tg.configured()) {
            try {
                tg.poll_callbacks(2, m_tg_offset, [&]() {
                    std::string err;
                    if (cam.stop_print(err)) {
                        tg.send_message("Печать остановлена по кнопке из Telegram.");
                    } else {
                        tg.send_message(std::string("Не удалось остановить: ") + err);
                    }
                });
            } catch (const std::exception &ex) {
                BOOST_LOG_TRIVIAL(warning) << "AIAssist TG poll: " << ex.what();
            }
        }

        if (clock::now() < next_vision) {
            std::this_thread::sleep_for(std::chrono::milliseconds(400));
            continue;
        }
        next_vision = clock::now() + std::chrono::seconds(interval);

        Snapshot snap = cam.grab();
        if (!snap.ok) {
            BOOST_LOG_TRIVIAL(debug) << "AIAssist snapshot skip: " << snap.error;
            continue;
        }

        std::string b64;
        b64.resize(boost::beast::detail::base64::encoded_size(snap.bytes.size()));
        b64.resize(boost::beast::detail::base64::encode(b64.data(), snap.bytes.data(), snap.bytes.size()));

        GeminiImagePart img;
        img.mime_type = snap.mime_type;
        img.base64_data = std::move(b64);

        GeminiClient gemini(cfg);
        auto gr = gemini.generate_with_images(
            PresetAnalyzer::system_prompt_vision(),
            "Analyze this printer webcam frame. Is the print failing?",
            {img});

        if (!gr.ok) {
            BOOST_LOG_TRIVIAL(warning) << "AIAssist vision: " << gr.error;
            continue;
        }

        bool anomaly = false;
        bool recommend_stop = false;
        std::string description = gr.text;
        try {
            // Иногда модель оборачивает JSON в fences
            std::string cleaned = gr.text;
            auto fence = cleaned.find("```");
            if (fence != std::string::npos) {
                auto start = cleaned.find('{');
                auto end = cleaned.rfind('}');
                if (start != std::string::npos && end != std::string::npos && end > start)
                    cleaned = cleaned.substr(start, end - start + 1);
            }
            auto j = json::parse(cleaned);
            anomaly = j.value("anomaly", false);
            recommend_stop = j.value("recommend_stop", false);
            description = j.value("description", gr.text);
            const std::string level = j.value("severity_level", "none");
            if (level == "low" && !recommend_stop)
                anomaly = false;
        } catch (...) {
            continue;
        }

        if (!anomaly)
            continue;

        const int64_t now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            clock::now().time_since_epoch()).count();
        if (now_ms - m_last_alert_ms.load() < 180000)
            continue;
        m_last_alert_ms = now_ms;

        if (tg.configured()) {
            std::string caption = "Orca AI: возможная проблема печати\n" + description;
            if (recommend_stop)
                caption += "\nРекомендуется остановить. Кнопка ниже.";
            auto tr = tg.send_photo_with_stop(caption, snap.bytes);
            if (!tr.ok)
                BOOST_LOG_TRIVIAL(error) << "AIAssist TG alert failed: " << tr.error;
        } else {
            BOOST_LOG_TRIVIAL(warning) << "AIAssist anomaly but Telegram not configured: " << description;
        }
    }
}

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r
