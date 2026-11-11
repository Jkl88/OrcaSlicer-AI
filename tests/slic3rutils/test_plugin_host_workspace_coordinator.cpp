#include <catch2/catch_all.hpp>

#include "python_test_support.hpp"
#include "plugin_test_utils.hpp"

#include <slic3r/plugin/host/PluginPresetTransaction.hpp>
#include <libslic3r/PresetBundle.hpp>

#include <nlohmann/json.hpp>

TEST_CASE("Preset transaction coordinator confirms after validation", "[PluginHost]")
{
    using nlohmann::json;
    Slic3r::PresetBundle bundle;
    bundle.prints.get_edited_preset().config.set("layer_height", 0.20, true);

    std::string plugin_identity;
    std::vector<Slic3r::StagedValueChange> confirmed_changes;
    bool confirmation_called = false;

    auto confirm_callback = [&](const std::string& plugin_id, const std::vector<Slic3r::StagedValueChange>& changes) {
        confirmation_called = true;
        plugin_identity = plugin_id;
        confirmed_changes = changes;
        return true;
    };

    const json request = {
        {"expected_revision", "test-revision"},
        {"operations", json::array({
            {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}},
             {"values", {{"layer_height", "0.16"}}}}
        })}
    };

    const Slic3r::PresetTransactionResult result =
        Slic3r::commit_preset_transaction_for_test(bundle, "test-revision", "test-plugin", request, confirm_callback);

    CHECK(confirmation_called);
    CHECK(plugin_identity == "test-plugin");
    REQUIRE(confirmed_changes.size() == 1);
    CHECK(confirmed_changes[0].key == "layer_height");
    CHECK(confirmed_changes[0].old_value == "0.2");
    CHECK(confirmed_changes[0].new_value == "0.16");
    CHECK(result.status == Slic3r::PresetTransactionStatus::Applied);
}

TEST_CASE("Preset transaction coordinator handles cancellation", "[PluginHost]")
{
    using nlohmann::json;
    Slic3r::PresetBundle bundle;
    bundle.prints.get_edited_preset().config.set("layer_height", 0.20, true);

    auto cancel_callback = [](const std::string&, const std::vector<Slic3r::StagedValueChange>&) {
        return false;
    };

    const json request = {
        {"expected_revision", "test-revision"},
        {"operations", json::array({
            {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}},
             {"values", {{"layer_height", "0.16"}}}}
        })}
    };

    const Slic3r::PresetTransactionResult result =
        Slic3r::commit_preset_transaction_for_test(bundle, "test-revision", "test-plugin", request, cancel_callback);

    CHECK(result.status == Slic3r::PresetTransactionStatus::Cancelled);
    CHECK(bundle.prints.get_edited_preset().config.opt_serialize("layer_height") == "0.2");
}

TEST_CASE("Preset transaction coordinator rejects stale revision before confirm", "[PluginHost]")
{
    using nlohmann::json;
    Slic3r::PresetBundle bundle;

    bool confirmation_called = false;
    auto confirm_callback = [&](const std::string&, const std::vector<Slic3r::StagedValueChange>&) {
        confirmation_called = true;
        return true;
    };

    const json request = {
        {"expected_revision", "old-revision"},
        {"operations", json::array({
            {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}},
             {"values", {{"layer_height", "0.16"}}}}
        })}
    };

    const Slic3r::PresetTransactionResult result =
        Slic3r::commit_preset_transaction_for_test(bundle, "new-revision", "test-plugin", request, confirm_callback);

    CHECK_FALSE(confirmation_called);
    CHECK(result.status == Slic3r::PresetTransactionStatus::Conflict);
    CHECK(result.revision == "new-revision");
}

TEST_CASE("Preset transaction coordinator validates before confirm", "[PluginHost]")
{
    using nlohmann::json;
    Slic3r::PresetBundle bundle;

    bool confirmation_called = false;
    auto confirm_callback = [&](const std::string&, const std::vector<Slic3r::StagedValueChange>&) {
        confirmation_called = true;
        return true;
    };

    const json request = {
        {"expected_revision", "test-revision"},
        {"operations", json::array({
            {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}},
             {"values", {{"unknown_key", "invalid"}}}}
        })}
    };

    const Slic3r::PresetTransactionResult result =
        Slic3r::commit_preset_transaction_for_test(bundle, "test-revision", "test-plugin", request, confirm_callback);

    CHECK_FALSE(confirmation_called);
    CHECK(result.status == Slic3r::PresetTransactionStatus::Invalid);
}

TEST_CASE("Preset transaction coordinator handles apply exception with rollback", "[PluginHost]")
{
    using nlohmann::json;
    Slic3r::PresetBundle bundle;
    bundle.prints.get_edited_preset().config.set("layer_height", 0.20, true);
    bundle.filaments.get_edited_preset().config.set("filament_colour", "#FF0000", true);

    auto confirm_callback = [](const std::string&, const std::vector<Slic3r::StagedValueChange>&) {
        return true;
    };

    const json request = {
        {"expected_revision", "test-revision"},
        {"operations", json::array({
            {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}},
             {"values", {{"layer_height", "0.16"}}}},
            {{"op", "set_values"}, {"preset_type", "filament"}, {"scope", {{"edited", true}}},
             {"values", {{"filament_colour", "#00FF00"}}}}
        })}
    };

    const Slic3r::PresetTransactionResult result =
        Slic3r::commit_preset_transaction_for_test(bundle, "test-revision", "test-plugin", request, confirm_callback, true);

    CHECK(result.status == Slic3r::PresetTransactionStatus::Error);
    CHECK(bundle.prints.get_edited_preset().config.opt_serialize("layer_height") == "0.2");
    CHECK(bundle.filaments.get_edited_preset().config.opt_serialize("filament_colour") == "#FF0000");
}
