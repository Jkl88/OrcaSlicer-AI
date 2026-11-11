#pragma once

#include <libslic3r/Preset.hpp>

#include <map>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace Slic3r {

class PresetBundle;

struct PresetSnapshotRequest
{
    std::map<Preset::Type, std::vector<std::string>> config_keys;
};

struct PresetWorkspaceSnapshot
{
    std::string    revision;
    nlohmann::json resources;
    nlohmann::json selected;
    nlohmann::json edited;
    nlohmann::json filament_slots;
    nlohmann::json active_printer;
    bool           project_dirty{false};
    bool           presets_dirty{false};
    nlohmann::json requested_values;
    nlohmann::json diagnostics;
    nlohmann::json revision_state;
};

PresetWorkspaceSnapshot make_preset_snapshot(const PresetBundle& bundle, bool project_dirty, bool presets_dirty,
                                             const PresetSnapshotRequest& request);
std::string compute_preset_revision(const PresetWorkspaceSnapshot& snapshot);
bool is_sensitive_preset_key(const std::string& key);

void to_json(nlohmann::json& json, const PresetWorkspaceSnapshot& snapshot);

} // namespace Slic3r
