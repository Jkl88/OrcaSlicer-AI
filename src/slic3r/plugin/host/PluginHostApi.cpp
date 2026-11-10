#include "PluginHostApi.hpp"

#include "slic3r/plugin/PluginFsUtils.hpp"

#include <libslic3r/libslic3r.h>

namespace Slic3r {

namespace {

constexpr char API_VERSION[] = "1.0";
constexpr char FEATURE_PLUGIN_STORAGE[] = "plugin_storage.v1";
constexpr char FEATURE_PRESET_SNAPSHOT[] = "preset_snapshot.v1";
constexpr char FEATURE_PRESET_TRANSACTION[] = "preset_transaction.v1";
constexpr char FEATURE_PRESET_SELECT[] = "preset_select.v1";
constexpr char FEATURE_PRESET_VALUES[] = "preset_values.v1";

} // namespace

nlohmann::json PluginHostApi::api_info()
{
    return {
        { "api_version", API_VERSION },
        { "host_version", SLIC3R_VERSION },
        { "features", nlohmann::json::array({
            FEATURE_PLUGIN_STORAGE,
            FEATURE_PRESET_SNAPSHOT,
            FEATURE_PRESET_TRANSACTION,
            FEATURE_PRESET_SELECT,
            FEATURE_PRESET_VALUES,
        }) },
    };
}

void PluginHostApi::RegisterBindings(pybind11::module_& host)
{
    host.def("api_info", [] { return json_to_py(api_info()); }, "Return the versioned host API capabilities.");
    host.def_submodule("v1", "Version 1 host API namespace.");
}

} // namespace Slic3r
