#include <catch2/catch_all.hpp>

#include "python_test_support.hpp"
#include "plugin_test_utils.hpp"

#include <slic3r/plugin/PluginAuditManager.hpp>
#include <slic3r/plugin/host/PluginHostStorage.hpp>
#include <slic3r/plugin/host/PluginPresetSnapshot.hpp>
#include <slic3r/plugin/host/PluginPresetTransaction.hpp>

#include <libslic3r/PresetBundle.hpp>

#include <boost/filesystem.hpp>
#include <boost/nowide/fstream.hpp>
#include <pybind11/pybind11.h>

#include <nlohmann/json.hpp>

#include <string>

namespace py = pybind11;

namespace {

bool has_attr(const py::handle& object, const char* name)
{
    return py::hasattr(object, name);
}

} // namespace

TEST_CASE("Plugin host storage is private to the active plugin", "[PluginHost][Python]")
{
    namespace fs = boost::filesystem;
    Slic3r::ScopedDataDir data_dir_guard("plugin-host-storage-private");

    py::object storage = import_orca_module().attr("host").attr("v1").attr("plugin_storage");
    CHECK_THROWS_AS(storage(), py::error_already_set);

    fs::path plugin_one_path;
    {
        Slic3r::ScopedPluginAuditContext context("plugin-one");
        plugin_one_path = fs::canonical(storage().cast<std::string>());
        CHECK(fs::is_directory(plugin_one_path));
        boost::nowide::ofstream(plugin_one_path / "state.txt") << "private state";
        CHECK(fs::is_regular_file(plugin_one_path / "state.txt"));
    }

    fs::path plugin_two_path;
    {
        Slic3r::ScopedPluginAuditContext context("plugin-two");
        plugin_two_path = fs::canonical(storage().cast<std::string>());
    }

    CHECK(plugin_one_path != plugin_two_path);
    CHECK(plugin_one_path.parent_path() == plugin_two_path.parent_path());
    CHECK(plugin_one_path.parent_path().filename() == "orca_plugins_data");
}

TEST_CASE("Plugin host storage rejects escaping identities", "[PluginHost][Python]")
{
    namespace fs = boost::filesystem;
    Slic3r::ScopedDataDir data_dir_guard("plugin-host-storage-identities");

    py::object storage = import_orca_module().attr("host").attr("v1").attr("plugin_storage");
    for (const std::string& plugin_key : { "../outside", "nested/plugin", "\\\\network", "" }) {
        CAPTURE(plugin_key);
        Slic3r::ScopedPluginAuditContext context(plugin_key);
        if (plugin_key.empty()) {
            CHECK_THROWS_AS(storage(), py::error_already_set);
            continue;
        }

        const fs::path path = fs::canonical(storage().cast<std::string>());
        CHECK(path.parent_path().filename() == "orca_plugins_data");
        CHECK(path.filename() != "..");
    }
}

TEST_CASE("Plugin data cleanup retains by default and rejects child escapes", "[PluginHost]")
{
    namespace fs = boost::filesystem;
    Slic3r::ScopedDataDir data_dir_guard("plugin-host-storage-cleanup");

    const fs::path plugin_one = Slic3r::plugin_storage_path("cleanup-plugin-one");
    const fs::path plugin_two = Slic3r::plugin_storage_path("cleanup-plugin-two");
    boost::nowide::ofstream(plugin_one / "state.txt") << "remove only when confirmed";
    boost::nowide::ofstream(plugin_two / "state.txt") << "other plugin";

    CHECK(fs::exists(plugin_one)); // Uninstall's default remove_plugin_data=false leaves data intact.

    std::string error;
    CHECK(Slic3r::delete_plugin_storage("cleanup-plugin-one", error));
    CHECK_FALSE(fs::exists(plugin_one));
    CHECK(fs::exists(plugin_two));

    const fs::path escaped_storage = Slic3r::plugin_storage_path("cleanup-plugin-escape");
    const fs::path external_target = escaped_storage.parent_path().parent_path() / "plugin-storage-external";
    fs::create_directories(external_target);
    fs::remove_all(escaped_storage);
    fs::create_symlink(external_target, escaped_storage);
    CHECK_FALSE(Slic3r::delete_plugin_storage("cleanup-plugin-escape", error));
    CHECK(fs::exists(external_target));
    fs::remove(escaped_storage);
    fs::remove_all(external_target);

    CHECK_FALSE(Slic3r::delete_plugin_storage("", error));
    CHECK_FALSE(error.empty());
}

TEST_CASE("Plugin storage rejects a symlinked data root", "[PluginHost][Regression]")
{
    namespace fs = boost::filesystem;
    Slic3r::ScopedDataDir data_dir_guard("plugin-host-storage-root-symlink");
    Slic3r::ScopedTemporaryDir external("orca-plugin-storage-external-root");

    const fs::path storage_root = data_dir_guard.dir / "orca_plugins_data";
    fs::create_directory_symlink(external.path(), storage_root);

    CHECK_THROWS_AS(Slic3r::plugin_storage_path("root-escape"), std::runtime_error);
    CHECK_FALSE(fs::exists(external.path() / "root-escape"));

    std::string error;
    CHECK_FALSE(Slic3r::delete_plugin_storage("root-escape", error));
    CHECK_FALSE(error.empty());
    CHECK(fs::is_directory(external.path()));
}

