#include <catch2/catch_all.hpp>

#include "python_test_support.hpp"
#include "plugin_test_utils.hpp"

#include <slic3r/plugin/PluginAuditManager.hpp>
#include <slic3r/plugin/host/PluginHostStorage.hpp>

#include <boost/filesystem.hpp>
#include <boost/nowide/fstream.hpp>
#include <pybind11/pybind11.h>

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
