#include "PluginHostStorage.hpp"

#include "slic3r/plugin/PluginAuditManager.hpp"
#include "slic3r/plugin/PluginDescriptor.hpp"

#include <libslic3r/Utils.hpp>

#include <boost/filesystem.hpp>

#include <stdexcept>

namespace Slic3r {
namespace {

namespace fs = boost::filesystem;

fs::path plugin_storage_root()
{
    return fs::path(data_dir()) / "orca_plugins_data";
}

bool resolve_plugin_storage_path(const std::string& plugin_key, fs::path& storage_path, std::string& error)
{
    if (plugin_key.empty()) {
        error = "Plugin storage requires an active plugin context.";
        return false;
    }

    const std::string escaped_key = filesystem_safe_escape(plugin_key);
    if (escaped_key.empty() || escaped_key == "." || escaped_key == "..") {
        error = "Plugin storage identity is invalid.";
        return false;
    }

    const fs::path root = plugin_storage_root();
    storage_path = root / escaped_key;
    if (!is_inside_allowed_root(storage_path, root)) {
        error = "Plugin storage path escapes its root.";
        return false;
    }

    return true;
}

} // namespace

boost::filesystem::path plugin_storage_path(const std::string& plugin_key)
{
    fs::path storage_path;
    std::string error;
    if (!resolve_plugin_storage_path(plugin_key, storage_path, error))
        throw std::runtime_error(error);

    boost::system::error_code ec;
    fs::create_directories(storage_path, ec);
    if (ec)
        throw std::runtime_error("Failed to create plugin storage: " + ec.message());

    const fs::path canonical_storage = fs::canonical(storage_path, ec);
    if (ec || !is_inside_allowed_root(canonical_storage, plugin_storage_root()))
        throw std::runtime_error("Plugin storage path escapes its root.");

    return canonical_storage;
}

bool delete_plugin_storage(const std::string& plugin_key, std::string& error)
{
    fs::path storage_path;
    if (!resolve_plugin_storage_path(plugin_key, storage_path, error))
        return false;

    boost::system::error_code ec;
    if (!fs::exists(storage_path, ec)) {
        if (ec)
            error = "Failed to inspect plugin data: " + ec.message();
        return !ec;
    }

    const fs::path canonical_storage = fs::canonical(storage_path, ec);
    if (ec || !is_inside_allowed_root(canonical_storage, plugin_storage_root())) {
        error = "Refusing to delete plugin data outside the plugin data directory.";
        return false;
    }

    fs::remove_all(storage_path, ec);
    if (ec) {
        error = "Failed to delete plugin data " + storage_path.string() + ": " + ec.message();
        return false;
    }

    return true;
}

} // namespace Slic3r
