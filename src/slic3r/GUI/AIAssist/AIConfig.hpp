#ifndef slic3r_AIAssist_AIConfig_hpp_
#define slic3r_AIAssist_AIConfig_hpp_

#include <algorithm>
#include <string>

#include "libslic3r/AppConfig.hpp"

namespace Slic3r {
namespace GUI {
namespace AIAssist {

// Секция в AppConfig (OrcaSlicer.conf). Секреты только локально, не в пресетах.
inline constexpr const char *SECTION = "ai_assist";

inline constexpr const char *KEY_GEMINI_API_KEY      = "gemini_api_key";
inline constexpr const char *KEY_GEMINI_MODEL        = "gemini_model";
inline constexpr const char *KEY_GEMINI_BASE_URL     = "gemini_base_url";
inline constexpr const char *KEY_TG_BOT_TOKEN        = "telegram_bot_token";
inline constexpr const char *KEY_TG_CHAT_ID          = "telegram_chat_id";
inline constexpr const char *KEY_TG_API_BASE         = "telegram_api_base";
inline constexpr const char *KEY_WEBCAM_URL          = "webcam_url";
inline constexpr const char *KEY_MOONRAKER_URL       = "moonraker_url";
inline constexpr const char *KEY_MOONRAKER_API_KEY   = "moonraker_api_key";
inline constexpr const char *KEY_MONITOR_ENABLED     = "monitor_enabled";
inline constexpr const char *KEY_MONITOR_INTERVAL_S  = "monitor_interval_sec";
inline constexpr const char *KEY_WEBCAM_SOURCE       = "webcam_source"; // direct | moonraker

inline constexpr const char *DEFAULT_GEMINI_MODEL    = "gemini-2.0-flash";
inline constexpr const char *DEFAULT_GEMINI_BASE     = "https://generativelanguage.googleapis.com";
inline constexpr const char *DEFAULT_TG_API_BASE     = "https://api.telegram.org";
inline constexpr const char *DEFAULT_MONITOR_INTERVAL = "45";
inline constexpr const char *DEFAULT_WEBCAM_SOURCE   = "moonraker";

inline std::string get(AppConfig *cfg, const char *key, const std::string &fallback = {})
{
    if (!cfg)
        return fallback;
    std::string v = cfg->get(SECTION, key);
    return v.empty() ? fallback : v;
}

inline void set(AppConfig *cfg, const char *key, const std::string &value)
{
    if (!cfg)
        return;
    cfg->set(SECTION, key, value);
}

inline bool get_bool(AppConfig *cfg, const char *key, bool fallback = false)
{
    if (!cfg)
        return fallback;
    const std::string v = cfg->get(SECTION, key);
    if (v.empty())
        return fallback;
    return v == "true" || v == "1";
}

inline void set_bool(AppConfig *cfg, const char *key, bool value)
{
    set(cfg, key, value ? "true" : "false");
}

// Базовый URL Telegram Bot API без хвостового слэша.
// Можно подставить свой прокси (proxyTelega / Cloudflare Worker и т.п.).
inline std::string telegram_api_base(AppConfig *cfg)
{
    std::string base = get(cfg, KEY_TG_API_BASE, DEFAULT_TG_API_BASE);
    while (!base.empty() && base.back() == '/')
        base.pop_back();
    if (base.empty())
        base = DEFAULT_TG_API_BASE;
    return base;
}

inline std::string gemini_base_url(AppConfig *cfg)
{
    std::string base = get(cfg, KEY_GEMINI_BASE_URL, DEFAULT_GEMINI_BASE);
    while (!base.empty() && base.back() == '/')
        base.pop_back();
    if (base.empty())
        base = DEFAULT_GEMINI_BASE;
    return base;
}

inline int monitor_interval_sec(AppConfig *cfg)
{
    try {
        int v = std::stoi(get(cfg, KEY_MONITOR_INTERVAL_S, DEFAULT_MONITOR_INTERVAL));
        return std::max(15, std::min(v, 600));
    } catch (...) {
        return 45;
    }
}

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r

#endif
