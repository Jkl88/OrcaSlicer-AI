#include "GeminiClient.hpp"

#include "AIConfig.hpp"
#include "slic3r/Utils/Http.hpp"

#include <boost/log/trivial.hpp>
#include <nlohmann/json.hpp>

namespace Slic3r {
namespace GUI {
namespace AIAssist {

using json = nlohmann::json;

GeminiClient::GeminiClient(AppConfig *cfg) : m_cfg(cfg) {}

GeminiResult GeminiClient::generate_text(const std::string &system_prompt, const std::string &user_prompt) const
{
    return generate_with_images(system_prompt, user_prompt, {});
}

GeminiResult GeminiClient::generate_with_images(const std::string &system_prompt,
                                                const std::string &user_prompt,
                                                const std::vector<GeminiImagePart> &images) const
{
    json parts = json::array();
    if (!system_prompt.empty())
        parts.push_back({{"text", system_prompt + "\n\n"}});
    parts.push_back({{"text", user_prompt}});
    for (const auto &img : images) {
        if (img.base64_data.empty())
            continue;
        parts.push_back({
            {"inline_data", {
                {"mime_type", img.mime_type.empty() ? "image/jpeg" : img.mime_type},
                {"data", img.base64_data}
            }}
        });
    }

    json body = {
        {"contents", json::array({json{{"role", "user"}, {"parts", parts}}})},
        {"generationConfig", {
            {"temperature", 0.2},
            {"responseMimeType", "application/json"}
        }}
    };
    return request(body.dump());
}

GeminiResult GeminiClient::request(const std::string &json_body) const
{
    GeminiResult out;
    const std::string api_key = get(m_cfg, KEY_GEMINI_API_KEY);
    if (api_key.empty()) {
        out.error = "Gemini API key is not set (Preferences → AI Assist).";
        return out;
    }

    const std::string model = get(m_cfg, KEY_GEMINI_MODEL, DEFAULT_GEMINI_MODEL);
    const std::string url = gemini_base_url(m_cfg) + "/v1beta/models/" + model + ":generateContent?key=" + api_key;

    // Не логируем ключ целиком
    BOOST_LOG_TRIVIAL(info) << "AIAssist Gemini request model=" << model;

    bool done = false;
    auto http = Http::post(url);
    http.header("Content-Type", "application/json")
        .timeout_max(120)
        .size_limit(16 * 1024 * 1024)
        .set_post_body(json_body)
        .tls_verify(true)
        .on_complete([&](std::string body, unsigned status) {
            out.http_status = status;
            try {
                auto j = json::parse(body);
                if (j.contains("error")) {
                    out.error = j["error"].value("message", body);
                    return;
                }
                // candidates[0].content.parts[*].text
                if (!j.contains("candidates") || j["candidates"].empty()) {
                    out.error = "Empty candidates from Gemini";
                    return;
                }
                std::string text;
                const auto &content = j["candidates"][0]["content"];
                if (content.contains("parts")) {
                    for (const auto &p : content["parts"]) {
                        if (p.contains("text"))
                            text += p["text"].get<std::string>();
                    }
                }
                out.text = std::move(text);
                out.ok = !out.text.empty();
                if (!out.ok)
                    out.error = "Gemini returned empty text";
            } catch (const std::exception &ex) {
                out.error = std::string("Gemini JSON parse: ") + ex.what();
            }
            done = true;
        })
        .on_error([&](std::string body, std::string error, unsigned status) {
            out.http_status = status;
            out.error = error.empty() ? body : error;
            if (out.error.empty())
                out.error = "HTTP " + std::to_string(status);
            // Маскируем возможный ключ в теле ошибки
            const auto pos = out.error.find(api_key);
            if (pos != std::string::npos)
                out.error.replace(pos, api_key.size(), "***");
            done = true;
        })
        .perform_sync();

    if (!done && out.error.empty())
        out.error = "Gemini request did not complete";
    return out;
}

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r
