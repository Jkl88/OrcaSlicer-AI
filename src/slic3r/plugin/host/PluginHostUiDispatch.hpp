#pragma once

#include <pybind11/pybind11.h>

#include <chrono>
#include <future>
#include <memory>
#include <stdexcept>
#include <type_traits>
#include <utility>

#include <wx/app.h>

#include <slic3r/GUI/GUI_App.hpp>

namespace Slic3r::host_ui_dispatch {

inline constexpr auto wait_timeout = std::chrono::seconds(30);

template<typename Fn>
auto run_blocking(Fn&& fn) -> std::invoke_result_t<std::decay_t<Fn>&>
{
    using Callable = std::decay_t<Fn>;
    using Result = std::invoke_result_t<Callable&>;

    if (wxTheApp == nullptr || wxTheApp->IsScheduledForDestruction())
        throw std::runtime_error("OrcaSlicer application is not available");

    if (wxIsMainThread()) {
        pybind11::gil_scoped_release release_gil;
        return fn();
    }

    struct State {
        explicit State(Callable&& callable) : fn(std::move(callable)) {}
        Callable fn;
        std::promise<Result> promise;
    };

    auto state = std::make_shared<State>(Callable(std::forward<Fn>(fn)));
    std::future<Result> future = state->promise.get_future();

    pybind11::gil_scoped_release release_gil;
    if (wxTheApp == nullptr || wxTheApp->IsScheduledForDestruction())
        throw std::runtime_error("OrcaSlicer application is shutting down");

    GUI::wxGetApp().CallAfter([state]() mutable {
        try {
            if constexpr (std::is_void_v<Result>) {
                state->fn();
                state->promise.set_value();
            } else {
                state->promise.set_value(state->fn());
            }
        } catch (...) {
            state->promise.set_exception(std::current_exception());
        }
    });

    if (future.wait_for(wait_timeout) != std::future_status::ready)
        throw std::runtime_error("Timed out waiting for the OrcaSlicer UI thread");
    return future.get();
}

} // namespace Slic3r::host_ui_dispatch
