#include "PluginPresetTransaction.hpp"

#include <set>
#include <utility>

namespace Slic3r {
namespace {

using json = nlohmann::json;

PresetTransactionResult diagnostic_result(PresetTransactionStatus status, const char* code,
                                          std::optional<size_t> operation_index, const char* field,
                                          const char* message)
{
    PresetTransactionResult result;
    result.status = status;
    result.diagnostics.push_back({{"code", code},
                                  {"operation_index", operation_index ? json(*operation_index) : json(nullptr)},
                                  {"field", field},
                                  {"message", message}});
    return result;
}

bool is_preset_type(const json& value)
{
    return value.is_string() &&
           (value == "print" || value == "filament" || value == "printer");
}

bool is_exact_scope(const json& scope, const char* key, const json& value)
{
    return scope.is_object() && scope.size() == 1 && scope.contains(key) && scope[key] == value;
}

} // namespace

PresetTransactionResult parse_transaction_request(const json& request_json)
{
    if (!request_json.is_object())
        return diagnostic_result(PresetTransactionStatus::Invalid, "invalid_request", std::nullopt, "request",
                                 "Transaction request must be an object");
    if (!request_json.contains("expected_revision") || !request_json["expected_revision"].is_string() ||
        request_json["expected_revision"].get<std::string>().empty())
        return diagnostic_result(PresetTransactionStatus::Invalid, "missing_expected_revision", std::nullopt,
                                 "expected_revision", "Transaction request requires a non-empty expected_revision");
    if (!request_json.contains("operations") || !request_json["operations"].is_array())
        return diagnostic_result(PresetTransactionStatus::Invalid, "invalid_operations", std::nullopt, "operations",
                                 "Transaction request requires an operations array");
    if (request_json["operations"].empty())
        return diagnostic_result(PresetTransactionStatus::Invalid, "empty_operations", std::nullopt, "operations",
                                 "Transaction request requires at least one operation");

    if (request_json.contains("interaction") && !request_json["interaction"].is_string())
        return diagnostic_result(PresetTransactionStatus::Invalid, "invalid_interaction", std::nullopt, "interaction",
                                 "Transaction interaction must be a string");

    PresetTransactionRequest request;
    request.expected_revision = request_json["expected_revision"].get<std::string>();
    request.interaction = request_json.value("interaction", "confirm");
    if (request.interaction != "confirm")
        return diagnostic_result(PresetTransactionStatus::Unsupported, "unsupported_interaction", std::nullopt,
                                 "interaction", "Transaction interaction is not supported");

    std::set<std::pair<std::string, std::string>> assigned_keys;
    const json& operations = request_json["operations"];
    for (size_t index = 0; index < operations.size(); ++index) {
        const json& operation = operations[index];
        if (!operation.is_object() || !operation.contains("op") || !operation["op"].is_string())
            return diagnostic_result(PresetTransactionStatus::Invalid, "unknown_operation", index, "op",
                                     "Transaction operation is not supported");
        if (!operation.contains("preset_type") || !is_preset_type(operation["preset_type"]))
            return diagnostic_result(PresetTransactionStatus::Invalid, "unknown_preset_type", index, "preset_type",
                                     "Transaction preset_type is not supported");

        const std::string preset_type = operation["preset_type"].get<std::string>();
        const std::string op = operation["op"].get<std::string>();
        if (op == "select") {
            if (!operation.contains("name") || !operation["name"].is_string() || operation["name"].empty())
                return diagnostic_result(PresetTransactionStatus::Invalid, "invalid_name", index, "name",
                                         "Select operation requires a non-empty preset name");
            if (!operation.contains("scope") || !operation["scope"].is_object() || preset_type != "filament" ||
                operation["scope"].size() != 1 || !operation["scope"].contains("slot") ||
                !operation["scope"]["slot"].is_number_unsigned())
                return diagnostic_result(PresetTransactionStatus::Invalid, "invalid_scope", index, "scope",
                                         "Select operation requires a filament slot scope");
            request.operations.push_back(SelectPresetOperation{preset_type, operation["name"].get<std::string>(),
                                                                 operation["scope"]["slot"].get<size_t>()});
        } else if (op == "set_values") {
            if (!operation.contains("scope") || !is_exact_scope(operation["scope"], "edited", true))
                return diagnostic_result(PresetTransactionStatus::Invalid, "invalid_scope", index, "scope",
                                         "Set-values operation requires an edited scope");
            if (!operation.contains("values") || !operation["values"].is_object() || operation["values"].empty())
                return diagnostic_result(PresetTransactionStatus::Invalid, "invalid_values", index, "values",
                                         "Set-values operation requires at least one value");
            for (auto it = operation["values"].begin(); it != operation["values"].end(); ++it) {
                if (!it.value().is_string())
                    return diagnostic_result(PresetTransactionStatus::Invalid, "invalid_value", index, "values",
                                             "Set-values operation values must be serialized strings");
                if (!assigned_keys.emplace(preset_type, it.key()).second)
                    return diagnostic_result(PresetTransactionStatus::Invalid, "duplicate_key_assignment", index,
                                             "values", "A preset key may be assigned only once per transaction");
            }
            request.operations.push_back(SetValuesOperation{preset_type, operation["values"]});
        } else {
            return diagnostic_result(PresetTransactionStatus::Invalid, "unknown_operation", index, "op",
                                     "Transaction operation is not supported");
        }
    }

    PresetTransactionResult result;
    result.status = PresetTransactionStatus::Valid;
    result.request = std::move(request);
    return result;
}

PresetTransactionResult validate_transaction_shape(const json& request_json, const std::string& current_revision,
                                                    const std::function<bool()>& confirm,
                                                    const std::function<void()>& apply)
{
    PresetTransactionResult result = parse_transaction_request(request_json);
    if (result.status != PresetTransactionStatus::Valid)
        return result;
    if (result.request->expected_revision != current_revision) {
        result.status = PresetTransactionStatus::Conflict;
        result.revision = current_revision;
        result.affected = json::array();
        return result;
    }
    if (!confirm())
        return result;
    apply();
    return result;
}

} // namespace Slic3r
