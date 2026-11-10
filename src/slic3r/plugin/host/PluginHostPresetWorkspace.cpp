#include "PluginHostBindings.hpp"
#include "PluginPresetSnapshot.hpp"

#include "slic3r/plugin/PluginFsUtils.hpp"

#include <libslic3r/PresetBundle.hpp>
#include <slic3r/GUI/GUI_App.hpp>
#include <slic3r/GUI/Plater.hpp>

#include <pybind11/stl.h>

#include <future>
#include <stdexcept>
#include <type_traits>

#include <wx/app.h>

namespace py = pybind11;

namespace Slic3r {
namespace {

template<typename Fn>
auto run_on_ui_blocking(Fn&& fn) -> std::invoke_result_t<Fn&>
{
    using Result = std::invoke_result_t<Fn&>;
    if (wxTheApp == nullptr)
        throw std::runtime_error("OrcaSlicer application is not initialized");

    if (wxIsMainThread()) {
        py::gil_scoped_release release_gil;
        return fn();
    }

    std::promise<Result> promise;
    std::future<Result> future = promise.get_future();
    py::gil_scoped_release release_gil;
    GUI::wxGetApp().CallAfter([&promise, &fn]() {
        try {
            promise.set_value(fn());
        } catch (...) {
            promise.set_exception(std::current_exception());
        }
    });
    return future.get();
}

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

py::object snapshot(const py::dict& config_keys)
{
    const PresetSnapshotRequest request = parse_snapshot_request(config_keys);
    const PresetWorkspaceSnapshot detached = run_on_ui_blocking([&request] {
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

} // namespace

void host_bindings::register_preset_workspace(py::module_& host)
{
    py::module_ v1 = host.attr("v1").cast<py::module_>();
    py::module_ presets = v1.def_submodule("presets", "Immutable preset workspace snapshots.");
    presets.def("snapshot", &snapshot, py::arg("config_keys") = py::dict(),
                "Return a detached immutable snapshot of the live preset workspace.");
}

} // namespace Slic3r
