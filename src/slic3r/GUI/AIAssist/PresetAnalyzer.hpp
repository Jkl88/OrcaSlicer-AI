#ifndef slic3r_AIAssist_PresetAnalyzer_hpp_
#define slic3r_AIAssist_PresetAnalyzer_hpp_

#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace Slic3r {

class DynamicPrintConfig;
class PresetBundle;

namespace GUI {
namespace AIAssist {

struct SettingChange
{
    std::string category; // print | filament | printer
    std::string key;
    std::string old_value;
    std::string new_value;
    std::string reason;
};

struct AnalysisResult
{
    bool                     ok{false};
    std::string              summary;
    std::string              error;
    std::vector<SettingChange> changes;
    nlohmann::json           raw;
};

// Снимок текущих пресетов + применение правок от Gemini.
class PresetAnalyzer
{
public:
    static nlohmann::json dump_current_settings(const PresetBundle *bundle);
    static AnalysisResult parse_ai_response(const std::string &ai_text);
    // Применяет changes к edited presets и обновляет вкладки UI. Возвращает число применённых ключей.
    static int apply_changes(const std::vector<SettingChange> &changes, std::string &error);

    static std::string system_prompt_analyze();
    static std::string system_prompt_problem();
    static std::string system_prompt_vision();

private:
    static nlohmann::json config_to_json(const DynamicPrintConfig &cfg, const std::vector<std::string> &prefer_keys);
    static DynamicPrintConfig *config_for_category(const std::string &category);
};

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r

#endif
