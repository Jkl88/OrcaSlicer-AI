#include "PresetAnalyzer.hpp"

#include "slic3r/GUI/GUI_App.hpp"
#include "slic3r/GUI/Tab.hpp"
#include "libslic3r/PresetBundle.hpp"
#include "libslic3r/PrintConfig.hpp"

#include <boost/algorithm/string.hpp>
#include <boost/log/trivial.hpp>

namespace Slic3r {
namespace GUI {
namespace AIAssist {

using json = nlohmann::json;

namespace {

// Ключи, которые чаще всего правят при дефектах печати — чтобы не раздувать промпт.
const std::vector<std::string> k_print_keys = {
    "layer_height", "initial_layer_print_height", "wall_loops", "top_shell_layers", "bottom_shell_layers",
    "sparse_infill_density", "sparse_infill_pattern", "print_speed", "outer_wall_speed", "inner_wall_speed",
    "sparse_infill_speed", "top_surface_speed", "initial_layer_speed", "travel_speed",
    "elephant_foot_compensation", "brim_type", "brim_width", "brim_object_gap",
    "enable_support", "support_type", "raft_layers",
    "reduce_crossing_wall", "detect_overhang_wall", "overhang_1_4_speed", "overhang_2_4_speed",
    "overhang_3_4_speed", "overhang_4_4_speed",
    "slow_down_for_layer_cooling", "slow_down_layer_time", "min_print_speed",
    "bridge_speed", "bridge_flow", "thick_bridges",
    "seam_position", "wipe_on_loops", "enable_arc_fitting"
};

const std::vector<std::string> k_filament_keys = {
    "filament_type", "filament_diameter", "filament_density",
    "nozzle_temperature", "nozzle_temperature_initial_layer",
    "hot_plate_temp", "hot_plate_temp_initial_layer", "cool_plate_temp", "eng_plate_temp",
    "textured_plate_temp", "chamber_temperatures",
    "fan_min_speed", "fan_max_speed", "fan_cooling_layer_time", "close_fan_the_first_x_layers",
    "reduce_fan_stop_start_freq", "full_fan_speed_layer",
    "filament_flow_ratio", "filament_max_volumetric_speed",
    "retraction_length", "retraction_speed", "deretraction_speed", "retract_before_wipe",
    "retract_restart_extra", "retraction_minimum_travel", "wipe", "wipe_distance",
    "filament_retraction_length", "filament_retraction_speed"
};

const std::vector<std::string> k_printer_keys = {
    "printer_model", "nozzle_diameter", "printer_structure",
    "gcode_flavor", "extruder_clearance_radius", "extruder_clearance_height_to_lid",
    "machine_max_speed_x", "machine_max_speed_y", "machine_max_speed_z", "machine_max_speed_e",
    "machine_max_acceleration_x", "machine_max_acceleration_y", "machine_max_acceleration_extruding",
    "retraction_length", "retraction_speed", "deretraction_speed", "retract_length_toolchange",
    "z_hop", "z_hop_types", "retract_lift_above", "retract_lift_below",
    "print_host", "print_host_webui", "printhost_apikey"
};

std::string strip_json_fences(std::string s)
{
    boost::trim(s);
    if (boost::starts_with(s, "```")) {
        auto first_nl = s.find('\n');
        if (first_nl != std::string::npos)
            s = s.substr(first_nl + 1);
        auto last = s.rfind("```");
        if (last != std::string::npos)
            s = s.substr(0, last);
        boost::trim(s);
    }
    return s;
}

} // namespace

json PresetAnalyzer::config_to_json(const DynamicPrintConfig &cfg, const std::vector<std::string> &prefer_keys)
{
    json j = json::object();
    auto add_key = [&](const std::string &key) {
        const ConfigOption *opt = cfg.option(key);
        if (!opt)
            return;
        try {
            j[key] = opt->serialize();
        } catch (...) {
            // skip exotic options
        }
    };
    for (const auto &k : prefer_keys)
        add_key(k);
    return j;
}

json PresetAnalyzer::dump_current_settings(const PresetBundle *bundle)
{
    json root;
    if (!bundle)
        return root;

    const Preset &print = bundle->prints.get_edited_preset();
    const Preset &fila  = bundle->filaments.get_edited_preset();
    const Preset &prn   = bundle->printers.get_edited_preset();

    root["print_preset"] = print.name;
    root["filament_preset"] = fila.name;
    root["printer_preset"] = prn.name;
    root["print"] = config_to_json(print.config, k_print_keys);
    root["filament"] = config_to_json(fila.config, k_filament_keys);
    root["printer"] = config_to_json(prn.config, k_printer_keys);
    return root;
}

std::string PresetAnalyzer::system_prompt_analyze()
{
    return R"(You are an expert FDM 3D printing process engineer familiar with OrcaSlicer / BambuStudio setting keys.
Analyze the provided JSON of current print/filament/printer settings.
Return STRICT JSON only (no markdown) with schema:
{
  "summary": "short Russian explanation",
  "changes": [
    {"category":"print|filament|printer", "key":"orca_option_key", "value":"serialized_value", "reason":"why in Russian"}
  ]
}
Only suggest concrete improvements (speeds, temps, cooling, retraction, brim, elephant_foot_compensation, etc.).
Use real OrcaSlicer option keys. Prefer fewer high-impact changes (max 12).
If settings look fine, return empty changes and say so in summary.)";
}

std::string PresetAnalyzer::system_prompt_problem()
{
    return R"(You are an expert FDM troubleshooting assistant for OrcaSlicer.
User describes a print defect (Russian or English), e.g. elephant foot, corner lifting/warping, stringing, layer shifts, under-extrusion, pillows, zits.
Given current settings JSON + problem text, propose corrective setting changes.
Return STRICT JSON only:
{
  "summary": "diagnosis + plan in Russian",
  "changes": [
    {"category":"print|filament|printer", "key":"orca_option_key", "value":"serialized_value", "reason":"why in Russian"}
  ]
}
Use real OrcaSlicer keys. Max 12 changes. Prefer process/filament over printer hardware limits.)";
}

std::string PresetAnalyzer::system_prompt_vision()
{
    return R"(You analyze a webcam frame of an FDM printer during a print.
Decide if something is going wrong (spaghetti, detachment, nozzle crash into blob, severe warping, fire/smoke risk, empty bed while supposedly printing, obvious failure).
Return STRICT JSON only:
{
  "ok": true/false,
  "severity": true/false,
  "severity_level": "none|low|medium|high",
  "description": "short Russian description of what you see",
  "recommend_stop": true/false
}
Set anomaly=true and recommend_stop=true only when failure is clear. If image is unclear/dark/blank, ok=false, anomaly=false.)";
}

