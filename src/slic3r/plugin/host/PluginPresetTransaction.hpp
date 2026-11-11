#pragma once

#include <functional>
#include <map>
#include <optional>
#include <string>
#include <variant>
#include <vector>

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

// Test-only filament selection adapter (no GUI dependencies)
FilamentSelectionResult select_filament_preset_for_test(PresetBundle& bundle, size_t slot, const std::string& preset_name);

// Test-only transaction coordinator (no GUI dependencies, injectable confirmation callback)
using TransactionConfirmCallback = std::function<bool(const std::string& plugin_id, const std::vector<StagedValueChange>& changes)>;
PresetTransactionResult commit_preset_transaction_for_test(
    PresetBundle& bundle,
    const std::string& current_revision,
    const std::string& plugin_id,
    const nlohmann::json& request_json,
    const TransactionConfirmCallback& confirm_callback,
    bool inject_apply_failure = false);

} // namespace Slic3r
