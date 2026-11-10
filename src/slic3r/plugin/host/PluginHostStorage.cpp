#include "PluginHostStorage.hpp"

#include "slic3r/plugin/PluginAuditManager.hpp"
#include "slic3r/plugin/PluginDescriptor.hpp"

#include <libslic3r/Utils.hpp>

#include <boost/filesystem.hpp>

#include <stdexcept>

namespace Slic3r {
namespace {

namespace fs = boost::filesystem;

bool resolve_plugin_storage_root(fs::path& canonical_root, std::string& error)
{
    boost::system::error_code ec;
    const fs::path canonical_data_dir = fs::canonical(fs::path(data_dir()), ec);
    if (ec || !fs::is_directory(canonical_data_dir)) {
        error = "Failed to resolve the application data directory.";
        return false;
    }

    const fs::path root = fs::path(data_dir()) / "orca_plugins_data";
    if (fs::is_symlink(root, ec)) {
        error = "Plugin data directory must not be a symbolic link.";
        return false;
    }
    if (ec) {
        error = "Failed to inspect the plugin data directory: " + ec.message();
        return false;
    }
    if (!fs::exists(root, ec)) {
        if (ec) {
            error = "Failed to inspect the plugin data directory: " + ec.message();
            return false;
        }
        if (!fs::create_directory(root, ec) || ec) {
            error = "Failed to create the plugin data directory: " + ec.message();
            return false;
        }
    }

    canonical_root = fs::canonical(root, ec);
    if (ec || !fs::is_directory(canonical_root) || canonical_root.parent_path() != canonical_data_dir) {
        error = "Plugin data directory is not a real directory inside the application data directory.";
        return false;
    }

    return true;
}

bool resolve_plugin_storage_path(const std::string& plugin_key, const fs::path& canonical_root,
                                 fs::path& storage_path, std::string& error)
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

    storage_path = canonical_root / escaped_key;
    if (!is_inside_allowed_root(storage_path, canonical_root)) {
        error = "Plugin storage path escapes its root.";
        return false;
    }

    return true;
}

} // namespace

boost::filesystem::path plugin_storage_path(const std::string& plugin_key)
{
    fs::path canonical_root;
    fs::path storage_path;
    std::string error;
    if (!resolve_plugin_storage_root(canonical_root, error) ||
        !resolve_plugin_storage_path(plugin_key, canonical_root, storage_path, error))
        throw std::runtime_error(error);

    boost::system::error_code ec;
    fs::create_directory(storage_path, ec);
    if (ec)
        throw std::runtime_error("Failed to create plugin storage: " + ec.message());

    const fs::path canonical_storage = fs::canonical(storage_path, ec);
    if (ec || canonical_storage.parent_path() != canonical_root)
        throw std::runtime_error("Plugin storage path escapes its root.");

    return canonical_storage;
}

bool delete_plugin_storage(const std::string& plugin_key, std::string& error)
{
    fs::path canonical_root;
    fs::path storage_path;
    if (!resolve_plugin_storage_root(canonical_root, error) ||
        !resolve_plugin_storage_path(plugin_key, canonical_root, storage_path, error))
        return false;

    boost::system::error_code ec;
    if (!fs::exists(storage_path, ec)) {
        if (ec)
            error = "Failed to inspect plugin data: " + ec.message();
        return !ec;
    }

    const fs::path canonical_storage = fs::canonical(storage_path, ec);
    if (ec || canonical_storage.parent_path() != canonical_root) {
        error = "Refusing to delete plugin data outside the plugin data directory.";
        return false;
    }

    fs::remove_all(canonical_storage, ec);
    if (ec) {
        error = "Failed to delete plugin data " + canonical_storage.string() + ": " + ec.message();
        return false;
    }

    return true;
}

} // namespace Slic3r
