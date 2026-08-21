#ifndef slic3r_AIAssist_WebcamCapture_hpp_
#define slic3r_AIAssist_WebcamCapture_hpp_

#include <string>
#include <vector>

#include "libslic3r/AppConfig.hpp"

namespace Slic3r {
namespace GUI {
namespace AIAssist {

struct Snapshot
{
    bool                         ok{false};
    std::string                  error;
    std::string                  mime_type{"image/jpeg"};
    std::vector<unsigned char>   bytes;
};

// Кадр с прямого URL (mjpeg snapshot / static jpg) или через Moonraker webcam.
class WebcamCapture
{
public:
    explicit WebcamCapture(AppConfig *cfg);

    Snapshot grab() const;

    // Остановка печати на Moonraker (pause + cancel — «остановить»).
    bool stop_print(std::string &error) const;

private:
    AppConfig *m_cfg;
    Snapshot http_get_bytes(const std::string &url, const std::string &api_key) const;
    std::string resolve_snapshot_url() const;
    std::string moonraker_base() const;
};

} // namespace AIAssist
} // namespace GUI
} // namespace Slic3r

#endif
