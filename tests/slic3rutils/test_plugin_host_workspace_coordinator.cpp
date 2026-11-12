#include <catch2/catch_all.hpp>

#include <slic3r/plugin/host/PluginPresetTransaction.hpp>
#include <libslic3r/PresetBundle.hpp>

#include <nlohmann/json.hpp>

#include <stdexcept>

using nlohmann::json;
using Slic3r::PresetTransactionStatus;

namespace {

void add_filament_preset(Slic3r::PresetBundle& bundle, const std::string& name, bool compatible = true)
{
    Slic3r::Preset& preset = bundle.filaments.load_preset(std::string(), name, bundle.filaments.default_preset().config,
                                                         /*select=*/false);
    preset.is_compatible = compatible;
}

json set_print_values(json values)
{
    return {{"op", "set_values"}, {"preset_type", "print"}, {"scope", {{"edited", true}}}, {"values", std::move(values)}};
}

json select_filament(const std::string& name, size_t slot)
{
    return {{"op", "select"}, {"preset_type", "filament"}, {"name", name}, {"scope", {{"slot", slot}}}};
}

json request_with(json operations, const std::string& revision = "rev-A")
{
    return {{"expected_revision", revision}, {"interaction", "require_user_confirmation"}, {"operations", operations}};
}

struct RecordingConfirm
{
    int                              calls = 0;
    std::string                      plugin_id;
    Slic3r::PresetTransactionPreview preview;
    bool                             answer = true;

    Slic3r::PresetTransactionHooks hooks()
    {
        Slic3r::PresetTransactionHooks hooks;
        hooks.confirm = [this](const std::string& id, const Slic3r::PresetTransactionPreview& p) {
            ++calls;
            plugin_id = id;
            preview   = p;
            return answer;
        };
        return hooks;
    }
};

} // namespace

TEST_CASE("Preset transaction coordinator confirms after validation", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    bundle.prints.get_edited_preset().config.set("layer_height", 0.20, true);
    RecordingConfirm confirm;

    const Slic3r::PresetTransactionResult result = Slic3r::commit_preset_transaction(
        bundle, "rev-A", "test-plugin", request_with({set_print_values({{"layer_height", "0.16"}})}), confirm.hooks());

    CHECK(result.status == PresetTransactionStatus::Applied);
    CHECK(confirm.calls == 1);
    CHECK(confirm.plugin_id == "test-plugin");
    REQUIRE(confirm.preview.values.size() == 1);
    CHECK(confirm.preview.values[0].key == "layer_height");
    CHECK(confirm.preview.values[0].old_value == "0.2");
    CHECK(confirm.preview.values[0].new_value == "0.16");
    CHECK(bundle.prints.get_edited_preset().config.opt_serialize("layer_height") == "0.16");
    REQUIRE(result.affected.size() == 1);
    CHECK(result.affected[0]["key"] == "layer_height");
}

TEST_CASE("Preset transaction coordinator leaves presets untouched on cancellation", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    bundle.prints.get_edited_preset().config.set("layer_height", 0.20, true);
    RecordingConfirm confirm;
    confirm.answer = false;

    const Slic3r::PresetTransactionResult result = Slic3r::commit_preset_transaction(
        bundle, "rev-A", "test-plugin", request_with({set_print_values({{"layer_height", "0.16"}})}), confirm.hooks());

    CHECK(result.status == PresetTransactionStatus::Cancelled);
    CHECK(bundle.prints.get_edited_preset().config.opt_serialize("layer_height") == "0.2");
}

TEST_CASE("Preset transaction coordinator rejects stale revision before confirm", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    RecordingConfirm confirm;

    const Slic3r::PresetTransactionResult result =
        Slic3r::commit_preset_transaction(bundle, "rev-B", "test-plugin",
                                          request_with({set_print_values({{"layer_height", "0.16"}})}, "rev-A"),
                                          confirm.hooks());

    CHECK(result.status == PresetTransactionStatus::Conflict);
    CHECK(result.revision == "rev-B");
    CHECK(result.affected.empty());
    CHECK(confirm.calls == 0);
}

TEST_CASE("Preset transaction coordinator validates every operation before confirm", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    bundle.prints.get_edited_preset().config.set("layer_height", 0.20, true);
    RecordingConfirm confirm;

    const Slic3r::PresetTransactionResult result = Slic3r::commit_preset_transaction(
        bundle, "rev-A", "test-plugin",
        request_with({set_print_values({{"layer_height", "0.16"}, {"unknown_key", "1"}})}), confirm.hooks());

    CHECK(result.status == PresetTransactionStatus::Invalid);
    CHECK(confirm.calls == 0);
    CHECK(bundle.prints.get_edited_preset().config.opt_serialize("layer_height") == "0.2");
}

TEST_CASE("Preset transaction coordinator rolls back when applying throws", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    bundle.prints.get_edited_preset().config.set("layer_height", 0.20, true);
    bundle.printers.get_edited_preset().config.set("printer_model", "Before", true);
    RecordingConfirm               confirm;
    Slic3r::PresetTransactionHooks hooks = confirm.hooks();
    hooks.apply_config = [&bundle](const std::string& type, const Slic3r::DynamicPrintConfig& config) {
        if (type == "printer" && config.opt_serialize("printer_model") == "After")
            throw std::runtime_error("injected failure");
        (type == "print" ? bundle.prints : bundle.printers).get_edited_preset().config = config;
    };

    const Slic3r::PresetTransactionResult result = Slic3r::commit_preset_transaction(
        bundle, "rev-A", "test-plugin",
        request_with({set_print_values({{"layer_height", "0.16"}}),
                      {{"op", "set_values"}, {"preset_type", "printer"}, {"scope", {{"edited", true}}},
                       {"values", {{"printer_model", "After"}}}}}),
        hooks);

    CHECK(result.status == PresetTransactionStatus::Error);
    CHECK(bundle.prints.get_edited_preset().config.opt_serialize("layer_height") == "0.2");
    CHECK(bundle.printers.get_edited_preset().config.opt_serialize("printer_model") == "Before");
}