AnalysisResult PresetAnalyzer::parse_ai_response(const std::string &ai_text)
{
    AnalysisResult out;
    try {
        const std::string cleaned = strip_json_fences(ai_text);
        out.raw = json::parse(cleaned);
        out.summary = out.raw.value("summary", "");
        if (out.raw.contains("changes") && out.raw["changes"].is_array()) {
            for (const auto &c : out.raw["changes"]) {
                SettingChange ch;
                ch.category = c.value("category", "print");
                ch.key = c.value("key", "");
                ch.new_value = c.value("value", "");
                ch.reason = c.value("reason", "");
                if (!ch.key.empty() && !ch.new_value.empty())
                    out.changes.push_back(std::move(ch));
            }
        }
        out.ok = true;
    } catch (const std::exception &ex) {
        out.error = std::string("Cannot parse AI JSON: ") + ex.what();
    }
    return out;
}

DynamicPrintConfig *PresetAnalyzer::config_for_category(const std::string &category)
{
    auto *bundle = wxGetApp().preset_bundle;
    if (!bundle)
        return nullptr;
    std::string cat = category;
    boost::algorithm::to_lower(cat);
    if (cat == "print" || cat == "process")
        return &bundle->prints.get_edited_preset().config;
    if (cat == "filament" || cat == "filament_preset")
        return &bundle->filaments.get_edited_preset().config;
    if (cat == "printer" || cat == "machine")
        return &bundle->printers.get_edited_preset().config;
    return nullptr;
}

int PresetAnalyzer::apply_changes(const std::vector<SettingChange> &changes, std::string &error)
{
    int applied = 0;
    ConfigSubstitutionContext ctx{ForwardCompatibilitySubstitutionRule::Enable};
    for (const auto &ch : changes) {
        DynamicPrintConfig *cfg = config_for_category(ch.category);
        if (!cfg) {
            BOOST_LOG_TRIVIAL(warning) << "AIAssist unknown category: " << ch.category;
            continue;
        }
        if (!cfg->option(ch.key)) {
            BOOST_LOG_TRIVIAL(warning) << "AIAssist unknown key: " << ch.key;
            continue;
        }
        try {
            cfg->set_deserialize(ch.key, ch.new_value, ctx);
            ++applied;
        } catch (const std::exception &ex) {
            BOOST_LOG_TRIVIAL(error) << "AIAssist apply " << ch.key << ": " << ex.what();
        }
    }

    // Обновляем вкладки, чтобы UI подхватил значения
    for (auto type : {Preset::TYPE_PRINT, Preset::TYPE_FILAMENT, Preset::TYPE_PRINTER}) {
        if (Tab *tab = wxGetApp().get_tab(type)) {
            tab->update_dirty();
            tab->reload_config();
        }
    }

    if (applied == 0 && !changes.empty())
        error = "None of the suggested keys could be applied (unknown keys or values).";
    return applied;
}

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r
