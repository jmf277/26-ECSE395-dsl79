// Exact embedded JavaScript against a minimal DOM/API seam; not a browser test.
'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, '../src/control_page.hpp');
const source = fs.readFileSync(sourcePath, 'utf8');
const script = source.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const elements = Object.fromEntries([...source.matchAll(/\bid="([^"]+)"/g)]
    .map(match => [match[1], {value: '', textContent: ''}]));
const intervals = [], requests = [], delayedStatuses = [], delayedMutations = [];
let checks = 0, failStatus = false, nextFailure = null;
let state = {state: 'UNCONFIGURED', paused: true, configured: false,
    clock_valid: false, clock_age_s: -1, utc: 0, window_start: 0,
    window_end: 0, next_start: 0, baseline_ready: false,
    network_connected: false, setup_ssid: 'DeerSetup-TEST', wifi_ssid: '',
    latitude: 0, longitude: 0, after_sunset_minutes: 0,
    before_sunrise_minutes: 0, csrf: 'test-only-request-token'};
function check(condition, message) {assert.ok(condition, message); checks++;}
function equal(actual, expected, message) {assert.deepEqual(actual, expected, message); checks++;}
function delayNextStatus(snapshot, {phase = 'fetch', error = null} = {}) {
    const captured = {...snapshot};
    let release;
    const pending = new Promise(resolve => {release = resolve;});
    const body = async () => {if (error) throw error; return {...captured};};
    delayedStatuses.push(phase === 'body'
        ? async () => ({ok: true, status: 200, json: async () => {await pending; return body();}})
        : async () => {await pending; if (error) throw error; return {ok: true, status: 200, json: body};});
    return release;
}
function delayNextMutation(url, {message = 'Applied.', error = null} = {}) {
    let release;
    const pending = new Promise(resolve => {release = resolve;});
    delayedMutations.push({url, reply: async () => {
        await pending;
        if (error) throw error;
        return {ok: true, status: 200, json: async () => ({ok: true, message})};
    }});
    return release;
}
async function fetch(url, options = {}) {
    requests.push({url, ...options});
    if (url === '/api/status') {
        if (delayedStatuses.length) return delayedStatuses.shift()();
        if (failStatus) return {ok: false, status: typeof failStatus === 'number' ? failStatus : 503};
        const captured = {...state};
        return {ok: true, status: 200, json: async () => captured};
    }
    if (nextFailure) {
        const failure = nextFailure; nextFailure = null;
        if (failure.changeState) Object.assign(state, failure.changeState);
        return {ok: false, status: failure.status, json: async () => {
            if (failure.nonJson) throw new SyntaxError('Unexpected token < in JSON');
            return {ok: false, message: failure.message};
        }};
    }
    if (url === '/api/config') {
        Object.assign(state, JSON.parse(options.body), {configured: true, paused: true,
            state: 'PAUSED', baseline_ready: false});
    } else if (url === '/api/pause') {
        state.paused = true; state.state = 'PAUSED';
    } else if (url === '/api/resume') {
        state.paused = false; state.state = 'WAIT_TIME';
    } else if (url === '/api/calibrate') {
        state.state = 'CALIBRATING';
    } else throw new Error('Unexpected endpoint: ' + url);
    if (delayedMutations.length) {
        const delayed = delayedMutations.shift();
        assert.equal(delayed.url, url, 'Delayed mutation reaches the expected endpoint');
        return delayed.reply();
    }
    return {ok: true, status: 200, json: async () => ({ok: true, message: 'Applied.'})};
}
const context = vm.createContext({document: {getElementById: id => {
    assert.ok(elements[id], 'Referenced element exists: ' + id); return elements[id];
}}, fetch, setInterval: (callback, delay) => intervals.push({callback, delay}),
Date, Math, Number, Error, JSON});
const settle = () => new Promise(resolve => setImmediate(resolve));
function lastPost() {return requests.filter(r => r.method === 'POST').at(-1);}
function statusRequests() {return requests.filter(r => r.url === '/api/status').length;}

