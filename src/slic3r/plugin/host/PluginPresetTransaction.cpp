#include "PluginPresetTransaction.hpp"

#include <libslic3r/PresetBundle.hpp>
#include <libslic3r/Preset.hpp>

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

namespace {

bool is_sensitive_key(const std::string& key)
{
    static const std::set<std::string> sensitive_keys = {
        "printhost_apikey", "printhost_password", "printhost_client_cert_password",
        "print_host", "printhost_user", "printhost_ssl_ca_file",
    };
    return sensitive_keys.count(key) > 0 ||
           key.find("_key") != std::string::npos ||
           key.find("_password") != std::string::npos ||
           key.find("_token") != std::string::npos ||
           key.find("_secret") != std::string::npos ||
           key.find("apikey") != std::string::npos;
}

Preset::Type preset_type_from_string(const std::string& type_str)
{
    if (type_str == "print")    return Preset::TYPE_PRINT;
    if (type_str == "filament") return Preset::TYPE_FILAMENT;
    if (type_str == "printer")  return Preset::TYPE_PRINTER;
    throw std::invalid_argument("unsupported preset type");
}

Preset& get_edited_preset(PresetBundle& bundle, const std::string& preset_type)
{
    if (preset_type == "print")    return bundle.prints.get_edited_preset();
    if (preset_type == "filament") return bundle.filaments.get_edited_preset();
    if (preset_type == "printer")  return bundle.printers.get_edited_preset();
    throw std::invalid_argument("unsupported preset type");
}

json diagnostic_json(const char* code, size_t operation_index, const char* field, const char* message)
{
    return {{"code", code}, {"operation_index", operation_index}, {"field", field}, {"message", message}};
}

} // anonymous namespace

StagedPresetChanges stage_value_changes(PresetBundle& bundle, const std::vector<SetValuesOperation>& operations)
{
    StagedPresetChanges result;
    result.diagnostics = json::array();

    std::map<std::string, DynamicPrintConfig> config_copies;
    size_t operation_index = 0;

    for (const SetValuesOperation& op : operations) {
        if (op.preset_type != "print" && op.preset_type != "filament" && op.preset_type != "printer") {
            result.status = PresetTransactionStatus::Invalid;
            result.diagnostics.push_back(
                diagnostic_json("unsupported_preset_type", operation_index, "preset_type",
                               "Preset type must be print, filament, or printer"));
            return result;
        }

        try {
            Preset& edited = get_edited_preset(bundle, op.preset_type);

            if (config_copies.find(op.preset_type) == config_copies.end()) {
                config_copies[op.preset_type] = edited.config;
                result.original_configs[op.preset_type] = edited.config;
            }

            DynamicPrintConfig& config_copy = config_copies[op.preset_type];

            for (auto it = op.values.begin(); it != op.values.end(); ++it) {
                const std::string& key = it.key();
                const std::string& serialized_value = it.value().get<std::string>();

                if (is_sensitive_key(key)) {
                    result.status = PresetTransactionStatus::Invalid;
                    result.diagnostics.push_back(
                        diagnostic_json("sensitive_key", operation_index, "values",
                                       ("Key '" + key + "' contains sensitive data and cannot be modified").c_str()));
                    return result;
                }

                const ConfigDef* def = config_copy.def();
                if (!def || !def->has(key)) {
                    result.status = PresetTransactionStatus::Invalid;
                    result.diagnostics.push_back(
                        diagnostic_json("unknown_key", operation_index, "values",
                                       ("Unknown configuration key: " + key).c_str()));
                    return result;
                }

                const std::string old_value = edited.config.opt_serialize(key);

                ConfigSubstitutionContext substitution_context(ForwardCompatibilitySubstitutionRule::Disable);
                try {
                    if (!config_copy.set_deserialize_nothrow(key, serialized_value, substitution_context, false)) {
                        result.status = PresetTransactionStatus::Invalid;
                        result.diagnostics.push_back(
                            diagnostic_json("invalid_value", operation_index, "values",
                                           ("Cannot deserialize value for key '" + key + "'").c_str()));
                        return result;
                    }
                } catch (const std::exception& e) {
                    result.status = PresetTransactionStatus::Invalid;
                    result.diagnostics.push_back(
                        diagnostic_json("invalid_value", operation_index, "values",
                                       ("Cannot deserialize value for key '" + key + "': " + e.what()).c_str()));
                    return result;
                }

                const std::string new_value = config_copy.opt_serialize(key);

                StagedValueChange change;
                change.preset_type = op.preset_type;
                change.key = key;
                change.old_value = old_value;
                change.new_value = new_value;
                result.changes.push_back(change);
            }
        } catch (const std::exception& e) {
            result.status = PresetTransactionStatus::Invalid;
            result.diagnostics.push_back(
                diagnostic_json("error", operation_index, "preset_type", e.what()));
            return result;
        }

        ++operation_index;
    }

    result.status = PresetTransactionStatus::Valid;
    return result;
}

