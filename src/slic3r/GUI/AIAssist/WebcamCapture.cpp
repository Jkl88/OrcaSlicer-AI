#include "WebcamCapture.hpp"

#include "AIConfig.hpp"
#include "slic3r/Utils/Http.hpp"

#include <boost/algorithm/string.hpp>
#include <boost/log/trivial.hpp>
#include <nlohmann/json.hpp>

namespace Slic3r {
namespace GUI {
namespace AIAssist {

using json = nlohmann::json;

WebcamCapture::WebcamCapture(AppConfig *cfg) : m_cfg(cfg) {}

std::string WebcamCapture::moonraker_base() const
{
    std::string host = get(m_cfg, KEY_MOONRAKER_URL);
    if (host.empty())
        return {};
    boost::trim(host);
    while (!host.empty() && host.back() == '/')
        host.pop_back();
    if (host.find("http://") != 0 && host.find("https://") != 0)
        host = "http://" + host;
    return host;
}

std::string WebcamCapture::resolve_snapshot_url() const
{
    const std::string source = get(m_cfg, KEY_WEBCAM_SOURCE, DEFAULT_WEBCAM_SOURCE);
    const std::string direct = get(m_cfg, KEY_WEBCAM_URL);

    if (source == "direct" || (!direct.empty() && source != "moonraker")) {
        if (!direct.empty())
            return direct;
    }

    // Moonraker: типичный snapshot у crowsnest/mjpg-streamer
    // 1) Явный URL камеры в настройках имеет приоритет даже в режиме moonraker
    if (!direct.empty())
        return direct;

    const std::string base = moonraker_base();
    if (base.empty())
        return {};

    // Стандартный путь webcam-а на многих Klipper-образах
    return base + "/webcam/?action=snapshot";
}

Snapshot WebcamCapture::http_get_bytes(const std::string &url, const std::string &api_key) const
{
    Snapshot out;
    if (url.empty()) {
        out.error = "Webcam URL is empty";
        return out;
    }

    bool done = false;
    auto http = Http::get(url);
    if (!api_key.empty())
        http.header("X-Api-Key", api_key);
    http.timeout_max(20)
        .size_limit(12 * 1024 * 1024)
        .on_complete([&](std::string body, unsigned status) {
            out.bytes.assign(body.begin(), body.end());
            // Эвристика mime
            if (out.bytes.size() >= 3 && out.bytes[0] == 0xFF && out.bytes[1] == 0xD8)
                out.mime_type = "image/jpeg";
            else if (out.bytes.size() >= 8 && out.bytes[0] == 0x89 && out.bytes[1] == 0x50)
                out.mime_type = "image/png";
            out.ok = !out.bytes.empty() && status >= 200 && status < 300;
            if (!out.ok)
                out.error = "Bad webcam HTTP " + std::to_string(status);
            done = true;
        })
        .on_error([&](std::string, std::string error, unsigned status) {
            out.error = error.empty() ? ("HTTP " + std::to_string(status)) : error;
            done = true;
        })
        .perform_sync();

    if (!done && out.error.empty())
        out.error = "Webcam request incomplete";
    return out;
}

Snapshot WebcamCapture::grab() const
{
    const std::string url = resolve_snapshot_url();
    if (url.empty()) {
        Snapshot out;
        out.error = "Configure webcam URL and/or Moonraker host (Preferences → AI Assist)";
        return out;
    }
    const std::string api_key = get(m_cfg, KEY_MOONRAKER_API_KEY);
    BOOST_LOG_TRIVIAL(debug) << "AIAssist webcam snapshot: " << url;
    return http_get_bytes(url, api_key);
}

bool WebcamCapture::stop_print(std::string &error) const
{
    const std::string base = moonraker_base();
    if (base.empty()) {
        error = "Moonraker URL is not set — cannot stop print";
        return false;
    }
    const std::string api_key = get(m_cfg, KEY_MOONRAKER_API_KEY);

    // Сначала pause, затем cancel — мягче, чем emergency_stop
    auto post_empty = [&](const std::string &path) -> bool {
        bool ok = false;
        std::string err;
        auto http = Http::post(base + path);
        if (!api_key.empty())
            http.header("X-Api-Key", api_key);
        http.header("Content-Type", "application/json")
            .timeout_max(15)
            .set_post_body(std::string("{}"))
            .on_complete([&](std::string, unsigned status) {
                ok = status >= 200 && status < 300;
                if (!ok)
                    err = "HTTP " + std::to_string(status);
            })
            .on_error([&](std::string, std::string e, unsigned status) {
                err = e.empty() ? ("HTTP " + std::to_string(status)) : e;
            })
            .perform_sync();
        if (!ok)
            error = err;
        return ok;
    };

    if (!post_empty("/printer/print/pause")) {
        // Даже если pause не удался (уже paused) — пробуем cancel
        BOOST_LOG_TRIVIAL(warning) << "AIAssist pause failed: " << error;
    }
    if (!post_empty("/printer/print/cancel")) {
        return false;
    }
    error.clear();
    return true;
}

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r
