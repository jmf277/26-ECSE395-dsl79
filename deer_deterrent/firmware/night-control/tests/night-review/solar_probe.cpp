#include "solar.hpp"
#include <iomanip>
#include <iostream>
#include <string>

int main() {
    char command;
    int64_t now;
    double latitude, longitude;
    unsigned after, before;
    while(std::cin >> command >> now >> latitude >> longitude >> after >> before) {
        if(command=='E') {
            auto e=solar::events(now,latitude,longitude);
            std::cout << e.valid << ' ' << e.sunrise << ' ' << e.sunset << '\n';
        } else {
            auto w=solar::schedule(now,latitude,longitude,after,before);
            for(const auto& i:w) std::cout << i.start << ' ' << i.end << ' ';
            std::cout << '\n';
        }
    }
}