TEST_CASE("Preset transaction coordinator selects filament slots after confirmation", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    add_filament_preset(bundle, "Generic PLA");
    add_filament_preset(bundle, "Generic PETG");
    bundle.filament_presets = {"Generic PLA", "Generic PLA"};
    RecordingConfirm confirm;

    const Slic3r::PresetTransactionResult result = Slic3r::commit_preset_transaction(
        bundle, "rev-A", "qr-plugin", request_with({select_filament("Generic PETG", 1)}), confirm.hooks());

    CHECK(result.status == PresetTransactionStatus::Applied);
    REQUIRE(confirm.preview.selections.size() == 1);
    CHECK(confirm.preview.selections[0].slot == 1);
    CHECK(confirm.preview.selections[0].old_name == "Generic PLA");
    CHECK(confirm.preview.selections[0].new_name == "Generic PETG");
    CHECK(bundle.filament_presets[0] == "Generic PLA");
    CHECK(bundle.filament_presets[1] == "Generic PETG");
    REQUIRE(result.affected.size() == 1);
    CHECK(result.affected[0]["slot"] == 1);
    CHECK(result.affected[0]["name"] == "Generic PETG");
}

TEST_CASE("Preset transaction coordinator rejects invalid filament selections before confirm", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    add_filament_preset(bundle, "Generic PLA");
    add_filament_preset(bundle, "Other Printer PLA", false);
    bundle.filament_presets = {"Generic PLA"};
    RecordingConfirm confirm;

    const auto commit = [&](json operation) {
        return Slic3r::commit_preset_transaction(bundle, "rev-A", "qr-plugin", request_with({operation}), confirm.hooks());
    };

    CHECK(commit(select_filament("Generic PLA", 3)).status == PresetTransactionStatus::InvalidSlot);
    CHECK(commit(select_filament("Missing", 0)).status == PresetTransactionStatus::NotFound);
    CHECK(commit(select_filament("Other Printer PLA", 0)).status == PresetTransactionStatus::Incompatible);
    CHECK(confirm.calls == 0);
    CHECK(bundle.filament_presets[0] == "Generic PLA");
}

TEST_CASE("Preset transaction coordinator restores slots when a later selection fails", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    add_filament_preset(bundle, "Generic PLA");
    add_filament_preset(bundle, "Generic PETG");
    bundle.filament_presets = {"Generic PLA", "Generic PLA"};
    RecordingConfirm               confirm;
    Slic3r::PresetTransactionHooks hooks = confirm.hooks();
    hooks.select_filament = [&bundle](size_t slot, const std::string& name) {
        if (slot == 1 && name == "Generic PETG") {
            Slic3r::FilamentSelectionResult cancelled;
            cancelled.status = PresetTransactionStatus::Cancelled;
            return cancelled;
        }
        return Slic3r::select_filament_preset(bundle, slot, name);
    };

    const Slic3r::PresetTransactionResult result = Slic3r::commit_preset_transaction(
        bundle, "rev-A", "qr-plugin",
        request_with({select_filament("Generic PETG", 0), select_filament("Generic PETG", 1)}), hooks);

    CHECK(result.status == PresetTransactionStatus::Cancelled);
    CHECK(bundle.filament_presets[0] == "Generic PLA");
    CHECK(bundle.filament_presets[1] == "Generic PLA");
}

TEST_CASE("Preset transaction coordinator refuses mixing filament select and filament values", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    add_filament_preset(bundle, "Generic PLA");
    bundle.filament_presets = {"Generic PLA"};
    RecordingConfirm confirm;

    const Slic3r::PresetTransactionResult result = Slic3r::commit_preset_transaction(
        bundle, "rev-A", "test-plugin",
        request_with({select_filament("Generic PLA", 0),
                      {{"op", "set_values"}, {"preset_type", "filament"}, {"scope", {{"edited", true}}},
                       {"values", {{"filament_colour", "#00FF00"}}}}}),
        confirm.hooks());

    CHECK(result.status == PresetTransactionStatus::Invalid);
    REQUIRE(result.diagnostics.size() == 1);
    CHECK(result.diagnostics[0]["code"] == "conflicting_operations");
    CHECK(confirm.calls == 0);
}

TEST_CASE("Preset transaction coordinator skips confirmation when nothing changes", "[PluginHost]")
{
    Slic3r::PresetBundle bundle;
    add_filament_preset(bundle, "Generic PLA");
    bundle.filament_presets = {"Generic PLA"};
    RecordingConfirm confirm;

    const Slic3r::PresetTransactionResult result = Slic3r::commit_preset_transaction(
        bundle, "rev-A", "qr-plugin", request_with({select_filament("Generic PLA", 0)}), confirm.hooks());

    CHECK(result.status == PresetTransactionStatus::Applied);
    CHECK(result.affected.empty());
    CHECK(confirm.calls == 0);
}

TEST_CASE("Preset transaction results serialize to stable status strings", "[PluginHost]")
{
    Slic3r::PresetTransactionResult result;
    result.status   = PresetTransactionStatus::InvalidSlot;
    result.revision = "rev-A";
    result.message  = "out of range";

    const json serialized = Slic3r::transaction_result_json(result);
    CHECK(serialized["status"] == "invalid_slot");
    CHECK(serialized["revision"] == "rev-A");
    CHECK(serialized["message"] == "out of range");
    CHECK(serialized["affected"].is_array());
    CHECK(serialized["diagnostics"].is_array());
}
