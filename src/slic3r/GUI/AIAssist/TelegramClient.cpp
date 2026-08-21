#include "TelegramClient.hpp"

#include "AIConfig.hpp"
#include "slic3r/Utils/Http.hpp"

#include <boost/filesystem.hpp>
#include <boost/filesystem/fstream.hpp>
#include <boost/log/trivial.hpp>
#include <nlohmann/json.hpp>

#include <chrono>
#include <fstream>

namespace fs = boost::filesystem;
namespace Slic3r {
namespace GUI {
namespace AIAssist {

using json = nlohmann::json;

TelegramClient::TelegramClient(AppConfig *cfg) : m_cfg(cfg) {}

bool TelegramClient::configured() const
{
    return !get(m_cfg, KEY_TG_BOT_TOKEN).empty() && !get(m_cfg, KEY_TG_CHAT_ID).empty();
}

std::string TelegramClient::method_url(const std::string &method) const
{
    const std::string token = get(m_cfg, KEY_TG_BOT_TOKEN);
    return telegram_api_base(m_cfg) + "/bot" + token + "/" + method;
}

TelegramResult TelegramClient::post_json(const std::string &method, const std::string &json_body) const
{
    TelegramResult out;
    if (!configured()) {
        out.error = "Telegram bot token / chat id not configured";
        return out;
    }

    bool done = false;
    auto http = Http::post(method_url(method));
    http.header("Content-Type", "application/json")
        .timeout_max(30)
        .set_post_body(json_body)
        .tls_verify(true)
        .on_complete([&](std::string body, unsigned status) {
            out.http_status = status;
            out.raw = body;
            try {
                auto j = json::parse(body);
                out.ok = j.value("ok", false);
                if (!out.ok)
                    out.error = j.value("description", body);
            } catch (const std::exception &ex) {
                out.error = ex.what();
            }
            done = true;
        })
        .on_error([&](std::string body, std::string error, unsigned status) {
            out.http_status = status;
            out.error = error.empty() ? body : error;
            done = true;
        })
        .perform_sync();

    if (!done && out.error.empty())
        out.error = "Telegram request incomplete";
    return out;
}

TelegramResult TelegramClient::send_message(const std::string &text) const
{
    json body = {
        {"chat_id", get(m_cfg, KEY_TG_CHAT_ID)},
        {"text", text},
        {"disable_web_page_preview", true}
    };
    return post_json("sendMessage", body.dump());
}

TelegramResult TelegramClient::send_photo_with_stop(const std::string &caption,
                                                   const std::vector<unsigned char> &jpeg_bytes,
                                                   const std::string &filename) const
{
    TelegramResult out;
    if (!configured()) {
        out.error = "Telegram bot token / chat id not configured";
        return out;
    }
    if (jpeg_bytes.empty()) {
        out.error = "Empty photo";
        return out;
    }

    // multipart: пишем во временный файл — Http::form_add_file работает с путём
    const fs::path tmp = fs::temp_directory_path() / ("orca_ai_tg_" + std::to_string(
        std::chrono::steady_clock::now().time_since_epoch().count()) + ".jpg");
    {
        boost::filesystem::ofstream ofs(tmp, std::ios::binary);
        if (!ofs) {
            out.error = "Cannot write temp photo";
            return out;
        }
        ofs.write(reinterpret_cast<const char *>(jpeg_bytes.data()),
                  static_cast<std::streamsize>(jpeg_bytes.size()));
    }

    json reply_markup = {
        {"inline_keyboard", json::array({
            json::array({
                json{{"text", "⏹ Stop print"}, {"callback_data", STOP_CALLBACK}}
            })
        })}
    };

    bool done = false;
    auto http = Http::post(method_url("sendPhoto"));
    http.timeout_max(60)
        .size_limit(8 * 1024 * 1024)
        .form_add("chat_id", get(m_cfg, KEY_TG_CHAT_ID))
        .form_add("caption", caption.substr(0, 1024))
        .form_add("reply_markup", reply_markup.dump())
        .form_add_file("photo", tmp, filename)
        .tls_verify(true)
        .on_complete([&](std::string body, unsigned status) {
            out.http_status = status;
            out.raw = body;
            try {
                auto j = json::parse(body);
                out.ok = j.value("ok", false);
                if (!out.ok)
                    out.error = j.value("description", body);
            } catch (const std::exception &ex) {
                out.error = ex.what();
            }
            done = true;
        })
        .on_error([&](std::string body, std::string error, unsigned status) {
            out.http_status = status;
            out.error = error.empty() ? body : error;
            done = true;
        })
        .perform_sync();

    boost::system::error_code ec;
    fs::remove(tmp, ec);

    if (!done && out.error.empty())
        out.error = "Telegram sendPhoto incomplete";
    return out;
}

void TelegramClient::poll_callbacks(int timeout_sec, long &offset, const std::function<void()> &on_stop) const
{
    if (!configured())
        return;

    std::string url = method_url("getUpdates") + "?timeout=" + std::to_string(std::max(1, timeout_sec))
                    + "&allowed_updates=" + Http::url_encode(R"(["callback_query"])");
    if (offset > 0)
        url += "&offset=" + std::to_string(offset);

    auto http = Http::get(url);
    http.timeout_max(timeout_sec + 15)
        .tls_verify(true)
        .on_complete([&](std::string body, unsigned) {
            try {
                auto j = json::parse(body);
                if (!j.value("ok", false) || !j.contains("result"))
                    return;
                for (const auto &upd : j["result"]) {
                    const long update_id = upd.value("update_id", 0L);
                    if (update_id + 1 > offset)
                        offset = update_id + 1;

                    if (!upd.contains("callback_query"))
                        continue;
                    const auto &cq = upd["callback_query"];
                    const std::string data = cq.value("data", "");
                    const std::string cq_id = cq.value("id", "");

                    // Ответ на callback, чтобы убрать «часики» в клиенте
                    if (!cq_id.empty()) {
                        json ans = {{"callback_query_id", cq_id}, {"text", "Stopping…"}};
                        post_json("answerCallbackQuery", ans.dump());
                    }

                    if (data == STOP_CALLBACK && on_stop)
                        on_stop();
                }
            } catch (const std::exception &ex) {
                BOOST_LOG_TRIVIAL(warning) << "AIAssist Telegram getUpdates: " << ex.what();
            }
        })
        .on_error([&](std::string, std::string error, unsigned status) {
            BOOST_LOG_TRIVIAL(warning) << "AIAssist Telegram getUpdates error HTTP " << status << ": " << error;
        })
        .perform_sync();
}

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r
