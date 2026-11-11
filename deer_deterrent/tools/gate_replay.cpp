// Host adapter for the firmware's actual gate. Detection policy lives in the
// included header; no alternate Python implementation is used for replay.
#include "../firmware/motion-size/src/approach_gate.hpp"
#include <iostream>
#include <sstream>
#include <string>

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--describe") {
        // Recording contract of the retained growth-gate revision. This is a
        // compatibility check, not another implementation of its decisions.
        static_assert(approach::minimumArea == 480, "Update the recording contract after a gate change");
        std::cout << "{\"grid\":[80,60],\"cutoff_percent\":10.0,\"growth_window\":5,"
                     "\"growth_required_percent\":25,\"growth_min_gain_pixels\":96}\n";
        return 0;
    }
    approach::Gate gate;
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream input(line);
        std::string operation, extra;
        if (!(input >> operation)) continue;
        if (operation == "RESET") {
            if (input >> extra) return 2;
            gate.reset();
            continue;
        }
        int id, area, x, y, w, h;
        if (operation != "FRAME" || !(input >> id >> area >> x >> y >> w >> h) || (input >> extra)) return 2;
        if (id < 0 || area < 0 || area > 4800 || x < 0 || y < 0 || w < 0 || h < 0 ||
            x > 80 || y > 60 || w > 80 - x || h > 60 - y || area > w * h) return 2;
        bool accepted = gate.update({area, x, y, w, h});
        const char* state = accepted ? "LARGE_CHANGE" :
            (area >= approach::minimumArea ? "WAITING_FOR_GROWTH" : (area ? "SMALL_CHANGE" : "CLEAR"));
        std::cout << "{\"id\":" << id << ",\"state\":\"" << state << "\",\"accepted\":" << (accepted ? "true" : "false")
                  << ",\"growth_frames\":" << gate.frames() << ",\"growth_pixels\":" << gate.gainPixels()
                  << ",\"growth_percent\":" << gate.gainPercent() << "}\n";
    }
    return 0;
}