void apply_staged_value_changes(PresetBundle& bundle, StagedPresetChanges& staged,
                                const std::function<void(const std::string&)>& on_category_applied)
{
    if (staged.status != PresetTransactionStatus::Valid) {
        throw std::logic_error("Cannot apply staged changes with non-Valid status");
    }

    std::set<std::string> affected_types;
    for (const StagedValueChange& change : staged.changes) {
        affected_types.insert(change.preset_type);
    }

    try {
        for (const std::string& type : affected_types) {
            Preset& edited = get_edited_preset(bundle, type);

            for (const StagedValueChange& change : staged.changes) {
                if (change.preset_type == type) {
                    ConfigSubstitutionContext substitution_context(ForwardCompatibilitySubstitutionRule::Disable);
                    if (!edited.config.set_deserialize_nothrow(change.key, change.new_value, substitution_context, false)) {
                        throw std::runtime_error("Failed to apply value for key: " + change.key);
                    }
                }
            }

            if (on_category_applied) {
                on_category_applied(type);
            }
        }
    } catch (...) {
        rollback_staged_value_changes(bundle, staged);
        throw;
    }
}

void rollback_staged_value_changes(PresetBundle& bundle, const StagedPresetChanges& staged)
{
    for (const auto& [preset_type, original_config] : staged.original_configs) {
        try {
            Preset& edited = get_edited_preset(bundle, preset_type);
            edited.config = original_config;
        } catch (const std::exception&) {
        }
    }
}

FilamentSelectionResult select_filament_preset_for_test(PresetBundle& bundle, size_t slot, const std::string& preset_name)
{
    FilamentSelectionResult result;
    result.slot = slot;
    result.preset_name = preset_name;

    if (slot >= bundle.filament_presets.size()) {
        result.status = PresetTransactionStatus::InvalidSlot;
        result.message = "Filament slot index out of range";
        return result;
    }

    const Preset* preset = bundle.filaments.find_preset(preset_name, false);
    if (!preset) {
        result.status = PresetTransactionStatus::NotFound;
        result.message = "Filament preset not found: " + preset_name;
        return result;
    }

    if (!preset->is_compatible) {
        result.status = PresetTransactionStatus::Incompatible;
        result.message = "Filament preset is not compatible with current printer";
        return result;
    }

    bundle.filament_presets[slot] = preset_name;
    result.status = PresetTransactionStatus::Applied;
    result.message = "Filament preset applied successfully";
    return result;
}

PresetTransactionResult commit_preset_transaction_for_test(
    PresetBundle& bundle,
    const std::string& current_revision,
    const std::string& plugin_id,
    const json& request_json,
    const TransactionConfirmCallback& confirm_callback,
    bool inject_apply_failure)
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

    std::vector<SetValuesOperation> value_ops;
    for (const auto& op : result.request->operations) {
        if (std::holds_alternative<SetValuesOperation>(op)) {
            value_ops.push_back(std::get<SetValuesOperation>(op));
        }
    }

    StagedPresetChanges staged = stage_value_changes(bundle, value_ops);
    if (staged.status != PresetTransactionStatus::Valid) {
        result.status = staged.status;
        result.diagnostics = staged.diagnostics;
        return result;
    }

    if (!confirm_callback(plugin_id, staged.changes)) {
        result.status = PresetTransactionStatus::Cancelled;
        result.message = "User cancelled the transaction";
        return result;
    }

    try {
        if (inject_apply_failure) {
            throw std::runtime_error("Injected apply failure for testing");
        }
        apply_staged_value_changes(bundle, staged);
        result.status = PresetTransactionStatus::Applied;
        result.revision = current_revision;
    } catch (const std::exception& e) {
        result.status = PresetTransactionStatus::Error;
        result.message = std::string("Apply failed: ") + e.what();
        rollback_staged_value_changes(bundle, staged);
    }

    return result;
}

} // namespace Slic3r