TEST_CASE("Plugin delete confirmation maps cancellation and data choices", "[PluginHost]")
{
    CHECK(Slic3r::plugin_delete_choice(false, false) == Slic3r::PluginDeleteChoice::Cancel);
    CHECK(Slic3r::plugin_delete_choice(false, true) == Slic3r::PluginDeleteChoice::Cancel);
    CHECK(Slic3r::plugin_delete_choice(true, false) == Slic3r::PluginDeleteChoice::RetainData);
    CHECK(Slic3r::plugin_delete_choice(true, true) == Slic3r::PluginDeleteChoice::RemoveData);
}

TEST_CASE("Preset snapshot captures detached selected workspace state", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    Slic3r::Preset& print = bundle.prints.get_edited_preset();
    Slic3r::Preset& filament = bundle.filaments.get_edited_preset();
    Slic3r::Preset& printer = bundle.printers.get_edited_preset();
    print.config.set("layer_height", 0.16, true);
    printer.config.set("printer_model", "Snapshot Printer", true);
    bundle.filament_presets = {filament.name, filament.name};

    Slic3r::PresetSnapshotRequest request;
    request.config_keys[Slic3r::Preset::TYPE_PRINT] = {"layer_height"};
    const Slic3r::PresetWorkspaceSnapshot snapshot = Slic3r::make_preset_snapshot(bundle, true, true, request);
    const nlohmann::json json = snapshot;

    CHECK(json.contains("revision"));
    CHECK(json.contains("resources"));
    CHECK(json.contains("selected"));
    CHECK(json.contains("edited"));
    CHECK(json.contains("filament_slots"));
    CHECK(json.contains("active_printer"));
    CHECK(json["project_dirty"] == true);
    CHECK(json["presets_dirty"] == true);
    CHECK(json["filament_slots"].size() == 2);
    CHECK(json["requested_values"]["print"]["layer_height"] == "0.16");
    CHECK_FALSE(json["requested_values"]["print"].contains("printer_model"));

    const std::string revision = snapshot.revision;
    bundle.filament_presets[1] = "different filament";
    CHECK(Slic3r::make_preset_snapshot(bundle, true, true, request).revision != revision);

    print.config.set("layer_height", 0.28, true);
    CHECK(json["requested_values"]["print"]["layer_height"] == "0.16");
}

TEST_CASE("Preset snapshot excludes sensitive requested values", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    Slic3r::Preset& print = bundle.prints.get_edited_preset();
    Slic3r::Preset& printer = bundle.printers.get_edited_preset();
    print.config.set("layer_height", 0.2, true);
    printer.config.set("printhost_apikey", "must-not-leak", true);
    printer.config.set("print_host", "https://printer.example", true);
    printer.config.set("printhost_user", "must-not-leak", true);

    Slic3r::PresetSnapshotRequest request;
    request.config_keys[Slic3r::Preset::TYPE_PRINT] = {"layer_height", "does_not_exist"};
    request.config_keys[Slic3r::Preset::TYPE_PRINTER] = {"printhost_apikey", "print_host", "printhost_user"};
    const nlohmann::json json = Slic3r::make_preset_snapshot(bundle, false, false, request);

    CHECK(json["requested_values"]["print"]["layer_height"] == "0.2");
    CHECK_FALSE(json["requested_values"]["print"].contains("does_not_exist"));
    CHECK_FALSE(json["requested_values"].contains("printer"));
    REQUIRE(json["diagnostics"].size() == 3);
    for (const nlohmann::json& diagnostic : json["diagnostics"]) {
        CHECK(diagnostic["code"] == "sensitive_key");
        CHECK(diagnostic.contains("key"));
        CHECK_FALSE(diagnostic.contains("value"));
    }
}

