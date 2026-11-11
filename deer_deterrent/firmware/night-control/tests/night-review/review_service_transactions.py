"""Compile exact service transaction functions with controlled host seams.

The policy/configuration implementation and extracted service transaction bodies
are production code. Only RTOS mutexes, output application and NVS persistence are
host seams. This verifies ordering and failure acknowledgements, not ESP32 NVS or
GPIO operation. The harness is generated anew so repairs cannot leave it testing
an old handwritten copy of the transaction logic.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

HERE=Path(__file__).resolve().parent
SRC=HERE.parents[1]/'src'


def function(source, signature):
    start=source.index(signature)
    brace=source.index('{',start)
    depth=0
    quoted=None
    escape=False
    for i in range(brace,len(source)):
        ch=source[i]
        if quoted:
            if escape:escape=False
            elif ch=='\\':escape=True
            elif ch==quoted:quoted=None
        elif ch in ['"',"'"]:quoted=ch
        elif ch=='{':depth+=1
        elif ch=='}':
            depth-=1
            if not depth:return source[start:i+1]
    raise ValueError(signature)


PRELUDE=r'''
#include "control_policy.hpp"
#include "control_config.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>
#include <sys/time.h>
using namespace night_control;
Core core;
StoredConfiguration stored,disk;
std::mutex coreStorage,settingsStorage,hookMutex;
auto coreMutex=&coreStorage;auto settingsMutex=&settingsStorage;
std::atomic<uint32_t> pauseEpoch{0};
std::atomic<unsigned> pauseRequests{0};
std::atomic<bool> networkChange{false},syncLogged{false};
Outputs applied;
enum class Role {Main,Pause,Resume,Configure};
thread_local Role role=Role::Main;
std::condition_variable hookCondition;
bool blockPauseSettings=false,pauseStopped=false,releasePause=false;
bool blockSave=false,saveStopped=false,releaseSave=false;
Role blockedSaveRole=Role::Resume;
bool failSave=false;
static unsigned checks=0;
void check(bool v,const char* message,int line){++checks;if(!v){std::fprintf(stderr,"FAIL line %d %s\n",line,message);std::exit(1);}}
#define CHECK(x) check((x),#x,__LINE__)
void await(bool& condition){std::unique_lock<std::mutex> l(hookMutex);CHECK(hookCondition.wait_for(l,std::chrono::seconds(3),[&]{return condition;}));}
void release(bool& condition){{std::lock_guard<std::mutex> l(hookMutex);condition=true;}hookCondition.notify_all();}
class Lock {
 std::mutex* m;
 public:
 explicit Lock(std::mutex* v):m(v){
  if(m==settingsMutex&&role==Role::Pause&&blockPauseSettings){
   std::unique_lock<std::mutex> l(hookMutex);pauseStopped=true;hookCondition.notify_all();
   CHECK(hookCondition.wait_for(l,std::chrono::seconds(3),[]{return releasePause;}));
  }
  m->lock();
 }
 ~Lock(){m->unlock();}
};
int64_t now(){return 1000000;}
void apply(const Snapshot& s){applied=s.outputs;}
Snapshot snapshot(){Lock l(coreMutex);auto s=core.tick(now());apply(s);return s;}
void log(const char*){}
void fatal(const char*){Lock l(coreMutex);core.fault(now());apply(core.tick(now()));}
bool save(const StoredConfiguration& next){
 if(blockSave&&role==blockedSaveRole){
  std::unique_lock<std::mutex> l(hookMutex);saveStopped=true;hookCondition.notify_all();
  CHECK(hookCondition.wait_for(l,std::chrono::seconds(3),[]{return releaseSave;}));
 }
 if(failSave||!validStoredConfiguration(next))return false;disk=next;return true;
}
void reset(bool paused=false){
 core=Core{};stored=StoredConfiguration{};stored.configured=1;stored.paused=paused;
 stored.latitude=41.5;stored.longitude=-81.69;
 std::strcpy(stored.ssid,"test-network");std::strcpy(stored.wifiPassword,"test-password");
 std::strcpy(stored.accessPassword,"test-access-password");disk=stored;
 pauseEpoch=0;pauseRequests=0;networkChange=false;syncLogged=false;
 blockPauseSettings=false;pauseStopped=false;releasePause=false;
 blockSave=false;saveStopped=false;releaseSave=false;failSave=false;
 core.configure(policyConfiguration(stored),paused,now());
 CHECK(core.synchronize(1793750400LL*1000000,now()));core.calibrationComplete(now());apply(core.tick(now()));
}
'''

TESTS=r'''
int main(){
 // PAUSE already stopped the outputs but has not entered persistence.
 reset();blockPauseSettings=true;bool pauseResult=false;
 std::thread p([&]{role=Role::Pause;pauseResult=pause();});await(pauseStopped);
 CHECK(snapshot().paused);CHECK(!applied.white&&!applied.tone);
 role=Role::Resume;CHECK(!resume());role=Role::Main;
 // A fresh synchronization cannot release an in-flight held pause.
 timeval fresh{1793750401,0};synchronized(&fresh);CHECK(snapshot().paused);
 release(releasePause);p.join();
 CHECK(pauseResult);CHECK(snapshot().paused);CHECK(stored.paused==1&&disk.paused==1);
 CHECK(pauseRequests==0);

 // PAUSE arrives while RESUME is saving enabled intent.
 reset(true);blockSave=true;blockedSaveRole=Role::Resume;bool resumeResult=true;
 std::thread r([&]{role=Role::Resume;resumeResult=resume();});await(saveStopped);
 blockPauseSettings=true;
 std::thread p2([&]{role=Role::Pause;pauseResult=pause();});await(pauseStopped);
 CHECK(snapshot().paused);release(releaseSave);r.join();CHECK(!resumeResult);
 CHECK(snapshot().paused);release(releasePause);p2.join();
 CHECK(pauseResult&&snapshot().paused&&stored.paused==1&&disk.paused==1);

 // Configuration cancellation and subsequent explicit resume require a new
 // sync and baseline, and never revive a capture ticket from the old setup.
 reset();auto old=core.ticket(Capture::Monitoring,now());
 const char body[]=R"JSON({"latitude":-33.9,"longitude":151.2,"after_sunset_minutes":10,"before_sunrise_minutes":15,"wifi_ssid":"test-network"})JSON";
 const char* error=nullptr;CHECK(configure(body,std::strlen(body),error));
 CHECK(snapshot().paused&&!snapshot().clockValid&&!snapshot().baselineReady);
 CHECK(networkChange&&stored.paused==1&&disk.paused==1);
 CHECK(!core.ticketValid(old,now()));CHECK(resume());
 CHECK(snapshot().state==State::InvalidTime&&!snapshot().captureAllowed);

 // Save failures return false, keep the current boot inhibited, and leave the
 // simulated disk record unchanged instead of announcing durable success.
 reset();failSave=true;CHECK(!pause());
 CHECK(snapshot().state==State::Fault&&snapshot().paused);CHECK(disk.paused==0);
 CHECK(pauseRequests==0);CHECK(!applied.white&&!applied.tone);
 reset(true);failSave=true;CHECK(!resume());
 CHECK(snapshot().state==State::Fault&&snapshot().paused);CHECK(disk.paused==1);
 reset();failSave=true;CHECK(!configure(body,std::strlen(body),error));
 CHECK(error!=nullptr);CHECK(snapshot().state==State::Fault&&snapshot().paused);
 CHECK(stored.latitude==41.5&&disk.latitude==41.5);

 // Bad settings do not cancel active state or partially change configuration.
 reset();const char invalid[]=R"JSON({"latitude":999,"longitude":0,"after_sunset_minutes":0,"before_sunrise_minutes":0,"wifi_ssid":"test-network"})JSON";
 const auto generation=snapshot().generation;
 CHECK(!configure(invalid,std::strlen(invalid),error));
 CHECK(snapshot().generation==generation&&snapshot().state==State::Armed);
 CHECK(stored.latitude==41.5&&disk.latitude==41.5);
 std::printf("PASS extracted service transactions checks=%u\n",checks);
}
'''


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--idf',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    CJSON=args.idf.resolve()/'components/json/cJSON'
    if not (CJSON/'cJSON.c').is_file():
        parser.error('--idf must identify the ESP-IDF checkout containing components/json/cJSON/cJSON.c')
    OUTPUT=args.output.resolve()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    source=(SRC/'control_service.cpp').read_text()
    bodies='\n\n'.join(function(source,s) for s in [
        'void synchronized(struct timeval* time)',
        'bool configure(const char* body,size_t length,const char*& error)',
        'bool pause()', 'bool resume()'])
    generated=OUTPUT/'service_transactions_generated.cpp'
    generated.write_text(PRELUDE+'\n'+bodies+'\n'+TESTS)
    subprocess.run(['gcc','-std=c11','-c',str(CJSON/'cJSON.c'),'-I',str(CJSON),'-o',str(OUTPUT/'cJSON.o')],check=True)
    binary=OUTPUT/'service_transactions_test'
    command=['g++','-std=c++17','-pthread','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
             '-fsanitize=undefined','-fno-sanitize-recover=all','-g','-I',str(SRC),'-I',str(CJSON),
             str(generated),str(SRC/'control_policy.cpp'),str(SRC/'control_config.cpp'),
             str(SRC/'solar.cpp'),str(OUTPUT/'cJSON.o'),'-o',str(binary)]
    subprocess.run(command,check=True)
    result=subprocess.check_output([str(binary)],text=True)
    (OUTPUT/'service-transaction-results.json').write_text(json.dumps({
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'result':result.strip(),'scope':'Exact service transaction bodies; host mutex/output/NVS seams; no board operation'},indent=2)+'\n')
    print(result,end='')
