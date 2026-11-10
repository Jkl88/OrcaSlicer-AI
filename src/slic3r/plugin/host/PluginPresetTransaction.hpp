#pragma once

#include <functional>
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include <nlohmann/json.hpp>

namespace Slic3r {

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

PresetTransactionResult parse_transaction_request(const nlohmann::json& request);

PresetTransactionResult validate_transaction_shape(const nlohmann::json& request, const std::string& current_revision,
                                                   const std::function<bool()>& confirm,
                                                   const std::function<void()>& apply);

} // namespace Slic3r
