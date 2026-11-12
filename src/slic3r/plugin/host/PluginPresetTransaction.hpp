#pragma once

#include <functional>
#include <map>
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include <libslic3r/PrintConfig.hpp>

#include <nlohmann/json.hpp>

namespace Slic3r {

class PresetBundle;
class DynamicPrintConfig;

struct SelectPresetOperation
{
    std::string preset_type;
    std::string name;
    size_t      slot;
};

struct SetValuesOperation
{
    std::string    preset_type;
    nlohmann::json values;
};

using PresetOperation = std::variant<SelectPresetOperation, SetValuesOperation>;

struct PresetTransactionRequest
{
    std::string                  expected_revision;
    std::string                  interaction;
    std::vector<PresetOperation> operations;
};

enum class PresetTransactionStatus {
    Valid,
    Invalid,
    Unsupported,
    Conflict,
    Applied,
    Cancelled,
    NotFound,
    Incompatible,
    InvalidSlot,
    Error,
};

struct PresetTransactionResult
{
    PresetTransactionStatus                status{PresetTransactionStatus::Invalid};
    std::string                            revision;
    nlohmann::json                         affected{nlohmann::json::array()};
    nlohmann::json                         diagnostics{nlohmann::json::array()};
    std::string                            message;
    std::optional<PresetTransactionRequest> request;
};

struct StagedValueChange
{
    std::string preset_type;
    std::string key;
    std::string old_value;
    std::string new_value;
};

struct StagedPresetChanges
{
    PresetTransactionStatus        status{PresetTransactionStatus::Invalid};
    std::vector<StagedValueChange> changes;
    nlohmann::json                 diagnostics{nlohmann::json::array()};
    std::map<std::string, DynamicPrintConfig> original_configs;
    std::map<std::string, DynamicPrintConfig> staged_configs;
};

struct StagedSelection
{
    size_t      slot{0};
    std::string old_name;
    std::string new_name;
};

// Everything a transaction would change, shown to the user before anything is applied.
struct PresetTransactionPreview
{
    std::vector<StagedValueChange> values;
    std::vector<StagedSelection>   selections;
};

struct FilamentSelectionResult
{
    PresetTransactionStatus status{PresetTransactionStatus::Invalid};
    size_t                  slot{0};
    std::string             preset_name;
    std::string             message;
};

PresetTransactionResult parse_transaction_request(const nlohmann::json& request);

PresetTransactionResult validate_transaction_shape(const nlohmann::json& request, const std::string& current_revision,
                                                   const std::function<bool()>& confirm,
                                                   const std::function<void()>& apply);

StagedPresetChanges stage_value_changes(PresetBundle& bundle, const std::vector<SetValuesOperation>& operations);

void apply_staged_value_changes(PresetBundle& bundle, StagedPresetChanges& staged,
                                const std::function<void(const std::string&)>& on_category_applied = nullptr);

void rollback_staged_value_changes(PresetBundle& bundle, const StagedPresetChanges& staged);

// Checks slot range, preset existence and printer compatibility without changing anything.
// Returns status Valid when the selection may proceed.
FilamentSelectionResult validate_filament_selection(const PresetBundle& bundle, size_t slot, const std::string& preset_name);

// Validates and assigns the slot in the bundle only; the GUI path is Plater::select_filament_preset_for_plugin().
FilamentSelectionResult select_filament_preset(PresetBundle& bundle, size_t slot, const std::string& preset_name);

const char*    to_string(PresetTransactionStatus status);
nlohmann::json transaction_result_json(const PresetTransactionResult& result);

// Side effects of a commit. Unset hooks fall back to bundle-only behavior, which is what tests use;
// the GUI binding routes them through Tab and Plater so the visible state follows.
struct PresetTransactionHooks
{
    // Shows the preview; false means the user declined and nothing is applied.
    std::function<bool(const std::string& plugin_id, const PresetTransactionPreview& preview)> confirm;
    // Publishes a complete edited config for one preset type ("print", "filament", "printer").
    std::function<void(const std::string& preset_type, const DynamicPrintConfig& config)> apply_config;
    // Selects a filament preset for a zero-based slot; any status other than Applied aborts and rolls back.
    std::function<FilamentSelectionResult(size_t slot, const std::string& preset_name)> select_filament;
    // Revision of the workspace after a successful apply.
    std::function<std::string()> revision;
};

// Parses, checks the revision, validates every operation, asks for confirmation once, then applies all
// operations or none of them.
PresetTransactionResult commit_preset_transaction(PresetBundle&                 bundle,
                                                  const std::string&            current_revision,
                                                  const std::string&            plugin_id,
                                                  const nlohmann::json&         request,
                                                  const PresetTransactionHooks& hooks);

} // namespace Slic3r
