#pragma once

#include "GUI_Utils.hpp"

#include <string>

namespace Slic3r {

struct PresetTransactionPreview;

namespace GUI {

// Lists every preset change a plugin asked for and lets the user apply all of them or none.
class PluginPresetTransactionDialog : public DPIDialog
{
public:
    PluginPresetTransactionDialog(wxWindow* parent, const std::string& plugin_name, const PresetTransactionPreview& preview);

protected:
    void on_dpi_changed(const wxRect& suggested_rect) override;
};

} // namespace GUI
} // namespace Slic3r
