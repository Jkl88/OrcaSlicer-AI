#include "PluginHostBindings.hpp"
#include "PluginHostUiDispatch.hpp"
#include "PluginPresetSnapshot.hpp"
#include "PluginPresetTransaction.hpp"

#include "slic3r/plugin/PluginAuditManager.hpp"
#include "slic3r/plugin/PluginFsUtils.hpp"

#include <libslic3r/PresetBundle.hpp>
#include <slic3r/GUI/GUI_App.hpp>
#include <slic3r/GUI/MainFrame.hpp>
#include <slic3r/GUI/Plater.hpp>
#include <slic3r/GUI/PluginPresetTransactionDialog.hpp>
#include <slic3r/GUI/Tab.hpp>

#include <pybind11/stl.h>

#include <stdexcept>

namespace py = pybind11;

namespace Slic3r {
namespace {

Preset::Type preset_type_from_name(const std::string& name)
{
    if (name == "print")
        return Preset::TYPE_PRINT;
    if (name == "filament")
        return Preset::TYPE_FILAMENT;
    if (name == "printer")
        return Preset::TYPE_PRINTER;
    throw std::invalid_argument("Unknown preset type in config_keys: " + name);
}

PresetSnapshotRequest parse_snapshot_request(const py::dict& config_keys)
{
    PresetSnapshotRequest request;
    for (const auto& item : config_keys) {
        const std::string category = py::cast<std::string>(item.first);
        request.config_keys[preset_type_from_name(category)] = py::cast<std::vector<std::string>>(item.second);
    }
    return request;
}

std::string current_revision(const PresetBundle& bundle, const GUI::Plater& plater)
{
    return make_preset_snapshot(bundle, plater.is_project_dirty(), plater.is_presets_dirty(), {}).revision;
}

py::object snapshot(const py::dict& config_keys)
{
    const PresetSnapshotRequest request = parse_snapshot_request(config_keys);
    const PresetWorkspaceSnapshot detached = host_ui_dispatch::run_blocking([request] {
        PresetBundle* bundle = GUI::wxGetApp().preset_bundle;
        GUI::Plater* plater = GUI::wxGetApp().plater();
        if (bundle == nullptr)
            throw std::runtime_error("Preset bundle is not available");
        if (plater == nullptr)
            throw std::runtime_error("Plater is not available");
        return make_preset_snapshot(*bundle, plater->is_project_dirty(), plater->is_presets_dirty(), request);
    });
    return json_to_py(nlohmann::json(detached));
}

PresetTransactionResult unsupported(const std::string& message)
{
    PresetTransactionResult result;
    result.status  = PresetTransactionStatus::Unsupported;
    result.message = message;
    result.diagnostics.push_back({{"code", "host_unavailable"}, {"operation_index", nullptr}, {"field", nullptr},
                                  {"message", message}});
    return result;
}

PresetTransactionResult commit_on_ui(const nlohmann::json& request, const std::string& plugin_id)
{
    PresetBundle* bundle = GUI::wxGetApp().preset_bundle;
    GUI::Plater*  plater = GUI::wxGetApp().plater();
    if (bundle == nullptr || plater == nullptr || GUI::wxGetApp().mainframe == nullptr)
        return unsupported("The preset workspace is not available");

    PresetTransactionHooks hooks;
    hooks.confirm = [](const std::string& plugin, const PresetTransactionPreview& preview) {
        GUI::PluginPresetTransactionDialog dialog(GUI::wxGetApp().mainframe, plugin, preview);
        return dialog.ShowModal() == wxID_OK;
    };
    hooks.apply_config = [bundle](const std::string& preset_type, const DynamicPrintConfig& config) {
        const Preset::Type type = preset_type_from_name(preset_type);
        if (GUI::Tab* tab = GUI::wxGetApp().get_tab(type)) {
            tab->load_config(config);
            return;
        }
        PresetCollection& presets = type == Preset::TYPE_PRINT    ? bundle->prints :
                                    type == Preset::TYPE_FILAMENT ? bundle->filaments :
                                                                    bundle->printers;
        presets.get_edited_preset().config = config;
    };
    hooks.select_filament = [plater](size_t slot, const std::string& preset_name) {
        return plater->select_filament_preset_for_plugin(slot, preset_name);
    };

    PresetTransactionResult result = commit_preset_transaction(*bundle, current_revision(*bundle, *plater), plugin_id,
                                                               request, hooks);
    if (result.status == PresetTransactionStatus::Applied && !result.affected.empty()) {
        plater->on_config_change(bundle->full_config());
        result.revision = current_revision(*bundle, *plater);
    }
    return result;
}

py::object commit(const py::dict& request_dict)
{
    const nlohmann::json request = py_to_json(request_dict);
    const PresetTransactionResult parsed = parse_transaction_request(request);
    if (parsed.status != PresetTransactionStatus::Valid)
        return json_to_py(transaction_result_json(parsed));

    const std::string plugin_id = PluginAuditManager::instance().current_plugin();
    PresetTransactionResult result;
    try {
        result = host_ui_dispatch::run_blocking([request, plugin_id] { return commit_on_ui(request, plugin_id); },
                                                host_ui_dispatch::no_timeout);
    } catch (const std::exception& e) {
        result = unsupported(e.what());
    }
    return json_to_py(transaction_result_json(result));
}

} // namespace

void host_bindings::register_preset_workspace(py::module_& host)
{
    py::module_ v1 = host.attr("v1").cast<py::module_>();
    py::module_ presets = v1.def_submodule("presets", "Preset workspace snapshots and user-confirmed transactions.");
    presets.def("snapshot", &snapshot, py::arg("config_keys") = py::dict(),
                "Return a detached immutable snapshot of the live preset workspace.");
    presets.def("commit", &commit, py::arg("request"),
                "Validate a preset transaction, ask the user to confirm it and apply all operations or none. "
                "Returns a result dict with status, revision, affected and diagnostics; never raises for "
                "invalid requests.");
}

} // namespace Slic3r
