"""Measured simultaneous echo traffic and new-file SD round trips."""
import socket
import threading
import time
from types import SimpleNamespace
from scripts.peripheral_host_test import pattern, recv_exact, run as host_test


def run_combined(ip, command, seconds, event):
    if seconds < 1800:
        raise ValueError('combined acceptance requires at least 1800 measured seconds')
    import zlib
    expected_crc=zlib.crc32(pattern(1048576))&0xffffffff
    stop=threading.Event();lock=threading.Lock()
    counts={'udp_verified':0,'tcp_verified':0,'udp_timeouts':0,'tcp_errors':0,'payload_mismatches':0}
    def increment(key):
        with lock:counts[key]+=1
    def worker(udp):
        sequence=0
        while not stop.is_set():
            data=pattern(1472,sequence&255);sequence+=1
            try:
                if udp:
                    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
                        client.settimeout(1);client.connect((ip,5001));client.sendall(data)
                        reply=client.recv(1473)
                else:
                    with socket.create_connection((ip,5002),timeout=5) as client:
                        client.settimeout(5);client.sendall(data);reply=recv_exact(client,len(data))
                increment(('udp_verified' if udp else 'tcp_verified') if reply==data else 'payload_mismatches')
            except (OSError,RuntimeError):increment('udp_timeouts' if udp else 'tcp_errors')
            stop.wait(.1 if udp else 1)
    workers=[threading.Thread(target=worker,args=(udp,),daemon=True) for udp in (True,False)]
    measurements={'roundtrips':0,'file_mismatches':0,'unrecovered_hangs':0,'firmware_roundtrip_elapsed_ms':[],
                  'file_results':[],'uploads':[]}
    started=time.monotonic()
    event('combined_started',requested_seconds=seconds)
    for thread in workers:thread.start()
    upload_arguments=SimpleNamespace(host=ip,udp_port=5001,tcp_port=5002,upload_port=5003,
                 timeout=10,sizes=[1,64,512,1472],upload_bytes=1048576,chunk_bytes=4096,seed=0x5a,
                 ping=False,no_upload=False)
    try:
        upload=host_test(upload_arguments)
        measurements['uploads'].append(upload)
        measurements.update(upload_bytes=upload['upload_bytes'],host_crc32=upload['upload_crc32'],
                            card_crc32=upload['upload_card_crc32'])
        import re
        while time.monotonic()-started < seconds:
            response=command('sd roundtrip 1048576',timeout=600)
            match=re.search(r'SD roundtrip (\S+) bytes=(\d+) crc32=([0-9a-f]+) elapsed_ms=(\d+) result=(\S+)',response)
            if (not match or match.group(5)!='PASS' or int(match.group(2))!=1048576
                    or int(match.group(3),16)!=expected_crc):
                measurements['file_mismatches']+=1
                raise RuntimeError('combined SD roundtrip did not close/reopen/verify successfully')
            measurements['roundtrips']+=1
            measurements['firmware_roundtrip_elapsed_ms'].append(int(match.group(4)))
            measurements['file_results'].append({'path':match.group(1),'bytes':int(match.group(2)),
                                                  'crc32':match.group(3),'elapsed_ms':int(match.group(4))})
            with lock:
                if counts['payload_mismatches']:raise RuntimeError('combined echo integrity mismatch')
            event('combined_progress',elapsed_seconds=time.monotonic()-started,roundtrips=measurements['roundtrips'])
        measurements['duration_seconds']=time.monotonic()-started
    except BaseException as error:
        if not measurements['uploads'] and hasattr(upload_arguments,'progress_report'):
            progress=upload_arguments.progress_report
            progress.update(status='failed',error=f'{type(error).__name__}: {error}')
            measurements['uploads'].append(progress)
        measurements['duration_seconds']=time.monotonic()-started
        event('combined_failed',measurements=measurements,counters=counts.copy())
        raise
    finally:
        stop.set()
        for thread in workers:
            thread.join(timeout=6)
            if thread.is_alive():raise RuntimeError('combined network worker did not stop; recovery refused')
    measurements.update(counts)
    measurements['simultaneous_network_packets']=counts['udp_verified']+counts['tcp_verified']
    if not counts['udp_verified'] or not counts['tcp_verified']:
        raise RuntimeError('combined workload did not verify both UDP and TCP traffic')
    event('combined_finished',measurements=measurements)
    return measurements
