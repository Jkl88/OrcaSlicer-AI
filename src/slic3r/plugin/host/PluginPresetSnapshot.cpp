#include "PluginPresetSnapshot.hpp"

#include <libslic3r/PresetBundle.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string_view>

namespace Slic3r {
namespace {

using json = nlohmann::json;

const char* preset_type_name(Preset::Type type)
{
    switch (type) {
    case Preset::TYPE_PRINT:    return "print";
    case Preset::TYPE_FILAMENT: return "filament";
    case Preset::TYPE_PRINTER:  return "printer";
    default:                    return nullptr;
    }
}

json preset_summary(const Preset& preset)
{
    return {
        {"name", preset.name},
        {"preset_type", preset_type_name(preset.type)},
        {"visible", preset.is_visible},
        {"compatible", preset.is_compatible},
        {"is_dirty", preset.is_dirty},
        {"is_project_embedded", preset.is_project_embedded},
    };
}

const PresetCollection& collection_for(const PresetBundle& bundle, Preset::Type type)
{
    switch (type) {
    case Preset::TYPE_PRINT:    return bundle.prints;
    case Preset::TYPE_FILAMENT: return bundle.filaments;
    case Preset::TYPE_PRINTER:  return bundle.printers;
    default:                    throw std::invalid_argument("unsupported preset type");
    }
}

uint64_t fnv1a(std::string_view value)
{
    uint64_t hash = 14695981039346656037ULL;
    for (unsigned char byte : value) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string lower_copy(std::string key)
{
    std::transform(key.begin(), key.end(), key.begin(), [](unsigned char ch) { return char(std::tolower(ch)); });
    return key;
}

} // namespace

bool is_sensitive_preset_key(const std::string& key)
{
    const std::string lower = lower_copy(key);
    return lower == "printhost_apikey" || lower == "print_host" || lower == "printhost_user" ||
           lower.find("apikey") != std::string::npos ||
           lower.find("api_key") != std::string::npos || lower.find("token") != std::string::npos ||
           lower.find("password") != std::string::npos || lower.find("credential") != std::string::npos ||
           lower.find("username") != std::string::npos || lower.find("user_name") != std::string::npos;
}

PresetWorkspaceSnapshot make_preset_snapshot(const PresetBundle& bundle, bool project_dirty, bool presets_dirty,
                                             const PresetSnapshotRequest& request)
{
    PresetWorkspaceSnapshot snapshot;
    snapshot.project_dirty = project_dirty;
    snapshot.presets_dirty = presets_dirty;
    snapshot.resources = json::object();
    snapshot.selected = json::object();
    snapshot.edited = json::object();
    snapshot.requested_values = json::object();
    snapshot.diagnostics = json::array();
    snapshot.filament_slots = json::array();
    snapshot.revision_state = json::object();

    for (Preset::Type type : {Preset::TYPE_PRINT, Preset::TYPE_FILAMENT, Preset::TYPE_PRINTER}) {
        const char* name = preset_type_name(type);
        const PresetCollection& collection = collection_for(bundle, type);
        json resources = json::array();
        for (const Preset& preset : collection())
            resources.push_back(preset_summary(preset));
        snapshot.resources[name] = std::move(resources);
        snapshot.selected[name] = collection.get_selected_preset_name();
        snapshot.edited[name] = preset_summary(collection.get_edited_preset());

        const DynamicPrintConfig& config = collection.get_edited_preset().config;
        for (auto it = config.cbegin(); it != config.cend(); ++it) {
            if (!is_sensitive_preset_key(it->first))
                snapshot.revision_state[name][it->first] = config.opt_serialize(it->first);
        }
    }

    const Preset& printer = bundle.printers.get_edited_preset();
    snapshot.active_printer = preset_summary(printer);
    for (size_t slot = 0; slot < bundle.filament_presets.size(); ++slot) {
        const std::string& preset_name = bundle.filament_presets[slot];
        const Preset* preset = bundle.filaments.find_preset(preset_name);
        snapshot.filament_slots.push_back({
            {"slot", slot},
            {"preset_name", preset_name},
            {"compatible", preset != nullptr && preset->is_compatible},
        });
    }

    for (const auto& [type, requested_keys] : request.config_keys) {
        const char* category = preset_type_name(type);
        if (category == nullptr)
            continue;
        const Preset& preset = collection_for(bundle, type).get_edited_preset();
        for (const std::string& key : requested_keys) {
            if (is_sensitive_preset_key(key)) {
                snapshot.diagnostics.push_back({{"code", "sensitive_key"}, {"key", key},
                                                {"message", "Requested preset key is not available"}});
                continue;
            }
            if (preset.config.option(key) != nullptr)
                snapshot.requested_values[category][key] = preset.config.opt_serialize(key);
        }
    }

    snapshot.revision = compute_preset_revision(snapshot);
    return snapshot;
}

std::string compute_preset_revision(const PresetWorkspaceSnapshot& snapshot)
{
    json revision_input = {
        {"resources", snapshot.resources},
        {"selected", snapshot.selected},
        {"edited", snapshot.edited},
        {"filament_slots", snapshot.filament_slots},
        {"active_printer", snapshot.active_printer},
        {"project_dirty", snapshot.project_dirty},
        {"presets_dirty", snapshot.presets_dirty},
        {"revision_state", snapshot.revision_state},
    };
    std::ostringstream output;
    output << std::hex << std::setfill('0') << std::setw(16) << fnv1a(revision_input.dump());
    return output.str();
}

void to_json(json& json, const PresetWorkspaceSnapshot& snapshot)
{
    json = {
        {"revision", snapshot.revision},
        {"resources", snapshot.resources},
        {"selected", snapshot.selected},
        {"edited", snapshot.edited},
        {"filament_slots", snapshot.filament_slots},
        {"active_printer", snapshot.active_printer},
        {"project_dirty", snapshot.project_dirty},
        {"presets_dirty", snapshot.presets_dirty},
        {"requested_values", snapshot.requested_values},
        {"diagnostics", snapshot.diagnostics},
    };
}

} // namespace Slic3r
