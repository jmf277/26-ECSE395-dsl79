#pragma once
// No remote assets, credentials, or site coordinates are embedded in the page.
static constexpr const char controlPage[]=R"HTML(<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Deer deterrent control</title>
<style>body{font:17px system-ui;max-width:44rem;margin:2rem auto;padding:0 1rem;background:#f5f7f4;color:#14251c}section{background:white;padding:1rem;margin:1rem 0;border-radius:.6rem}button,input{font:inherit;padding:.6rem;margin:.3rem 0}button{cursor:pointer}#pause{background:#8e1717;color:white}label{display:block;margin:.7rem 0}input{display:block;max-width:95%}pre{white-space:pre-wrap;font:inherit}.hint{font-size:.9rem;color:#394c40}#message{font-weight:bold}</style>
<h1>Deer deterrent</h1><section><h2 id="state">Loading status…</h2><pre id="status"></pre><button id="pause">Pause and hold</button> <button id="resume">Resume automatic operation</button><p class="hint">Pause stops light and sound and remains set after a successful save. Resume allows deterrence only with fresh network time, a valid night window, and a completed reference.</p><button id="calibrate">Relearn empty view</button><p class="hint">Relearning requires Resume first. It intentionally takes 12 illuminated reference photos, including in daytime or while waiting for time. Keep the view empty. It cannot sound an alarm.</p><p id="message" role="status"></p></section>
<section><h2>Site and network</h2><p class="hint">Use the installed location, with east-positive longitude. Save pauses operation and discards the old reference. A new reference is learned automatically at night after Resume. Network access is needed for time synchronization.</p><form id="config"><label>Latitude (−90 to 90, excluding poles)<input id="latitude" type="number" step="any" min="-89.999999" max="89.999999" required></label><label>Longitude (west negative, east positive)<input id="longitude" type="number" step="any" min="-180" max="180" required></label><label>Start minutes after sunset<input id="after" type="number" min="0" max="120" step="1" value="0" required></label><label>Stop minutes before sunrise<input id="before" type="number" min="0" max="120" step="1" value="0" required></label><label>Wi-Fi network name<input id="ssid" maxlength="32" autocomplete="off" required></label><label>Wi-Fi password (8–63 printable characters)<input id="password" type="password" minlength="8" maxlength="63" autocomplete="new-password"></label><p class="hint">Leave password blank to retain it only when the network name is unchanged. The setup network stays available at http://192.168.4.1. Use this controller only on a trusted local network; its HTTP page is not encrypted separately from Wi-Fi.</p><button>Save settings and pause</button></form></section>
<script nonce="%NONCE%">
'use strict';
let token='',loaded=false,statusRevision=0,statusPending=false,changesPending=0,actionRevision=0;
let commandMessage='',statusMessage='';
const $=id=>document.getElementById(id);
const fmt=s=>s?new Date(s*1000).toISOString().replace('T',' ').replace('.000Z',' UTC'):'—';
function showMessages(){
    $('message').textContent=[commandMessage,statusMessage].filter(Boolean).join(' ');
}
async function status(){
    const revision=++statusRevision;
    statusPending=true;
    try{
        const r=await fetch('/api/status',{cache:'no-store'});
        if(!r.ok)throw Error('Status unavailable');
        const s=await r.json();
        // A newer read or control request makes this snapshot obsolete.
        if(revision!==statusRevision)return;
        token=s.csrf;
        $('state').textContent=s.state;
        $('status').textContent='Control: '+(s.paused?'PAUSED':'ENABLED')+
            '\nClock: '+(s.clock_valid?'valid, last sync '+Math.floor(s.clock_age_s/60)+' min ago':'waiting for a fresh network sync')+
            '\nUTC: '+fmt(s.utc)+'\nNight window: '+fmt(s.window_start)+' → '+fmt(s.window_end)+
            '\nNext start: '+fmt(s.next_start)+'\nReference: '+(s.baseline_ready?'ready':'required')+
            '\nNetwork: '+(s.network_connected?'connected':'disconnected')+'\nSetup network: '+s.setup_ssid;
        if(!loaded){
            if(s.configured){$('latitude').value=s.latitude;$('longitude').value=s.longitude;}
            $('after').value=s.after_sunset_minutes;
            $('before').value=s.before_sunrise_minutes;
            $('ssid').value=s.wifi_ssid;
            loaded=true;
        }
        statusMessage='';
        showMessages();
    }catch(e){
        if(revision===statusRevision){statusMessage=e.message;showMessages();}
    }finally{
        if(revision===statusRevision)statusPending=false;
    }
}
async function post(path,body){
    const action=++actionRevision;
    ++changesPending;
    // Invalidate earlier reads immediately, without waiting for their network replies.
    ++statusRevision;
    statusPending=false;
    try{
        const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Deer-Control':token},body:JSON.stringify(body)});
        let result;
        try{result=await r.json();}
        catch{throw Error(r.status===401?'Sign-in required. Reload the control page.':r.status===403?'Control request refused. Reload the page and try again.':'Control request failed (HTTP '+r.status+').');}
        if(!r.ok)throw Error(result.message);
        if(action===actionRevision){commandMessage=result.message;showMessages();}
    }catch(e){
        if(action===actionRevision){commandMessage=e.message;showMessages();}
    }finally{
        --changesPending;
        // Read actual state even after a rejected request, and without waiting
        // for an older command. A pause must remain visible if that reply stalls.
        await status();
    }
}
$('pause').onclick=()=>post('/api/pause',{});
$('resume').onclick=()=>post('/api/resume',{});
$('calibrate').onclick=()=>post('/api/calibrate',{});
$('config').onsubmit=e=>{
    e.preventDefault();
    const c={latitude:Number($('latitude').value),longitude:Number($('longitude').value),
        after_sunset_minutes:Number($('after').value),before_sunrise_minutes:Number($('before').value),wifi_ssid:$('ssid').value};
    if($('password').value)c.wifi_password=$('password').value;
    post('/api/config',c).then(()=>{$('password').value='';});
};
status();
setInterval(()=>{if(!statusPending&&!changesPending)return status();},2000);
</script></html>)HTML";
