"""Recorded physical card removal/reinsertion between closed-file operations."""
import re
import time
from pathlib import Path


def wait_confirmation(directory, phase, event, timeout=600):
    ready=directory/(phase+'.ready')
    ready.write_text('Files are closed; confirm this physical action in the active session.\n')
    event('card_cycle_waiting',phase=phase,control_directory=str(directory))
    deadline=time.monotonic()+timeout
    while not (directory/(phase+'.confirmed')).exists():
        if time.monotonic()>=deadline:
            raise TimeoutError('physical card '+phase+' confirmation not received')
        time.sleep(.2)


def check_card_cycle(control_directory, command, original_cid, file_result, event):
    directory=Path(control_directory)
    directory.mkdir(parents=True,exist_ok=False)
    wait_confirmation(directory,'removed',event)
    started=time.monotonic()
    absent=command('sd status',timeout=10)
    listing=command('sd ls',timeout=10)
    absent_seconds=time.monotonic()-started
    if ('SD status: unavailable' not in absent or 'SD error:' not in listing
            or absent_seconds>10):
        raise RuntimeError('missing-card handling did not return a bounded error')
    event('card_cycle_absent_verified',elapsed_seconds=absent_seconds)
    wait_confirmation(directory,'inserted',event)
    started=time.monotonic()
    present=command('sd status',timeout=10)
    cid=re.search(r'CID:([ 0-9a-f]+)',present)
    if ('SD status: ready' not in present or not cid
            or cid.group(1).strip()!=original_cid.strip()):
        raise RuntimeError('reinserted card did not initialize with the original CID')
    listing=command('sd ls',timeout=10)
    readback=command('sd read '+file_result['path'],timeout=30)
    if ('Directory ' not in listing
            or f"bytes={file_result['bytes']} crc32={file_result['crc32']}" not in readback):
        raise RuntimeError('closed test file did not survive card reinsertion')
    recovered_seconds=time.monotonic()-started
    event('card_cycle_recovered',elapsed_seconds=recovered_seconds,file=file_result)
    return {'missing_card_bounded_recovery':True,'missing_card_seconds':absent_seconds,
            'reinsert_recovery_seconds':recovered_seconds,'reinsert_cid':cid.group(1).strip()}
