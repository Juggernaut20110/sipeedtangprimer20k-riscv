import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.peripheral_card_cycle import check_card_cycle


class CardCycleTests(unittest.TestCase):
    def test_physical_confirmations_are_required_and_file_is_read_after_reinsert(self):
        with tempfile.TemporaryDirectory() as parent:
            directory=Path(parent)/'cycle';events=[];commands=[]
            def confirm(path,phase,event):
                self.assertEqual(path,directory)
                events.append(phase)
            replies=iter(['SD status: unavailable','SD error: no card',
                          'SD status: ready\nCID: aa bb\n','Directory 0:/',
                          'SD read 0:/T20K0001/RT000000.BIN bytes=512 crc32=12345678'])
            def command(line,**kwargs):
                commands.append(line);return next(replies)
            with patch('scripts.peripheral_card_cycle.wait_confirmation',side_effect=confirm):
                result=check_card_cycle(directory,command,'aa bb',
                    {'path':'0:/T20K0001/RT000000.BIN','bytes':512,'crc32':'12345678'},lambda *a,**k:None)
            self.assertEqual(events,['removed','inserted'])
            self.assertTrue(result['missing_card_bounded_recovery'])
            self.assertEqual(commands[-1],'sd read 0:/T20K0001/RT000000.BIN')

    def test_still_present_card_cannot_pass_removal(self):
        with tempfile.TemporaryDirectory() as parent:
            with patch('scripts.peripheral_card_cycle.wait_confirmation'):
                with self.assertRaisesRegex(RuntimeError,'missing-card'):
                    check_card_cycle(Path(parent)/'cycle',lambda *a,**k:'SD status: ready',
                                     'aa',{},lambda *a,**k:None)

    def test_stale_confirmation_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            with self.assertRaises(FileExistsError):
                check_card_cycle(parent,None,'aa',{},None)
