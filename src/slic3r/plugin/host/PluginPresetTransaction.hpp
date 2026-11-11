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

PresetTransactionResult parse_transaction_request(const nlohmann::json& request);

PresetTransactionResult validate_transaction_shape(const nlohmann::json& request, const std::string& current_revision,
                                                   const std::function<bool()>& confirm,
                                                   const std::function<void()>& apply);

StagedPresetChanges stage_value_changes(PresetBundle& bundle, const std::vector<SetValuesOperation>& operations);

void apply_staged_value_changes(PresetBundle& bundle, StagedPresetChanges& staged,
                                const std::function<void(const std::string&)>& on_category_applied = nullptr);

void rollback_staged_value_changes(PresetBundle& bundle, const StagedPresetChanges& staged);

} // namespace Slic3r
