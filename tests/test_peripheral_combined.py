import threading
import time
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import patch
from scripts.peripheral_combined import run_combined
from scripts.peripheral_host_test import pattern

class EchoSocket:
    def __init__(self,*args):self.data=b'';self.offset=0
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def settimeout(self,value):pass
    def connect(self,address):pass
    def sendall(self,data):self.data=data
    def recv(self,length):
        data=self.data[self.offset:self.offset+length];self.offset+=len(data);return data

class CombinedRunnerTests(unittest.TestCase):
    def test_measured_duration_and_simultaneous_echo_are_required(self):
        clock=SimpleNamespace(value=0)
        crc=f'{zlib.crc32(pattern(1048576))&0xffffffff:08x}'
        events=[]
        def command(line,timeout):
            self.assertEqual(line,'sd roundtrip 1048576')
            time.sleep(.15) # let actual worker threads verify payloads during the file operation
            clock.value+=1000
            return f'SD roundtrip 0:/T20K/RT{clock.value}.BIN bytes=1048576 crc32={crc} elapsed_ms=150 result=PASS\ngpio> '
        upload={'upload_bytes':1048576,'upload_crc32':crc,'upload_card_crc32':crc}
        with patch('scripts.peripheral_combined.time',SimpleNamespace(monotonic=lambda:clock.value)), \
             patch('scripts.peripheral_combined.socket.socket',EchoSocket), \
             patch('scripts.peripheral_combined.socket.create_connection',lambda *a,**k:EchoSocket()), \
             patch('scripts.peripheral_combined.host_test',return_value=upload):
            result=run_combined('test-peer',command,1800,lambda name,**details:events.append((name,details)))
        self.assertGreaterEqual(result['duration_seconds'],1800)
        self.assertEqual(result['roundtrips'],2)
        self.assertGreater(result['udp_verified'],0)
        self.assertGreater(result['tcp_verified'],0)
        self.assertEqual(result['payload_mismatches'],0)
        self.assertEqual(result['file_mismatches'],0)
        self.assertEqual(events[-1][0],'combined_finished')

    def test_shortened_acceptance_is_rejected(self):
        with self.assertRaises(ValueError):run_combined('test-peer',None,1799,None)

    def test_firmware_pass_label_with_wrong_crc_is_rejected(self):
        crc=f'{zlib.crc32(pattern(1048576))&0xffffffff:08x}'
        events=[]
        upload={'upload_bytes':1048576,'upload_crc32':crc,'upload_card_crc32':crc}
        with patch('scripts.peripheral_combined.socket.socket',EchoSocket), \
             patch('scripts.peripheral_combined.socket.create_connection',lambda *a,**k:EchoSocket()), \
             patch('scripts.peripheral_combined.host_test',return_value=upload):
            with self.assertRaises(RuntimeError):
                run_combined('test-peer',lambda *a,**k:'SD roundtrip x bytes=1048576 crc32=00000000 elapsed_ms=1 result=PASS',1800,lambda name,**details:events.append((name,details)))
        self.assertEqual(events[-1][0],'combined_failed')
        self.assertEqual(events[-1][1]['measurements']['file_mismatches'],1)
