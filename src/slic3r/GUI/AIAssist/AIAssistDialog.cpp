#include "AIAssistDialog.hpp"

#include "AIConfig.hpp"
#include "GeminiClient.hpp"
#include "PrintMonitor.hpp"
#include "TelegramClient.hpp"
#include "WebcamCapture.hpp"

#include "slic3r/GUI/GUI.hpp"
#include "slic3r/GUI/GUI_App.hpp"
#include "slic3r/GUI/I18N.hpp"
#include "slic3r/GUI/MsgDialog.hpp"
#include "slic3r/GUI/Widgets/Button.hpp"
#include "slic3r/GUI/Widgets/Label.hpp"
#include "libslic3r/PresetBundle.hpp"

#include <wx/sizer.h>
#include <wx/stattext.h>
#include <wx/busyinfo.h>

#include <boost/log/trivial.hpp>

namespace Slic3r {
namespace GUI {
namespace AIAssist {

AIAssistDialog::AIAssistDialog(wxWindow *parent)
    : DPIDialog(parent, wxID_ANY, _L("AI Assist"), wxDefaultPosition, wxDefaultSize,
                wxDEFAULT_DIALOG_STYLE | wxRESIZE_BORDER)
{
    SetBackgroundColour(*wxWHITE);
    build_ui();
    wxGetApp().UpdateDlgDarkUI(this);
    SetMinSize(wxSize(FromDIP(720), FromDIP(560)));
    Fit();
    CenterOnParent();
}

void AIAssistDialog::on_dpi_changed(const wxRect &)
{
    Fit();
    Refresh();
}

void AIAssistDialog::build_ui()
{
    auto *root = new wxBoxSizer(wxVERTICAL);

    auto *hint = new wxStaticText(this, wxID_ANY,
        _L("Gemini анализирует текущие пресеты Orca и предлагает правки. "
           "Опишите дефект (слоновая нога, задирает углы и т.п.) — модель скорректирует настройки печати/пластика."));
    hint->Wrap(FromDIP(680));
    root->Add(hint, 0, wxEXPAND | wxALL, FromDIP(12));

    auto *row_btns = new wxBoxSizer(wxHORIZONTAL);
    m_btn_analyze = new Button(this, _L("Analyze settings"));
    m_btn_analyze->SetStyle(ButtonStyle::Confirm, ButtonType::Window);
    m_btn_analyze->Bind(wxEVT_BUTTON, [this](wxCommandEvent &) { on_analyze(); });
    row_btns->Add(m_btn_analyze, 0, wxRIGHT, FromDIP(8));

    m_btn_problem = new Button(this, _L("Fix by problem description"));
    m_btn_problem->SetStyle(ButtonStyle::Regular, ButtonType::Window);
    m_btn_problem->Bind(wxEVT_BUTTON, [this](wxCommandEvent &) { on_fix_problem(); });
    row_btns->Add(m_btn_problem, 0, wxRIGHT, FromDIP(8));

    auto *btn_cam = new Button(this, _L("Test webcam"));
    btn_cam->SetStyle(ButtonStyle::Regular, ButtonType::Window);
    btn_cam->Bind(wxEVT_BUTTON, [this](wxCommandEvent &) { on_test_webcam(); });
    row_btns->Add(btn_cam, 0, wxRIGHT, FromDIP(8));

    auto *btn_tg = new Button(this, _L("Test Telegram"));
    btn_tg->SetStyle(ButtonStyle::Regular, ButtonType::Window);
    btn_tg->Bind(wxEVT_BUTTON, [this](wxCommandEvent &) { on_test_telegram(); });
    row_btns->Add(btn_tg, 0, wxRIGHT, FromDIP(8));

    m_btn_monitor = new Button(this, _L("Start monitor"));
    m_btn_monitor->SetStyle(ButtonStyle::Regular, ButtonType::Window);
    m_btn_monitor->Bind(wxEVT_BUTTON, [this](wxCommandEvent &) { on_toggle_monitor(); });
    row_btns->Add(m_btn_monitor, 0);
    root->Add(row_btns, 0, wxLEFT | wxRIGHT | wxBOTTOM, FromDIP(12));

    if (PrintMonitor::instance().running())
        m_btn_monitor->SetLabel(_L("Stop monitor"));

    auto *lbl_prob = new wxStaticText(this, wxID_ANY, _L("Problem description"));
    root->Add(lbl_prob, 0, wxLEFT | wxRIGHT, FromDIP(12));
    m_problem_input = new wxTextCtrl(this, wxID_ANY, wxEmptyString, wxDefaultPosition,
                                     wxSize(-1, FromDIP(72)), wxTE_MULTILINE);
    root->Add(m_problem_input, 0, wxEXPAND | wxLEFT | wxRIGHT | wxBOTTOM, FromDIP(12));

    auto *lbl_sum = new wxStaticText(this, wxID_ANY, _L("AI summary"));
    root->Add(lbl_sum, 0, wxLEFT | wxRIGHT, FromDIP(12));
    m_summary = new wxTextCtrl(this, wxID_ANY, wxEmptyString, wxDefaultPosition,
                               wxSize(-1, FromDIP(90)), wxTE_MULTILINE | wxTE_READONLY);
    root->Add(m_summary, 0, wxEXPAND | wxLEFT | wxRIGHT | wxBOTTOM, FromDIP(12));

    m_changes = new wxListCtrl(this, wxID_ANY, wxDefaultPosition, wxDefaultSize,
                               wxLC_REPORT | wxLC_SINGLE_SEL);
    m_changes->AppendColumn(_L("Category"), wxLIST_FORMAT_LEFT, FromDIP(90));
    m_changes->AppendColumn(_L("Key"), wxLIST_FORMAT_LEFT, FromDIP(180));
    m_changes->AppendColumn(_L("New value"), wxLIST_FORMAT_LEFT, FromDIP(120));
    m_changes->AppendColumn(_L("Reason"), wxLIST_FORMAT_LEFT, FromDIP(260));
    root->Add(m_changes, 1, wxEXPAND | wxLEFT | wxRIGHT, FromDIP(12));

    auto *row_apply = new wxBoxSizer(wxHORIZONTAL);
    m_btn_apply = new Button(this, _L("Apply selected changes"));
    m_btn_apply->SetStyle(ButtonStyle::Confirm, ButtonType::Window);
    m_btn_apply->Bind(wxEVT_BUTTON, [this](wxCommandEvent &) { on_apply(); });
    row_apply->Add(m_btn_apply, 0, wxRIGHT, FromDIP(12));

    m_status = new wxStaticText(this, wxID_ANY, wxEmptyString);
    row_apply->Add(m_status, 1, wxALIGN_CENTER_VERTICAL);
    root->Add(row_apply, 0, wxEXPAND | wxALL, FromDIP(12));

    SetSizer(root);
}

void AIAssistDialog::set_busy(bool busy)
{
    m_btn_analyze->Enable(!busy);
    m_btn_problem->Enable(!busy);
    m_btn_apply->Enable(!busy);
}

void AIAssistDialog::set_status(const wxString &msg)
{
    if (m_status)
        m_status->SetLabel(msg);
}

void AIAssistDialog::fill_changes_list(const AnalysisResult &result)
{
    m_changes->DeleteAllItems();
    m_summary->SetValue(from_u8(result.summary));
    long i = 0;
    for (const auto &ch : result.changes) {
        long idx = m_changes->InsertItem(i++, from_u8(ch.category));
        m_changes->SetItem(idx, 1, from_u8(ch.key));
        m_changes->SetItem(idx, 2, from_u8(ch.new_value));
        m_changes->SetItem(idx, 3, from_u8(ch.reason));
    }
    set_status(wxString::Format(_L("Suggestions: %d"), (int) result.changes.size()));
}

void AIAssistDialog::on_analyze()
{
    auto *cfg = wxGetApp().app_config;
    if (get(cfg, KEY_GEMINI_API_KEY).empty()) {
        MessageDialog(this, _L("Set Gemini API key in Preferences → AI Assist."),
                      _L("AI Assist"), wxOK | wxICON_WARNING).ShowModal();
        return;
    }

    set_busy(true);
    set_status(_L("Requesting Gemini…"));
    wxBusyInfo busy(_L("Analyzing settings with Gemini…"), this);

    const auto settings = PresetAnalyzer::dump_current_settings(wxGetApp().preset_bundle);
    GeminiClient client(cfg);
    auto gr = client.generate_text(
        PresetAnalyzer::system_prompt_analyze(),
        std::string("Current OrcaSlicer settings JSON:\n") + settings.dump(2));

    set_busy(false);
    if (!gr.ok) {
        set_status(from_u8(gr.error));
        MessageDialog(this, from_u8(gr.error), _L("Gemini error"), wxOK | wxICON_ERROR).ShowModal();
        return;
    }

    m_last = PresetAnalyzer::parse_ai_response(gr.text);
    if (!m_last.ok) {
        set_status(from_u8(m_last.error));
        MessageDialog(this, from_u8(m_last.error + "\n\n" + gr.text.substr(0, 800)),
                      _L("Parse error"), wxOK | wxICON_ERROR).ShowModal();
        return;
    }
    fill_changes_list(m_last);
}

void AIAssistDialog::on_fix_problem()
{
    const std::string problem = into_u8(m_problem_input->GetValue());
    if (problem.empty()) {
        MessageDialog(this, _L("Describe the problem first (e.g. elephant foot, corners lifting)."),
                      _L("AI Assist"), wxOK | wxICON_INFORMATION).ShowModal();
        return;
    }
    auto *cfg = wxGetApp().app_config;
    if (get(cfg, KEY_GEMINI_API_KEY).empty()) {
        MessageDialog(this, _L("Set Gemini API key in Preferences → AI Assist."),
                      _L("AI Assist"), wxOK | wxICON_WARNING).ShowModal();
        return;
    }

    set_busy(true);
    set_status(_L("Requesting Gemini…"));
    wxBusyInfo busy(_L("Diagnosing problem with Gemini…"), this);

    const auto settings = PresetAnalyzer::dump_current_settings(wxGetApp().preset_bundle);
    GeminiClient client(cfg);
    auto gr = client.generate_text(
        PresetAnalyzer::system_prompt_problem(),
        std::string("Problem:\n") + problem + "\n\nCurrent settings JSON:\n" + settings.dump(2));

    set_busy(false);
    if (!gr.ok) {
        set_status(from_u8(gr.error));
        MessageDialog(this, from_u8(gr.error), _L("Gemini error"), wxOK | wxICON_ERROR).ShowModal();
        return;
    }

    m_last = PresetAnalyzer::parse_ai_response(gr.text);
    if (!m_last.ok) {
        set_status(from_u8(m_last.error));
        MessageDialog(this, from_u8(m_last.error), _L("Parse error"), wxOK | wxICON_ERROR).ShowModal();
        return;
    }
    fill_changes_list(m_last);
}

void AIAssistDialog::on_apply()
{
    if (m_last.changes.empty()) {
        MessageDialog(this, _L("No changes to apply."), _L("AI Assist"), wxOK).ShowModal();
        return;
    }
    std::string err;
    const int n = PresetAnalyzer::apply_changes(m_last.changes, err);
    if (n <= 0) {
        MessageDialog(this, from_u8(err.empty() ? "Apply failed" : err),
                      _L("AI Assist"), wxOK | wxICON_ERROR).ShowModal();
        return;
    }
    set_status(wxString::Format(_L("Applied %d setting(s). Review and slice again."), n));
    MessageDialog(this,
                  wxString::Format(_L("Applied %d setting(s) to the edited presets."), n),
                  _L("AI Assist"), wxOK | wxICON_INFORMATION).ShowModal();
}

void AIAssistDialog::on_test_webcam()
{
    WebcamCapture cam(wxGetApp().app_config);
    auto snap = cam.grab();
    if (!snap.ok) {
        MessageDialog(this, from_u8(snap.error), _L("Webcam"), wxOK | wxICON_ERROR).ShowModal();
        return;
    }
    MessageDialog(this,
                  wxString::Format(_L("Snapshot OK: %d bytes (%s)"),
                                   (int) snap.bytes.size(), from_u8(snap.mime_type)),
                  _L("Webcam"), wxOK | wxICON_INFORMATION).ShowModal();
}

void AIAssistDialog::on_test_telegram()
{
    TelegramClient tg(wxGetApp().app_config);
    auto r = tg.send_message("OrcaSlicer AI Assist: test message");
    if (!r.ok) {
        MessageDialog(this, from_u8(r.error), _L("Telegram"), wxOK | wxICON_ERROR).ShowModal();
        return;
    }
    MessageDialog(this, _L("Telegram test message sent."), _L("Telegram"),
                  wxOK | wxICON_INFORMATION).ShowModal();
}

void AIAssistDialog::on_toggle_monitor()
{
    auto *cfg = wxGetApp().app_config;
    auto &mon = PrintMonitor::instance();
    if (mon.running()) {
        mon.stop();
        set_bool(cfg, KEY_MONITOR_ENABLED, false);
        cfg->save();
        m_btn_monitor->SetLabel(_L("Start monitor"));
        set_status(_L("Monitor stopped"));
        return;
    }
    set_bool(cfg, KEY_MONITOR_ENABLED, true);
    cfg->save();
    mon.start(cfg);
    m_btn_monitor->SetLabel(_L("Stop monitor"));
    set_status(_L("Monitor running (webcam → Gemini → Telegram)"));
}

void show_ai_assist_dialog(wxWindow *parent)
{
    AIAssistDialog dlg(parent);
    dlg.ShowModal();
}

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r
