#include "PluginPresetTransactionDialog.hpp"

#include "GUI.hpp"
#include "GUI_App.hpp"
#include "I18N.hpp"

#include "slic3r/plugin/host/PluginPresetTransaction.hpp"

#include <wx/button.h>
#include <wx/listctrl.h>
#include <wx/sizer.h>
#include <wx/stattext.h>

namespace Slic3r {
namespace GUI {

namespace {

wxString preset_type_label(const std::string& preset_type)
{
    if (preset_type == "print")
        return _L("Process");
    if (preset_type == "filament")
        return _L("Filament");
    if (preset_type == "printer")
        return _L("Printer");
    return from_u8(preset_type);
}

} // namespace

PluginPresetTransactionDialog::PluginPresetTransactionDialog(wxWindow*                       parent,
                                                             const std::string&              plugin_name,
                                                             const PresetTransactionPreview& preview)
    : DPIDialog(parent, wxID_ANY, _L("Plugin preset changes"), wxDefaultPosition, wxDefaultSize,
                wxDEFAULT_DIALOG_STYLE | wxRESIZE_BORDER)
{
    const int em = em_unit();
    auto*     sizer = new wxBoxSizer(wxVERTICAL);

    const wxString requester = plugin_name.empty() ? _L("A plugin") : wxString::Format("\"%s\"", from_u8(plugin_name));
    auto* intro = new wxStaticText(this, wxID_ANY,
                                   wxString::Format(_L("%s wants to change the following settings:"), requester));
    sizer->Add(intro, 0, wxALL, em);

    auto* list = new wxListCtrl(this, wxID_ANY, wxDefaultPosition, wxSize(70 * em, 20 * em),
                                wxLC_REPORT | wxLC_SINGLE_SEL);
    list->AppendColumn(_L("Preset"), wxLIST_FORMAT_LEFT, 10 * em);
    list->AppendColumn(_L("Setting"), wxLIST_FORMAT_LEFT, 20 * em);
    list->AppendColumn(_L("Current"), wxLIST_FORMAT_LEFT, 18 * em);
    list->AppendColumn(_L("New"), wxLIST_FORMAT_LEFT, 18 * em);

    const auto add_row = [list](const wxString& preset, const wxString& setting, const std::string& current,
                                const std::string& proposed) {
        const long row = list->InsertItem(list->GetItemCount(), preset);
        list->SetItem(row, 1, setting);
        list->SetItem(row, 2, from_u8(current));
        list->SetItem(row, 3, from_u8(proposed));
    };
    for (const StagedSelection& selection : preview.selections)
        add_row(_L("Filament"), wxString::Format(_L("Filament slot %d"), int(selection.slot + 1)), selection.old_name,
                selection.new_name);
    for (const StagedValueChange& change : preview.values)
        add_row(preset_type_label(change.preset_type), from_u8(change.key), change.old_value, change.new_value);
    sizer->Add(list, 1, wxEXPAND | wxLEFT | wxRIGHT, em);

    auto* note = new wxStaticText(this, wxID_ANY,
                                  _L("Nothing is saved automatically. Changed presets are marked as modified, and you "
                                     "can save or revert them as usual."));
    note->Wrap(70 * em);
    sizer->Add(note, 0, wxALL, em);

    auto* buttons = new wxStdDialogButtonSizer();
    auto* apply   = new wxButton(this, wxID_OK, _L("Apply"));
    buttons->AddButton(apply);
    buttons->AddButton(new wxButton(this, wxID_CANCEL, _L("Cancel")));
    buttons->Realize();
    sizer->Add(buttons, 0, wxEXPAND | wxLEFT | wxRIGHT | wxBOTTOM, em);
    apply->SetDefault();

    SetSizerAndFit(sizer);
    CenterOnParent();
    wxGetApp().UpdateDlgDarkUI(this);
}

void PluginPresetTransactionDialog::on_dpi_changed(const wxRect& /*suggested_rect*/)
{
    Fit();
    Refresh();
}

} // namespace GUI
} // namespace Slic3r
