#!/usr/bin/env python3
"""Run recorded UART status and Ethernet acceptance on a DDR-qualified SRAM image."""
import argparse
import datetime
import hashlib
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from scripts import benchmark_run as serial_runner
from scripts.peripheral_config import peripheral_build_dir
from scripts.peripheral_evidence import board_identity, validate_session
from scripts.peripheral_program import program_sram, assert_hardware_available
from scripts.peripheral_run import verify_build
from scripts.peripheral_host_test import run as host_test, pattern
import zlib
from scripts.peripheral_combined import run_combined
from scripts.peripheral_card_cycle import check_card_cycle


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', required=True)
    p.add_argument('--memory',choices=['ddr3','onchip'],default='ddr3')
    p.add_argument('--sdcard', choices=['none','spi'], default='none')
    p.add_argument('--ethernet', choices=['none','rmii'], default='none')
    p.add_argument('--port', required=True)
    p.add_argument('--board-serial', required=True)
    p.add_argument('--board-revision', required=True)
    p.add_argument('--ddr-session', type=Path)
    p.add_argument('--network-tests', action='store_true')
    p.add_argument('--status-only', action='store_true',help='inspect card without creating test files')
    p.add_argument('--combined-seconds',type=int,default=1800)
    p.add_argument('--card-cycle-control-dir',type=Path,
                   help='new control directory for explicit physical removal/reinsertion acknowledgements')
    a=p.parse_args(argv)
    if a.memory=='ddr3' and not a.ddr_session:p.error('DDR applications require --ddr-session')
    if a.memory=='onchip' and (a.network_tests or a.card_cycle_control_dir):
        p.error('on-chip diagnostics cannot perform full file/network acceptance')
    if a.ddr_session:a.ddr_session=a.ddr_session.resolve()
    if a.card_cycle_control_dir and (a.sdcard!='spi' or a.status_only):
        p.error('card cycling requires full SPI file acceptance')
    board=board_identity(a.board_serial,a.board_revision)
    directory=peripheral_build_dir(ROOT,a.memory,a.profile,a.sdcard,a.ethernet)
    metadata,binary,bitstream=verify_build(directory,a.profile,a.memory,a.sdcard,a.ethernet)
    identity={'profile':a.profile,'memory':a.memory,'sdcard':a.sdcard,'ethernet':a.ethernet,
              'build_record':metadata,'artifact_hashes':{'bitstream':sha(bitstream),'firmware':sha(binary)}}
    if a.memory=='ddr3':
        checked=validate_session(a.ddr_session,identity,ROOT)
        ddr_manifest=json.loads((a.ddr_session/'manifest.json').read_text())
        if checked['criteria'].get('ddr_qualification')!='passed' or ddr_manifest['board']!=board:
            raise RuntimeError('matching complete DDR qualification and board identity are required')
    elif metadata.get('software_capability')!='diagnostic':
        raise RuntimeError('on-chip acceptance requires the explicitly reduced diagnostic firmware')
    if a.network_tests and a.ethernet!='rmii':p.error('--network-tests requires RMII')
    assert_hardware_available(a.port)
    session=ROOT/'docs/peripherals/evidence'/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ-app-'+a.profile)
    session.mkdir(parents=True)
    manifest={'schema_version':1,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'board':board,'status':'in_progress','criteria':{},'build':{
        'profile':a.profile,'memory':a.memory,'sdcard':a.sdcard,'ethernet':a.ethernet,
        'source_fingerprint':metadata['source_fingerprint'],'bitstream_sha256':sha(bitstream),'firmware_sha256':sha(binary)}}
    if a.ddr_session:manifest['ddr_qualification_session']=str(a.ddr_session.relative_to(ROOT))
    rx=session/'uart_rx.bin';tx=session/'uart_tx.bin';program=session/'programmer.log';host=session/'host.json'
    events=session/'events.jsonl'
    def event(name,**details):
        with events.open('a') as output:
            output.write(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':name,**details})+'\n')
    term=owner=None
    from litex.tools import litex_term
    old_console=litex_term.Console;old_signal=signal.getsignal(signal.SIGINT)
    def data():return rx.read_bytes()
    def wait_for(marker,offset=0,timeout=120):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if owner.error:raise RuntimeError(str(owner.error))
            capture=data()[offset:]
            if marker in capture:return capture
            time.sleep(.05)
        raise TimeoutError(f'UART did not produce {marker!r}; captured evidence retained')
    def command(line,timeout=120):
        offset=rx.stat().st_size
        event('uart_command_started',command=line)
        term.port.write((line+'\n').encode())
        result=wait_for(b'gpio> ',offset,timeout).decode(errors='replace')
        event('uart_command_completed',command=line,response_sha256=hashlib.sha256(result.encode()).hexdigest())
        print(result.strip(),flush=True)
        return result
    reports=[]
    receive=rx.open('wb',buffering=0)
    transmit=tx.open('wb',buffering=0)
    try:
        litex_term.Console=serial_runner.HeadlessConsole
        load_address=metadata['address_map']['bus_regions']['main_ram']['origin']
        term=litex_term.LiteXTerm(True,str(binary),load_address,None,False,None)
        litex_term.Console=old_console
        term.open(a.port,115200);term.port.timeout=.1;term.port.write_timeout=2
        serial_runner.clear_uart_input_buffer(term.port)
        term.port=serial_runner.RecordingPort(term.port,lambda b:owner._record(b),lambda b:transmit.write(b))
        owner=serial_runner.SerialOwner(term,receive);owner.start()
        event('sram_programming_started',bitstream_sha256=sha(bitstream))
        program_sram(bitstream,program)
        event('sram_programming_completed')
        started=wait_for(b'GPIO demo ready',timeout=120)
        marker=data().find(b'GPIO demo ready')
        wait_for(b'gpio> ',marker,timeout=30)
        if a.memory=='onchip':
            observation=command('periph status')
            expected=f'Peripheral diagnostic: profile={a.profile} SD_SPI={int(a.sdcard=="spi")} ETHERNET_RMII={int(a.ethernet=="rmii")}'
            if expected not in observation:raise RuntimeError('on-chip diagnostic feature/profile response did not match')
            if a.sdcard=='spi' and 'LiteX SPI SD controller: present' not in observation:
                raise RuntimeError('on-chip diagnostic SPI controller response missing')
            if a.ethernet=='rmii' and 'RX slots=2 TX slots=2 slot bytes=2048' not in observation:
                raise RuntimeError('on-chip diagnostic MAC slot geometry response missing')
            manifest['diagnostic_observation']={'status':'passed','response':observation,
                'capability':'controller presence and UART execution only; filesystem/network services are not linked'}
            manifest['workload_closed']=True
            manifest['status']='completed_diagnostic_observations'
            return 0
        if a.sdcard=='spi':
            status=command('sd status')
            listing=command('sd ls')
            manifest['sd_observation']={'status':status,'listing':listing,
               'writes_attempted':False}
            if not a.status_only:
                if 'SD status: ready' not in status or 'Directory ' not in listing:
                    raise RuntimeError('card initialization or supported filesystem mount/list failed; no test writes attempted')
                results=[]
                manifest['sd_observation']['writes_attempted']=True
                for size in (512,4096,1048576):
                    response=command(f'sd roundtrip {size}',timeout=600)
                    match=re.search(r'SD roundtrip (\S+) bytes=(\d+) crc32=([0-9a-f]+) elapsed_ms=(\d+) result=(\S+)',response)
                    if (not match or match.group(5)!='PASS' or int(match.group(2))!=size
                            or int(match.group(3),16)!=zlib.crc32(pattern(size))&0xffffffff):
                        raise RuntimeError('SD file roundtrip failed; incomplete file retained')
                    results.append({'path':match.group(1),'bytes':size,'crc32':match.group(3),'elapsed_ms':int(match.group(4))})
                readback=command('sd read '+results[0]['path'])
                if f"bytes=512 crc32={results[0]['crc32']}" not in readback:
                    raise RuntimeError('explicit SD read did not return the expected length/checksum')
                cid=re.search(r'CID:([ 0-9a-f]+)',status);csd=re.search(r'CSD:([ 0-9a-f]+)',status)
                sectors=re.search(r'sectors=(\d+)',status)
                clocks=re.search(r'init=(\d+) Hz transfer=(\d+) Hz',status)
                if not all((cid,csd,sectors,clocks)):raise RuntimeError('card identity/clock capture incomplete')
                manifest['criteria']['sd']={'status':'partial_missing_card_test','measurements':{
                    'cid':cid.group(1).strip(),'csd':csd.group(1).strip(),'sector_count':int(sectors.group(1)),
                    'init_hz':int(clocks.group(1)),'transfer_hz':int(clocks.group(2)),
                    'verified_file_bytes':[r['bytes'] for r in results],'integrity_mismatches':0,
                    'missing_card_bounded_recovery':False,'files':results},'evidence':['uart_rx','uart_tx']}
        if a.ethernet=='rmii':
            initial_network=command('net status')
            manifest['network_observation']={'initial_status':initial_network}
        if a.network_tests:
            identify_deadline=time.monotonic()+10
            while 'phy=identified' not in initial_network:
                if time.monotonic()>=identify_deadline:
                    raise TimeoutError('PHY identification not obtained within 10 seconds')
                time.sleep(1)
                initial_network=command('net status')
            manifest['network_observation']['identified_status']=initial_network
            if 'link=down' in initial_network:
                manifest['network_observation']['restart']=command('net restart')
            command('net dhcp')
            deadline=time.monotonic()+120
            while True:
                status=command('net status')
                match=re.search(r'IPv4=([0-9.]+) mask=([0-9.]+) gateway=([0-9.]+).*DHCP leased',status)
                if match:break
                if time.monotonic()>deadline:raise TimeoutError('DHCP lease not obtained within 120 seconds')
                time.sleep(1)
            ip,mask,gateway=match.groups()
            manifest['criteria']['dhcp']={'status':'passed','measurements':{
                'lease_obtained':True,'address':ip,'netmask':mask,'gateway':gateway},'evidence':['uart_rx','host_log']}
            def echo_tests():
                arguments=SimpleNamespace(host=ip,udp_port=5001,tcp_port=5002,upload_port=5003,
                    timeout=10,sizes=[1,64,512,1472],upload_bytes=1048576,chunk_bytes=4096,seed=0x5a,
                    ping=True,ping_program='ping',no_upload=True)
                try:return host_test(arguments)
                except BaseException as error:
                    progress=getattr(arguments,'progress_report',{'target':ip})
                    progress.update(status='failed',error=f'{type(error).__name__}: {error}')
                    reports.append(progress)
                    raise
            reports.append(echo_tests())
            # Reuse this board's leased address; no unrelated LAN address is selected.
            command(f'net static {ip} {mask} {gateway}')
            reports.append(echo_tests())
            neighbour=subprocess.run(['ip','neigh','show','to',ip],capture_output=True,text=True)
            reports[-1]['arp_neighbour']=neighbour.stdout
            if a.sdcard=='spi' and not a.status_only:
                combined=run_combined(ip,command,a.combined_seconds,event)
                reports.append({'combined':combined})
                manifest['criteria']['combined_1800s']={'status':'passed','measurements':combined,
                    'evidence':['uart_rx','uart_tx','host_log','events']}
            status=command('net status')
            phy=re.search(r' addr=\d+ id=([0-9a-f:]+)',status)
            counters=re.search(r'frames rx=(\d+) tx=(\d+) rx_queue_drop=(\d+) rx_bad=(\d+) tx_error=(\d+) hw_drop=(\d+) crc=(\d+) preamble=(\d+)',status)
            if not phy or not counters or not neighbour.stdout.strip():
                raise RuntimeError('PHY/counter/ARP measurements incomplete')
            counts=list(map(int,counters.groups()))
            manifest['criteria']['static_network']={'status':'passed','measurements':{
                'phy_id':phy.group(1),'link':bool(re.search(r'link=\S*(?:100M|10M)',status)),
                'static_address':ip,'arp':True,'ping':True,'reconnect_recovered':True,'payload_mismatches':0},
                'evidence':['uart_rx','host_log']}
            measurement=reports[1].copy();measurement.update(observed_drops=counts[2]+counts[5],rx_errors=counts[3]+counts[6]+counts[7],tx_errors=counts[4])
            manifest['criteria']['udp_tcp']={'status':'passed','measurements':measurement,'evidence':['uart_rx','host_log']}
        if a.ethernet=='rmii':
            stopped=command('net stop')
            if 'Ethernet stopped; upload file closed' not in stopped:
                raise RuntimeError('network/file shutdown was not confirmed')
            manifest['workload_closed']=True
        else:
            manifest['workload_closed']=True
        if a.card_cycle_control_dir:
            measurement=manifest['criteria']['sd']['measurements']
            measurement.update(check_card_cycle(a.card_cycle_control_dir,command,
                measurement['cid'],measurement['files'][0],event))
            manifest['criteria']['sd']['status']='passed'
        manifest['status']='completed_observations'
    except BaseException as error:
        manifest.update(status='failed',error=f'{type(error).__name__}: {error}')
        print(manifest['error'],flush=True)
        if a.memory=='onchip':manifest['workload_closed']=True
        if owner and term and a.ethernet=='rmii' and a.memory=='ddr3':
            try:
                manifest['workload_closed']='Ethernet stopped; upload file closed' in command('net stop',timeout=180)
            except BaseException as close_error:
                manifest['shutdown_error']=str(close_error)
    finally:
        if owner:owner.stop()
        if term:term.close()
        receive.close();transmit.close()
        litex_term.Console=old_console;signal.signal(signal.SIGINT,old_signal)
        manifest['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        event('session_finished',status=manifest['status'])
        host.write_text(json.dumps(reports,indent=2)+'\n')
        manifest['artifacts']=[{'name':name,'path':path.name,'bytes':path.stat().st_size,'sha256':sha(path)}
            for name,path in [('uart_rx',rx),('uart_tx',tx),('programmer_log',program),('host_log',host),('events',events)] if path.exists()]
        (session/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        print('Evidence:',session,flush=True)
    return 1 if manifest['status']=='failed' else 0

if __name__=='__main__':raise SystemExit(main())