TEST_CASE("Preset transaction rejects malformed operation requests", "[PluginHost]")
{
    using Slic3r::PresetTransactionStatus;
    using nlohmann::json;

    const auto check_invalid = [](const json& request, const char* code) {
        const Slic3r::PresetTransactionResult result = Slic3r::parse_transaction_request(request);
        REQUIRE(result.status == PresetTransactionStatus::Invalid);
        REQUIRE(result.diagnostics.size() == 1);
        CHECK(result.diagnostics[0]["code"] == code);
        CHECK(result.diagnostics[0]["operation_index"].is_null());
    };

    check_invalid({{"expected_revision", "A"}, {"operations", json::array()}}, "empty_operations");
    check_invalid({{"operations", json::array({{{"op", "select"}}})}}, "missing_expected_revision");
    const Slic3r::PresetTransactionResult unsupported = Slic3r::parse_transaction_request(
        {{"expected_revision", "A"}, {"interaction", "silent"}, {"operations", json::array({{{"op", "select"}}})}});
    REQUIRE(unsupported.status == PresetTransactionStatus::Unsupported);
    REQUIRE(unsupported.diagnostics.size() == 1);
    CHECK(unsupported.diagnostics[0]["code"] == "unsupported_interaction");
    CHECK(unsupported.diagnostics[0]["operation_index"].is_null());

    const auto check_operation_invalid = [](const json& operation, const char* code) {
        const Slic3r::PresetTransactionResult result = Slic3r::parse_transaction_request(
            {{"expected_revision", "A"}, {"operations", json::array({operation})}});
        REQUIRE(result.status == PresetTransactionStatus::Invalid);
        REQUIRE(result.diagnostics.size() == 1);
        CHECK(result.diagnostics[0]["code"] == code);
        CHECK(result.diagnostics[0]["operation_index"] == 0);
    };

    check_operation_invalid({{"op", "delete"}}, "unknown_operation");
    check_operation_invalid({{"op", "select"}, {"preset_type", "resin"}, {"name", "A"}, {"scope", {{"slot", 0}}}},
                            "unknown_preset_type");
    check_operation_invalid({{"op", "select"}, {"preset_type", "filament"}, {"name", "A"}, {"scope", {{"edited", true}}}},
                            "invalid_scope");
    const Slic3r::PresetTransactionResult duplicate = Slic3r::parse_transaction_request(
        {{"expected_revision", "A"}, {"operations", json::array({
            {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}},
             {"values", {{"layer_height", "0.16"}}}},
            {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}},
             {"values", {{"layer_height", "0.20"}}}},
        })}});
    REQUIRE(duplicate.status == PresetTransactionStatus::Invalid);
    REQUIRE(duplicate.diagnostics.size() == 1);
    CHECK(duplicate.diagnostics[0]["code"] == "duplicate_key_assignment");
    CHECK(duplicate.diagnostics[0]["operation_index"] == 1);
}

TEST_CASE("Preset transaction parses select and set-values operations", "[PluginHost]")
{
    const Slic3r::PresetTransactionResult result = Slic3r::parse_transaction_request({
        {"expected_revision", "revision-A"},
        {"interaction", "confirm"},
        {"operations", nlohmann::json::array({
            {{"op", "select"}, {"preset_type", "filament"}, {"name", "Generic PLA"}, {"scope", {{"slot", 0}}}},
            {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}},
             {"values", {{"layer_height", "0.16"}}}},
        })},
    });

    REQUIRE(result.status == Slic3r::PresetTransactionStatus::Valid);
    REQUIRE(result.request.has_value());
    CHECK(result.request->expected_revision == "revision-A");
    CHECK(result.request->interaction == "confirm");
    REQUIRE(result.request->operations.size() == 2);
    CHECK(std::holds_alternative<Slic3r::SelectPresetOperation>(result.request->operations[0]));
    CHECK(std::holds_alternative<Slic3r::SetValuesOperation>(result.request->operations[1]));
}

TEST_CASE("Preset transaction rejects stale revision before callbacks", "[PluginHost]")
{
    int confirmation_calls = 0;
    int apply_calls = 0;
    const Slic3r::PresetTransactionResult result = Slic3r::validate_transaction_shape(
        {{"expected_revision", "A"}, {"operations", nlohmann::json::array({
            {{"op", "select"}, {"preset_type", "filament"}, {"name", "Generic PLA"}, {"scope", {{"slot", 0}}}},
        })}},
        "B",
        [&] { ++confirmation_calls; return true; },
        [&] { ++apply_calls; });

    CHECK(result.status == Slic3r::PresetTransactionStatus::Conflict);
    CHECK(result.affected.empty());
    CHECK(confirmation_calls == 0);
    CHECK(apply_calls == 0);
}

TEST_CASE("Plugin host API advertises stable versioned features", "[PluginHost][Python]")
{
    py::object host = import_orca_module().attr("host");

    REQUIRE(has_attr(host, "api_info"));
    py::dict api_info = host.attr("api_info")().cast<py::dict>();

    REQUIRE(api_info.contains("api_version"));
    CHECK(api_info["api_version"].cast<std::string>() == "1.0");
    REQUIRE(api_info.contains("host_version"));
    CHECK_FALSE(api_info["host_version"].cast<std::string>().empty());
    REQUIRE(api_info.contains("features"));
    py::list features = api_info["features"].cast<py::list>();
    REQUIRE(py::len(features) == 5);
    CHECK(features[0].cast<std::string>() == "plugin_storage.v1");
    CHECK(features[1].cast<std::string>() == "preset_snapshot.v1");
    CHECK(features[2].cast<std::string>() == "preset_transaction.v1");
    CHECK(features[3].cast<std::string>() == "preset_select.v1");
    CHECK(features[4].cast<std::string>() == "preset_values.v1");
    CHECK(has_attr(host, "v1"));
}
