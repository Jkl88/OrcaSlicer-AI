#pragma once

#include <boost/filesystem/path.hpp>

#include <string>

namespace Slic3r {

// Returns the private data directory for plugin_key, creating it when necessary.
// The caller must establish the plugin audit context before exposing this path to Python.
boost::filesystem::path plugin_storage_path(const std::string& plugin_key);

// Removes only plugin_key's private data directory. Missing data is not an error.
bool delete_plugin_storage(const std::string& plugin_key, std::string& error);

} // namespace Slic3r
