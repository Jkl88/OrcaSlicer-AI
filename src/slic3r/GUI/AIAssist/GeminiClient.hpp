#ifndef slic3r_AIAssist_GeminiClient_hpp_
#define slic3r_AIAssist_GeminiClient_hpp_

#include <string>
#include <vector>

#include "libslic3r/AppConfig.hpp"

namespace Slic3r {
namespace GUI {
namespace AIAssist {

struct GeminiImagePart
{
    std::string mime_type; // image/jpeg, image/png
    std::string base64_data;
};

struct GeminiResult
{
    bool        ok{false};
    std::string text;   // сырой ответ модели (ожидаем JSON)
    std::string error;
    unsigned    http_status{0};
};

// Синхронный клиент Gemini generateContent (текст + опционально картинки).
class GeminiClient
{
public:
    explicit GeminiClient(AppConfig *cfg);

    GeminiResult generate_text(const std::string &system_prompt, const std::string &user_prompt) const;
    GeminiResult generate_with_images(const std::string &system_prompt,
                                      const std::string &user_prompt,
                                      const std::vector<GeminiImagePart> &images) const;

private:
    AppConfig *m_cfg;
    GeminiResult request(const std::string &json_body) const;
};

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r

#endif
