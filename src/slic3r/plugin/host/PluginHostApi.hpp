#pragma once

#include <nlohmann/json.hpp>
#include <pybind11/pybind11.h>

namespace Slic3r {

class PluginHostApi
{
public:
    static nlohmann::json api_info();
    static void RegisterBindings(pybind11::module_& host);
};

} // namespace Slic3r