async function main() {
    vm.runInContext(script, context, {filename: sourcePath});
    await settle();
    equal(elements.state.textContent, 'UNCONFIGURED', 'Initial permission is displayed');
    equal(elements.latitude.value, '', 'No invented latitude populates an unconfigured site');
    equal(elements.longitude.value, '', 'No invented longitude populates an unconfigured site');
    equal(intervals.length, 1, 'One periodic status poll');
    equal(intervals[0].delay, 2000, 'Status poll interval');
    check(elements.status.textContent.includes('waiting for a fresh network sync'), 'Invalid time explained');
    check(elements.status.textContent.includes('DeerSetup-TEST'), 'Setup network is displayed');

    for (const [id, value] of Object.entries({latitude: '41.5', longitude: '-81.6',
        after: '10', before: '5', ssid: 'TestNetwork', password: 'test-only-passphrase'})) elements[id].value = value;
    let prevented = false;
    elements.config.onsubmit({preventDefault: () => {prevented = true;}});
    await settle();
    check(prevented, 'Submit stays on the control page');
    const saved = lastPost();
    equal(saved.url, '/api/config', 'Configuration endpoint');
    equal(saved.headers['Content-Type'], 'application/json', 'JSON content type');
    equal(saved.headers['X-Deer-Control'], state.csrf, 'Per-boot request token');
    equal(JSON.parse(saved.body), {latitude: 41.5, longitude: -81.6,
        after_sunset_minutes: 10, before_sunrise_minutes: 5,
        wifi_ssid: 'TestNetwork', wifi_password: 'test-only-passphrase'}, 'Exact settings payload');
    equal(elements.password.value, '', 'Password clears after submission');
    equal(elements.state.textContent, 'PAUSED', 'Save shows paused state');
    elements.config.onsubmit({preventDefault() {}}); await settle();
    check(!Object.hasOwn(JSON.parse(lastPost().body), 'wifi_password'), 'Blank password is omitted rather than replacing the saved secret');

    for (const [id, endpoint, expected] of [['resume','/api/resume','WAIT_TIME'],
        ['calibrate','/api/calibrate','CALIBRATING'],['pause','/api/pause','PAUSED']]) {
        await elements[id].onclick();
        equal(lastPost().url, endpoint, id + ' endpoint');
        equal(JSON.parse(lastPost().body), {}, id + ' sends an empty object');
        equal(elements.state.textContent, expected, id + ' reads actual returned status');
    }
    nextFailure = {status: 400, message: 'Calibration blocked while paused.'};
    await elements.calibrate.onclick();
    equal(elements.message.textContent, 'Calibration blocked while paused.', 'Device rejection is visible');
    equal(elements.state.textContent, 'PAUSED', 'A rejected command does not invent a state transition');
    nextFailure = {status: 403, nonJson: true};
    await elements.resume.onclick();
    check(elements.message.textContent.includes('Reload'), 'Non-JSON authorization error gives a recovery action');
    equal(elements.state.textContent, 'PAUSED', 'Authorization failure does not invent success');
    nextFailure = {status: 401, nonJson: true};
    await elements.resume.onclick();
    check(elements.message.textContent.includes('Sign-in required'), 'Authentication failure identifies the needed sign-in');
    equal(elements.state.textContent, 'PAUSED', 'Authentication failure does not invent success');
    nextFailure = {status: 503, nonJson: true};
    await elements.resume.onclick();
    check(elements.message.textContent.includes('HTTP 503'), 'Non-JSON service failure retains its HTTP status');
    equal(elements.state.textContent, 'PAUSED', 'Service failure does not invent success');
    failStatus = true; await intervals[0].callback();
    check(elements.message.textContent.includes('Status unavailable'), 'Status transport error is visible');
    check(elements.message.textContent.includes('HTTP 503'), 'Status failure preserves the latest command rejection');
    failStatus = false;
    state = {...state, csrf: 'replacement-test-token', paused: true, state: 'PAUSED'};
    await intervals[0].callback(); await elements.pause.onclick();
    equal(lastPost().headers['X-Deer-Control'], 'replacement-test-token', 'Refreshed token is used after a restart');

    await elements.resume.onclick();
    const releaseOldStatus = delayNextStatus({...state, state: 'ARMED', paused: false,
        csrf: 'obsolete-test-token'});
    const oldPoll = intervals[0].callback();
    await elements.pause.onclick();
    equal(elements.state.textContent, 'PAUSED', 'Pause refresh displays the new device state');
    releaseOldStatus(); await oldPoll;
    equal(elements.state.textContent, 'PAUSED', 'A delayed pre-pause status cannot overwrite the paused state');
    await elements.pause.onclick();
    equal(lastPost().headers['X-Deer-Control'], 'replacement-test-token', 'A delayed status cannot restore an obsolete request token');

    // A response can arrive before its JSON body; both awaits cross command boundaries.
    await elements.resume.onclick();
    const releaseOldBody = delayNextStatus({...state, state: 'ARMED', paused: false,
        csrf: 'obsolete-body-token'}, {phase: 'body'});
    const oldBodyPoll = intervals[0].callback(); await settle();
    await elements.pause.onclick();
    releaseOldBody(); await oldBodyPoll;
    equal(elements.state.textContent, 'PAUSED', 'A delayed pre-pause JSON body cannot overwrite newer state');
    await elements.pause.onclick();
    equal(lastPost().headers['X-Deer-Control'], 'replacement-test-token', 'An obsolete body cannot overwrite the current token');

    // Slow status requests should not accumulate or suppress every result forever.
    const releaseSlowPoll = delayNextStatus({...state, state: 'DAYTIME', paused: false});
    const slowPoll = intervals[0].callback();
    const slowRequestCount = statusRequests();
    await intervals[0].callback(); await intervals[0].callback();
    equal(statusRequests(), slowRequestCount, 'Periodic polling does not overlap a current pending status request');
    releaseSlowPoll(); await slowPoll;
    equal(elements.state.textContent, 'DAYTIME', 'The completed slow poll still publishes its snapshot');
    await intervals[0].callback();
    equal(statusRequests(), slowRequestCount + 1, 'Polling resumes after the current request finishes');
    equal(elements.state.textContent, 'PAUSED', 'The next poll displays the next actual snapshot');

    // Finishing an obsolete read must not release the newer read's polling guard.
    const releaseSuperseded = delayNextStatus({...state, state: 'ARMED', paused: false});
    const supersededPoll = intervals[0].callback();
    const releasePauseStatus = delayNextStatus({...state, state: 'PAUSED', paused: true});
    const pausedCommand = elements.pause.onclick(); await settle();
    const pauseStatusCount = statusRequests();
    releaseSuperseded(); await supersededPoll;
    await intervals[0].callback();
    equal(statusRequests(), pauseStatusCount, 'An obsolete completion cannot allow polling over the current refresh');
    releasePauseStatus(); await pausedCommand;
    equal(elements.state.textContent, 'PAUSED', 'The command refresh remains eligible after an obsolete read finishes');

    // Obsolete transport and body errors cannot replace a newer acknowledgement.
    for (const phase of ['fetch', 'body']) {
        const releaseFailure = delayNextStatus({...state}, {phase, error: new Error('Obsolete ' + phase + ' failure')});
        const obsoleteFailure = intervals[0].callback(); await settle();
        await elements.pause.onclick();
        const acknowledged = elements.message.textContent;
        releaseFailure(); await obsoleteFailure;
        equal(elements.message.textContent, acknowledged, 'A superseded ' + phase + ' error cannot obscure the pause acknowledgement');
        equal(elements.state.textContent, 'PAUSED', 'A superseded ' + phase + ' error does not change state');
    }

    // Pause remains usable if an earlier command has been handled but its reply stalls.
    const releaseResumeReply = delayNextMutation('/api/resume', {message: 'Old resume acknowledged.'});
    const slowResume = elements.resume.onclick(); await settle();
    await elements.pause.onclick();
    equal(elements.state.textContent, 'PAUSED', 'Pause displays confirmed device state without waiting for an older command reply');
    const pauseMessage = elements.message.textContent;
    const pendingCommandCount = statusRequests();
    await intervals[0].callback();
    equal(statusRequests(), pendingCommandCount, 'Periodic polling waits while a command is pending');
    releaseResumeReply(); await slowResume;
    equal(elements.message.textContent, pauseMessage, 'An older command acknowledgement cannot replace the newer pause acknowledgement');
    equal(elements.state.textContent, 'PAUSED', 'Completing the older reply refreshes actual state rather than inventing resumed operation');

    const releaseResumeFailure = delayNextMutation('/api/resume', {error: new Error('Old resume connection lost')});
    const failedResume = elements.resume.onclick(); await settle();
    await elements.pause.onclick();
    const successfulPauseMessage = elements.message.textContent;
    releaseResumeFailure(); await failedResume;
    equal(elements.message.textContent, successfulPauseMessage, 'An older command failure cannot replace a newer pause acknowledgement');
    equal(elements.state.textContent, 'PAUSED', 'A failed older reply still refreshes the actual current state');

    // A failed save can still inhibit the controller; always retrieve its actual status.
    nextFailure = {status: 400, message: 'Stopped for this boot. Pause could not be saved.',
        changeState: {state: 'FAULT', paused: true}};
    await elements.pause.onclick();
    equal(elements.state.textContent, 'FAULT', 'A rejected command refreshes a real device-state change');
    equal(elements.message.textContent, 'Stopped for this boot. Pause could not be saved.', 'The device rejection remains visible after a successful refresh');

    // An authentication or device error can also prevent the follow-up status read.
    for (const failure of [
        {status: 401, nonJson: true, expected: 'Sign-in required.'},
        {status: 403, nonJson: true, expected: 'Control request refused.'},
        {status: 400, message: 'Stopped for this boot. Pause could not be saved.',
            expected: 'Pause could not be saved.'}
    ]) {
        failStatus = failure.status;
        nextFailure = failure;
        await elements.pause.onclick();
        check(elements.message.textContent.includes(failure.expected), 'A failed status refresh preserves the specific command failure: ' + failure.status);
        check(elements.message.textContent.includes('Status unavailable'), 'A failed follow-up status is also visible: ' + failure.status);
        failStatus = false;
        await intervals[0].callback();
        check(elements.message.textContent.includes(failure.expected), 'Status recovery preserves the command outcome: ' + failure.status);
        check(!elements.message.textContent.includes('Status unavailable'), 'A current successful status clears its old transport warning: ' + failure.status);
    }

    failStatus = 503;
    await elements.pause.onclick();
    check(elements.message.textContent.includes('Applied.'), 'A successful command acknowledgement survives a failed status refresh');
    check(elements.message.textContent.includes('Status unavailable'), 'The failed refresh remains visible beside the acknowledgement');
    failStatus = false;
    await intervals[0].callback();
    equal(elements.message.textContent, 'Applied.', 'Status recovery clears only the status warning');

    elements.latitude.value = '40.25'; elements.longitude.value = '-80.5';
    elements.ssid.value = 'UnsavedNetwork'; elements.password.value = 'unsaved-test-passphrase';
    state = {...state, state: 'PAUSED', paused: true, latitude: 41.5, longitude: -81.6,
        wifi_ssid: 'SavedNetwork'};
    await intervals[0].callback();
    equal(elements.latitude.value, '40.25', 'Polling preserves an in-progress latitude edit');
    equal(elements.longitude.value, '-80.5', 'Polling preserves an in-progress longitude edit');
    equal(elements.ssid.value, 'UnsavedNetwork', 'Polling preserves an in-progress network edit');
    equal(elements.password.value, 'unsaved-test-passphrase', 'Polling does not clear an unsubmitted password');
    equal(delayedStatuses.length, 0, 'Every delayed status fixture was exercised');
    equal(delayedMutations.length, 0, 'Every delayed command fixture was exercised');

    const result = {checks, passed: true, source_sha256: crypto.createHash('sha256').update(source).digest('hex'),
        scope: 'Exact embedded JavaScript with minimal DOM/API seams; no browser layout, HTTP server, radio, or board execution.'};
    console.log(JSON.stringify(result, null, 2));
}
main().catch(error => {console.error(error); process.exitCode = 1;});
