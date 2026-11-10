#include <catch2/catch_all.hpp>

#include "python_test_support.hpp"

#include <pybind11/pybind11.h>

#include <string>

namespace py = pybind11;

namespace {

bool has_attr(const py::handle& object, const char* name)
{
    return py::hasattr(object, name);
}

} // namespace

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
