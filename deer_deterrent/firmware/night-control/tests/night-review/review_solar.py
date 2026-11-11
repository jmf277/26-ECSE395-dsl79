"""Run independent numerical/date-boundary checks against compiled solar.cpp."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess

from usno_oracle import crossings, solar_altitude, HORIZON, DAY, validate_published_vectors

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / 'solar_probe'


def timestamp(date):
    return int(dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp())


def probe(rows, tz='UTC'):
    data=''.join(f'{kind} {epoch} {lat:.9f} {lon:.9f} {after} {before}\n'
                 for kind,epoch,lat,lon,after,before in rows)
    env=dict(os.environ, TZ=tz)
    output=subprocess.check_output([str(PROBE)],input=data,text=True,env=env)
    values=[list(map(int,line.split())) for line in output.splitlines()]
    assert len(values)==len(rows)
    return values


def run():
    oracle_checks=validate_published_vectors()
    vectors=json.loads((ROOT/'solar_reference_vectors.json').read_text())
    reference_checks=[]
    for v in vectors:
        d=v['nominal_midnight_utc']
        outputs=probe([('E',d+shift*DAY,v['latitude'],v['longitude'],0,0)
                       for shift in [-1,0,1]])
        for column,key in [(1,'expected_sunrise_unix'),(2,'expected_sunset_unix')]:
            actual=min((r[column] for r in outputs if r[0]),key=lambda t:abs(t-v[key]))
            error=actual-v[key]
            assert abs(error)<=v['tolerance_seconds'],(v['name'],key,error)
            reference_checks.append({'vector':v['name'],'event':'rise' if column==1 else 'set',
                                     'error_seconds':round(error,3)})

    # Real calendar boundaries, leap date, DST dates, both solstices, post-2038,
    # and near the policy's upper year. Longitudes exercise previous/next UTC dates.
    dates=['2024-02-28','2024-02-29','2024-03-01','2026-03-08','2026-06-21',
           '2026-11-01','2026-11-03','2026-12-21','2026-12-31','2027-01-01',
           '2038-01-19','2099-12-31']
    latitudes=[-65,-50,-23,0,23,50,65]
    longitudes=[-180,-179.9,-90,0,90,179.9,180]
    rows=[('E',timestamp(d),lat,lon,0,0)
          for d in dates for lat in latitudes for lon in longitudes]
    outputs=probe(rows)
    maximum_error=0.0
    worst=None
    for row,output in zip(rows,outputs):
        _,epoch,lat,lon,_,_=row
        valid,rise,sunset=output
        assert valid,(row,output,'expected two crossings below polar latitudes')
        assert rise<sunset,(row,output,'event chronology')
        # Fully independent altitude roots around each reported event. If a date
        # or sign bug shifts it away from a crossing, the bracket is empty.
        for rising,t in [(True,rise),(False,sunset)]:
            expected=[v for kind,v in crossings(t-300,t+300,lat,lon) if kind==rising]
            assert len(expected)==1,(row,rising,t,'missing independent crossing')
            error=abs(t-expected[0])
            assert error<=60,(row,rising,error)
            if error>maximum_error:
                maximum_error=error;worst={'row':row,'rising':rising,'error_seconds':round(error,3)}

    # All schedules must agree with independent physical night classification
    # except within a conservative 0.05 degree band at the approximate horizon.
    schedule_rows=[('W',timestamp(d)+offset,lat,lon,0,0)
                   for d in dates for lat in latitudes for lon in longitudes
                   for offset in [0,1,21600,43200,64800,86399]]
    schedules=probe(schedule_rows)
    classifications=0
    for row,flat in zip(schedule_rows,schedules):
        _,utc,lat,lon,_,_=row
        permitted=any(start<=utc<end for start,end in zip(flat[0::2],flat[1::2]) if start<end)
        altitude=solar_altitude(utc,lat,lon)
        if abs(altitude-HORIZON)>0.05:
            assert permitted==(altitude<HORIZON),(row,altitude,flat)
            classifications+=1

    # Same geographical meridian, same UTC instants, opposite date-line labels.
    meridian_rows=[('W',timestamp(d)+offset,lat,lon,0,0)
                   for d in dates for lat in latitudes
                   for offset in [0,21600,43200,64800,86399] for lon in [-180,180]]
    values=probe(meridian_rows)
    meridian_checks=0
    for i in range(0,len(values),2):
        utc=meridian_rows[i][1]
        a=any(start<=utc<end for start,end in zip(values[i][0::2],values[i][1::2]))
        b=any(start<=utc<end for start,end in zip(values[i+1][0::2],values[i+1][1::2]))
        assert a==b,(meridian_rows[i],values[i],values[i+1]);meridian_checks+=1

    # Inward margins can shorten or erase an interval; they cannot extend one.
    base_rows=[('W',timestamp(d),lat,lon,0,0)
               for d in dates for lat in latitudes for lon in longitudes]
    base=probe(base_rows)
    margin_rows=[('W',epoch,lat,lon,120,120) for _,epoch,lat,lon,_,_ in base_rows]
    margin=probe(margin_rows)
    for a,b in zip(base,margin):
        for j in range(0,10,2):
            if b[j]<b[j+1]:
                assert b[j]==a[j]+7200 and b[j+1]==a[j+1]-7200
            else:
                assert a[j]+7200>=a[j+1]-7200

    for timezone in ['America/New_York','Pacific/Kiritimati','Pacific/Honolulu']:
        assert probe(schedule_rows,timezone)==schedules,timezone

    return {'oracle_against_published_vectors':oracle_checks,
            'firmware_against_published_vectors':reference_checks,
            'independent_horizon_crossings_checked':len(rows)*2,
            'maximum_crossing_error_seconds':round(maximum_error,3),'worst_case':worst,
            'night_classifications_checked':classifications,
            'date_line_equivalences_checked':meridian_checks,
            'margin_windows_checked':len(base_rows)*5,
            'identical_timezone_runs':4}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--probe',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    PROBE=args.probe.resolve()
    result=run()
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'solar-review-results.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS independent solar review:', result['independent_horizon_crossings_checked'], 'crossings;', result['night_classifications_checked'], 'classifications; maximum difference', result['maximum_crossing_error_seconds'], 'seconds')
