#ifndef slic3r_AIAssist_AIAssistDialog_hpp_
#define slic3r_AIAssist_AIAssistDialog_hpp_

#include "../GUI_Utils.hpp"

#include <wx/dialog.h>
#include <wx/textctrl.h>
#include <wx/listctrl.h>

#include <vector>

#include "PresetAnalyzer.hpp"

class Button;
class wxStaticText;

namespace Slic3r {
namespace GUI {
namespace AIAssist {

class AIAssistDialog : public DPIDialog
{
public:
    explicit AIAssistDialog(wxWindow *parent);
    void on_dpi_changed(const wxRect &suggested_rect) override;

private:
    void build_ui();
    void on_analyze();
    void on_fix_problem();
    void on_test_webcam();
    void on_test_telegram();
    void on_toggle_monitor();
    void on_apply();
    void fill_changes_list(const AnalysisResult &result);
    void set_busy(bool busy);
    void set_status(const wxString &msg);

    wxTextCtrl *  m_problem_input{nullptr};
    wxTextCtrl *  m_summary{nullptr};
    wxListCtrl *  m_changes{nullptr};
    wxStaticText *m_status{nullptr};
    Button *      m_btn_analyze{nullptr};
    Button *      m_btn_problem{nullptr};
    Button *      m_btn_apply{nullptr};
    Button *      m_btn_monitor{nullptr};

    AnalysisResult m_last;
};

void show_ai_assist_dialog(wxWindow *parent);

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r

#endif
