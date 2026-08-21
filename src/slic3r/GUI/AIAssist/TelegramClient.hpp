#ifndef slic3r_AIAssist_TelegramClient_hpp_
#define slic3r_AIAssist_TelegramClient_hpp_

#include <functional>
#include <string>
#include <vector>

#include "libslic3r/AppConfig.hpp"

namespace Slic3r {
namespace GUI {
namespace AIAssist {

struct TelegramResult
{
    bool        ok{false};
    std::string error;
    std::string raw;
    unsigned    http_status{0};
};

// Bot API с настраиваемым базовым хостом (не только api.telegram.org).
class TelegramClient
{
public:
    explicit TelegramClient(AppConfig *cfg);

    bool configured() const;

    TelegramResult send_message(const std::string &text) const;
    // Фото + подпись + inline-кнопка «Остановить печать» (callback_data=orca_ai_stop).
    TelegramResult send_photo_with_stop(const std::string &caption,
                                        const std::vector<unsigned char> &jpeg_bytes,
                                        const std::string &filename = "snapshot.jpg") const;

    // Long poll getUpdates; вызывается из фонового монитора.
    // on_stop — пользователь нажал кнопку остановки.
    void poll_callbacks(int timeout_sec, long &offset, const std::function<void()> &on_stop) const;

    static constexpr const char *STOP_CALLBACK = "orca_ai_stop";

private:
    AppConfig *m_cfg;
    std::string method_url(const std::string &method) const;
    TelegramResult post_json(const std::string &method, const std::string &json_body) const;
};

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r

#endif
